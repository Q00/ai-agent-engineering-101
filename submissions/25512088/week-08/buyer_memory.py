"""Durable order state. Agreement is not payment; restart never repeats an order."""
import json
import os
from pathlib import Path
import sqlite3
import time


class Memory:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'buyer.sqlite3', timeout=15)
        os.chmod(self.root/'buyer.sqlite3', 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, context TEXT, state TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, order_id TEXT, shop TEXT,
          sku TEXT, task TEXT, kind TEXT, price INTEGER, details TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS payments(order_id TEXT PRIMARY KEY, shop TEXT, sku TEXT,
          price INTEGER, paid INTEGER, ledger INTEGER, reason TEXT);
        CREATE TABLE IF NOT EXISTS outcomes(order_id TEXT PRIMARY KEY, details TEXT);
        CREATE TABLE IF NOT EXISTS rival_visits(shop TEXT, sku TEXT, next_at REAL,
          pending_order TEXT, reason TEXT, PRIMARY KEY(shop,sku));
        CREATE TABLE IF NOT EXISTS rival_feedback(order_id TEXT PRIMARY KEY, sku TEXT, ledger INTEGER,
          observed_at REAL, stock_json TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS credentials(shop TEXT PRIMARY KEY, member_key TEXT, updated REAL);
        CREATE INDEX IF NOT EXISTS events_shop_kind_ts_order
          ON events(shop,kind,ts,order_id);
        CREATE INDEX IF NOT EXISTS payments_shop_sku_paid_order
          ON payments(shop,sku,paid,order_id);
        ''')

    def begin(self, order):
        safe = {k: order.get(k) for k in ('order','item','category','budget','acceptable','kind','use_within_hours','expires')}
        with self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO orders VALUES(?,?,?,?)',
                                     (order['order'], json.dumps(safe), 'running', time.time()))
        return cursor.rowcount == 1

    def state(self, order, state):
        with self.db:
            self.db.execute('UPDATE orders SET state=?,updated=? WHERE id=?', (state,time.time(),order))

    def record(self, order, shop, sku, task, kind, price=None, details=None):
        with self.db:
            self.db.execute('INSERT INTO events(order_id,shop,sku,task,kind,price,details,ts) VALUES(?,?,?,?,?,?,?,?)',
                            (order,shop,sku,task,kind,price,json.dumps(details or {},ensure_ascii=False),time.time()))

    def sync(self):
        path = self.root/'payments.jsonl'
        if not path.exists():
            return
        with self.db:
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row.get('order'), str) or type(row.get('paid')) is not bool:
                    continue
                self.db.execute('INSERT OR REPLACE INTO payments VALUES(?,?,?,?,?,?,?)',
                                (row['order'],row.get('shop'),row.get('item'),row.get('price'),int(row['paid']),row.get('ledger'),row.get('reason')))
                self.db.execute("UPDATE orders SET state=?,updated=? WHERE id=? AND state NOT IN ('paid','payment_failed')",
                                ('paid' if row['paid'] else 'payment_failed',time.time(),row['order']))

    def rival_ready(self, shop, sku, now=None):
        now=time.time() if now is None else now
        r=self.db.execute('SELECT * FROM rival_visits WHERE shop=? AND sku=?',(shop,sku)).fetchone()
        if not r:return True
        if r['pending_order']:
            outcome=self.db.execute('SELECT state FROM orders WHERE id=?',(r['pending_order'],)).fetchone()
            if not outcome or outcome[0] not in ('paid','payment_failed'):return False
        return now>=r['next_at']

    def defer_rival(self, shop, sku, reason, pending=None, seconds=60):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO rival_visits VALUES(?,?,?,?,?)',
                            (shop,sku,time.time()+seconds,pending,reason))

    def reference(self, shop, sku):
        # Only recent paid prices, for this exact SKU at this shop.
        row = self.db.execute('''SELECT p.price FROM payments p JOIN orders o ON o.id=p.order_id
          WHERE p.paid=1 AND p.shop=? AND p.sku=? AND o.updated>? ORDER BY o.updated DESC LIMIT 1''',
          (shop,sku,time.time()-86400)).fetchone()
        return row[0] if row else None

    def condition_reference(self, shop, sku, order, grade=None):
        from statistics import median
        rows=self.db.execute("""SELECT p.price,o.updated,x.details FROM payments p
          JOIN orders o ON o.id=p.order_id JOIN outcomes x ON x.order_id=p.order_id
          WHERE p.paid=1 AND p.shop=? AND p.sku=? AND o.updated>?
          ORDER BY o.updated DESC LIMIT 30""",(shop,sku,time.time()-21600)).fetchall()
        prices=[]
        for row in rows:
            info=json.loads(row['details'])
            if grade and info.get('grade')!=grade:continue
            if order.get('kind')=='food':
                expiry=info.get('expires_at')
                if not isinstance(expiry,(int,float)) or (expiry-row['updated'])/3600<order.get('use_within_hours',0):continue
            elif (info.get('warranty_hours') or 0)<6:continue
            prices.append(row['price'])
        return int(median(prices)) if prices else None

    def deal_estimate(self, shop):
        # Beta(2,2) smoothing; only offers whose order has resolved count.
        # Unselected quote-only shops and uncertain payments are not failures.
        rows=self.db.execute("""SELECT DISTINCT e.order_id,p.paid,p.shop,o.state
          FROM events e JOIN orders o ON o.id=e.order_id
          LEFT JOIN payments p ON p.order_id=e.order_id
          WHERE e.shop=? AND e.kind='offer' AND e.ts>? AND
          o.state IN ('paid','payment_failed','no_deal')""",(shop,time.time()-21600)).fetchall()
        won=sum(r['paid']==1 and r['shop']==shop for r in rows)
        return {'probability':(won+2)/(len(rows)+4),'trials':len(rows),'wins':won}

    def saved_portfolio(self, catalog):
        """Estimate Patch-3.6 Saved with published grade cost midpoints.

        Unit cost is hidden from buyers; this is diagnostic only and never
        relaxes the live price cap.
        """
        rows=self.db.execute('''SELECT p.sku,p.price,x.details FROM payments p
          JOIN outcomes x ON x.order_id=p.order_id WHERE p.paid=1''').fetchall()
        total=0.0;deals=0
        for row in rows:
            product=catalog.get(row['sku'],{})
            listed=product.get('list_price');price=row['price']
            if not isinstance(listed,(int,float)) or listed<=0 or not isinstance(price,(int,float)):continue
            try:grade=json.loads(row['details']).get('grade')
            except (TypeError,ValueError):grade=None
            cost_ratio={'A':.65,'B':.50,'C':.35}.get(grade,.50)
            total+=max(-1.0,min(1.0,(listed-price)/(listed*(1-cost_ratio))));deals+=1
        return {'deals':deals,'estimated_margin_saved_sum':total,
            'estimated_saved':total/(deals+100) if deals or total else 0.0,
            'basis':'patch3.6-grade-cost-midpoint'}

    def remember_key(self, shop, key):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO credentials VALUES(?,?,?)', (shop,key,time.time()))

    def key(self, shop):
        row = self.db.execute('SELECT member_key FROM credentials WHERE shop=?', (shop,)).fetchone()
        return row[0] if row else ''

    def close(self):
        self.db.close()
