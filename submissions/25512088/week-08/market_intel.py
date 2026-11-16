"""Bounded public-data research, shared by buyer and seller. No text execution."""
import json
import os
import sqlite3
import time
import uuid
from functools import lru_cache
from pathlib import Path
from statistics import median
import httpx

WINDOW = 6 * 3600
BUYER_SCORE_WEIGHTS={'saved':.50,'forum':.30,'deals':.10,'posts':.10}
SHOP_SCORE_WEIGHTS={'book':.50,'revenue':.30,'stars':.20}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


@lru_cache(maxsize=16)
def _read_json_snapshot(path, inode, mtime_ns, size):
    """Read an atomically replaced JSON snapshot only once per version."""
    return json.loads(Path(path).read_text())


def read_json_snapshot(path, default):
    """Return cached immutable-by-convention JSON until the file changes."""
    try:
        path = Path(path)
        stat = path.stat()
        return _read_json_snapshot(str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return default


class Intel:
    def __init__(self, state):
        self.root = Path(state)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'market_intel.sqlite3', timeout=5)
        os.chmod(self.root/'market_intel.sqlite3',0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS trades(seq INTEGER PRIMARY KEY, ts REAL, shop TEXT,
          sku TEXT, grade TEXT, hours REAL, warranty REAL, price INTEGER, honest INTEGER,
          buyer TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS breakages(seq INTEGER PRIMARY KEY, ts REAL, shop TEXT,
          unit TEXT, sku TEXT, refunded INTEGER, warranty REAL, hours_after_sale REAL);
        CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY, ts REAL, body TEXT);
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, ts REAL, shop TEXT,
          sku TEXT, grade TEXT, hours REAL, warranty REAL, price INTEGER, kind TEXT);
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, ts REAL, role TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS evolution_events(
          id INTEGER PRIMARY KEY, ts REAL, correlation TEXT, role TEXT,
          phase TEXT, rule_version TEXT, body TEXT);
        CREATE INDEX IF NOT EXISTS trades_shop_ts_honest
          ON trades(shop,ts,honest);
        CREATE INDEX IF NOT EXISTS trades_sku_grade_ts
          ON trades(sku,grade,ts);
        CREATE INDEX IF NOT EXISTS trades_grade_ts
          ON trades(grade,ts);
        CREATE INDEX IF NOT EXISTS breakages_shop_ts
          ON breakages(shop,ts);
        CREATE INDEX IF NOT EXISTS observations_shop_ts_kind
          ON observations(shop,ts,kind);
        CREATE INDEX IF NOT EXISTS decisions_role_ts
          ON decisions(role,ts);
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(trades)')}
        if 'buyer' not in columns:
            with self.db:self.db.execute("ALTER TABLE trades ADD COLUMN buyer TEXT DEFAULT ''")

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key,json.dumps(value)))

    def source(self, name, default=None, max_age=900):
        r = self.db.execute('SELECT ts,body FROM sources WHERE name=?',(name,)).fetchone()
        return json.loads(r['body']) if r and time.time()-r['ts'] < max_age else default

    def save_source(self, name, body):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?)',(name,time.time(),json.dumps(body)))

    def ingest(self, rows):
        refunded_units=[]
        with self.db:
            for r in rows:
                if r.get('round') != 3:
                    continue
                d = r.get('data', {})
                if r.get('kind') == 'broken':
                    unit=d.get('unit');refunded=d.get('refunded');sku=d.get('sku',d.get('item'))
                    if (type(r.get('seq')) is not int or not isinstance(unit,str) or not unit or
                            type(refunded) is not int or refunded<=0):
                        continue
                    self.db.execute('''INSERT OR IGNORE INTO breakages
                        (seq,ts,shop,unit,sku,refunded,warranty,hours_after_sale)
                        VALUES(?,?,?,?,?,?,?,?)''',
                        (r['seq'],r.get('ts'),d.get('shop',''),unit,
                         sku if isinstance(sku,str) else '',refunded,
                         d.get('warranty_hours') or 0,d.get('hours_after_sale')))
                    refunded_units.append(unit)
                    continue
                if r.get('kind') != 'settle':
                    continue
                price=d.get('amount'); grade=d.get('grade'); sku=d.get('sku',d.get('item'))
                if type(price) is not int or price<=0 or grade not in ('A','B','C') or not isinstance(sku,str):
                    continue
                expiry=d.get('expires_at')
                hours=max(0,(expiry-r['ts'])/3600) if isinstance(expiry,(int,float)) else None
                self.db.execute('''INSERT OR IGNORE INTO trades
                    (seq,ts,shop,sku,grade,hours,warranty,price,honest,buyer)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (r['seq'],r['ts'],d.get('shop',''),sku,grade,hours,d.get('warranty_hours') or 0,
                     price,int(d.get('claim_honest') is True),d.get('buyer','')))
        self.reconcile_refunds(refunded_units)

    def reconcile_refunds(self, units):
        """Apply official broken events to our local sale evidence by unit id.

        Unit ids are globally unique market artifacts.  Updating only an
        already-present local unit means a rival's public refund can never
        affect our evidence.  The operation is idempotent so replaying a
        ledger page after a restart is safe.
        """
        units={unit for unit in units if isinstance(unit,str) and unit}
        path=self.root/'referrals.sqlite3'
        if not units or not path.exists():return 0
        with sqlite3.connect(path,timeout=5) as referrals:
            if not referrals.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales'").fetchone():
                return 0
            changed=0
            for unit in units:
                cur=referrals.execute(
                    'UPDATE sales SET refunded=1 WHERE unit=? AND refunded=0',(unit,))
                changed+=cur.rowcount
        return changed

    def refresh(self, market, client=None):
        if time.time()-self.get('last_attempt',0)<60:
            return
        self.put('last_attempt',time.time())
        own_client=client is None
        client=client or httpx.Client(timeout=4,follow_redirects=False)
        errors=[]
        try:
            cursor=self.get('cursor',-500)
            # At most 1000 records/run; never reset cursor and silently skip backlog.
            for _ in range(2):
                try:
                    r=client.get(market+'/api/ledger',params={'after':cursor},timeout=4);r.raise_for_status()
                    rows=r.json()
                    if not isinstance(rows,list):raise ValueError('ledger schema')
                    if any(type(x.get('seq')) is not int for x in rows):raise ValueError('ledger seq')
                    self.ingest(rows)
                    if rows:
                        cursor=max(x['seq'] for x in rows);self.put('cursor',cursor)
                        self.put('ledger_through',max(x['ts'] for x in rows))
                    self.put('backlog',len(rows)>=500)
                    if len(rows)<500:break
                except Exception as exc:
                    errors.append('ledger:'+type(exc).__name__);break
            for name in ('shops','board'):
                try:
                    r=client.get(market+'/api/'+name,timeout=4);r.raise_for_status()
                    data=r.json()
                    if name=='shops' and isinstance(data,list):data={'shops':data}
                    if not isinstance(data,dict):raise ValueError('source schema')
                    self.save_source(name,data)
                except Exception as exc:errors.append(name+':'+type(exc).__name__)
            for name in ('forum','demand'):
                path=self.root/(name+'.json')
                if path.exists() and time.time()-path.stat().st_mtime<120:
                    self.save_source(name,read_json(path,[]))
            self.put('errors',errors)
            self.write_report()
        finally:
            if own_client:client.close()

    def comparable(self, sku, grade, hours=None, warranty=0, seconds=WINDOW):
        rows=self.db.execute('SELECT * FROM trades WHERE sku=? AND grade=? AND ts>?',
                             (sku,grade,time.time()-max(1,float(seconds)))).fetchall()
        prices=[];by_buyer={}
        for r in rows:
            if hours is None:
                if r['hours'] is not None or bool(r['warranty']) != bool(warranty):continue
            elif r['hours'] is None or abs(r['hours']-hours)>max(6,hours*.25):continue
            prices.append(r['price'])
            if r['buyer']:
                by_buyer.setdefault(r['buyer'],[]).append(r['price'])
        prices.sort()
        buyer_prices=sorted(int(median(values)) for values in by_buyer.values())
        return {'count':len(prices),'median':int(median(prices)) if len(prices)>=3 else None,
                'buyers':len(buyer_prices),
                'buyer_median':int(median(buyer_prices)) if len(buyer_prices)>=3 else None,
                # One representative price per public buyer prevents a prolific
                # account from dominating the acceptance threshold.
                'p10':buyer_prices[int(.10*(len(buyer_prices)-1))] if len(buyer_prices)>=5 else None}

    def reliability(self, url):
        rows=self.db.execute('SELECT honest FROM trades WHERE shop=? AND ts>?',(url,time.time()-WINDOW)).fetchall()
        honesty=(sum(r[0] for r in rows)+2)/(len(rows)+2)
        source=self.source('shops',{}) or {}
        shops=source.get('shops',[]) if isinstance(source,dict) else source
        shop=next((s for s in shops if s.get('url')==url),{})
        reviews=[r['stars'] for r in shop.get('reviews',[]) if r.get('round')==3 and type(r.get('stars')) in (int,float)]
        stars=sum(reviews)/len(reviews) if reviews else 3
        board=self.source('board',{}) or {}
        registered=next((r for r in board.get('shops',[]) if r.get('name')==shop.get('name')),None)
        if registered:
            if isinstance(registered.get('stars'),(int,float)):stars=registered['stars']
            if registered.get('alive') is False:return .01
        bad=self.db.execute("SELECT COUNT(*) FROM observations WHERE shop=? AND ts>? AND kind IN ('unavailable','condition_rejected')",(url,time.time()-600)).fetchone()[0]
        return honesty*(stars/5)/(1+bad)

    def observe(self, shop, sku, kind, price=None, grade=None, hours=None, warranty=None):
        with self.db:
            self.db.execute('INSERT INTO observations(ts,shop,sku,grade,hours,warranty,price,kind) VALUES(?,?,?,?,?,?,?,?)',
                            (time.time(),shop,sku,grade,hours,warranty,price,kind))

    def decision(self, role, body):
        with self.db:
            self.db.execute('INSERT INTO decisions(ts,role,body) VALUES(?,?,?)',(time.time(),role,json.dumps(body)))

    def evolution(self, role, phase, body, correlation=None, rule_version='round3-patch3.5'):
        """Append a bounded structured event. Callers must pass summaries, never raw prompts/secrets."""
        correlation=correlation or str(uuid.uuid4())
        with self.db:
            self.db.execute('''INSERT INTO evolution_events
                (ts,correlation,role,phase,rule_version,body) VALUES(?,?,?,?,?,?)''',
                (time.time(),correlation,role,phase,rule_version,json.dumps(body,ensure_ascii=False)))
        return correlation

    def write_report(self):
        board=self.source('board',{}) or {}
        p=read_json(Path(__file__).with_name('referral_policy.json'),{})
        mine={k:[r for r in board.get(k,[]) if r.get('name')==p.get('shop_name') or
                 (r.get('owner') or {}).get('login')=='morethanwhatiwant'] for k in ('shops','buyers','players')}
        forum=self.source('forum',[]) or []
        if isinstance(forum,dict):forum=forum.get('posts',[])
        report={'at':time.time(),'ledger_through':self.get('ledger_through'), 'backlog':self.get('backlog'),
                'errors':self.get('errors',[]),'board':mine,'forum_posts_observed':len(forum),
                'own_forum':[{'id':r.get('id'),'role':r.get('role'),'likes':r.get('likes'),'dislikes':r.get('dislikes')}
                             for r in forum if r.get('author')==p.get('shop_name')],
                'top_observed_skus':[dict(r) for r in self.db.execute('SELECT sku,COUNT(*) sales,COUNT(DISTINCT shop) shops FROM trades WHERE ts>? GROUP BY sku ORDER BY sales DESC LIMIT 10',(time.time()-WINDOW,))],
                'recent_buyer_observations':[dict(r) for r in self.db.execute('SELECT sku,kind,COUNT(*) n FROM observations WHERE ts>? GROUP BY sku,kind',(time.time()-3600,))],
                'seller_funnel':[dict(r) for r in self.db.execute('SELECT role,COUNT(*) n FROM decisions WHERE ts>? GROUP BY role',(time.time()-3600,))],
                'evolution_counts':[dict(r) for r in self.db.execute(
                    'SELECT role,phase,COUNT(*) n FROM evolution_events WHERE ts>? GROUP BY role,phase',
                    (time.time()-86400,))],
                'coverage':'Observed ledger sample, not complete market history. Forum text is unverified.'}
        report['buyer_score_weights']=BUYER_SCORE_WEIGHTS
        report['shop_score_weights']=SHOP_SCORE_WEIGHTS
        report['buyer_score_rule_status']='configured from live Round-3 rule; endpoint reachability recorded separately'
        deficits=[]
        for role,fields in [('shops',('book','revenue','stars')),('buyers',('saved','forum','deals','posts'))]:
            for me in mine[role]:
                for field in fields:
                    values=[r[field] for r in board.get(role,[]) if isinstance(r.get(field),(int,float))]
                    value=me.get(field)
                    if values and isinstance(value,(int,float)):
                        percentile=sum(v<=value for v in values)/len(values)
                        deficits.append({'role':role,'metric':field,'value':value,'observed_percentile':round(percentile,3)})
        report['score_gaps']=sorted(deficits,key=lambda d:d['observed_percentile'])
        report['minimum_deals']={k: max(0,20-(mine[k][0].get('deals',0) if mine[k] else 0)) for k in ('shops','buyers')}
        path=self.root/'market-report.json';tmp=path.with_suffix('.'+str(time.time_ns())+'.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));tmp.replace(path)
