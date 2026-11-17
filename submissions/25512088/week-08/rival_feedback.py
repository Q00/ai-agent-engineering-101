"""Refresh public competitor stock after confirmed payment; failures stay pending."""
import json
import time
import httpx


def refresh_feedback(memory, market='https://market.ourolabs.ai', fetch=None):
    rows=memory.db.execute('''SELECT o.order_id,o.details FROM outcomes o
       LEFT JOIN rival_feedback f ON f.order_id=o.order_id
       WHERE f.order_id IS NULL OR f.error IS NOT NULL LIMIT 20''').fetchall()
    if not rows:return
    try:
        if fetch is None:
            with httpx.Client(timeout=5) as client:
                response=client.get(market+'/api/shops');response.raise_for_status();shops=response.json()['shops']
        else:shops=fetch()
        error=None
    except Exception as exc:shops=[];error=type(exc).__name__
    for row in rows:
        settled=json.loads(row['details']);sku=settled.get('sku') or settled.get('item')
        if not sku:
            payment=memory.db.execute('SELECT sku FROM payments WHERE order_id=?',(row['order_id'],)).fetchone()
            sku=payment[0] if payment else None
        stock=[dict(shop=s['name'],url=s['url'],qty=s.get('stock',{}).get(sku,0)) for s in shops
               if s.get('name')!='25512088' and s.get('stock',{}).get(sku,0)>0]
        with memory.db:
            memory.db.execute('INSERT OR REPLACE INTO rival_feedback VALUES(?,?,?,?,?,?)',
               (row['order_id'],sku,settled.get('ledger'),time.time(),json.dumps(stock),error))
