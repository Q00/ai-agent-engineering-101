"""Read-only live execution check; pre/post counts are not a causal experiment."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json


def main():
    runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime['started_at'];now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        def rows(kind,lo,hi):
            return [json.loads(raw) for raw, in db.execute(
                'SELECT data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts',(kind,lo,hi))]
        def kv(key):
            r=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else None
        windows={}
        for label,lo,hi in [('last_30m_before_restart',start-1800,start),('since_restart',start,now)]:
            quote=[r for r in rows('shop_fast_execution',lo,hi) if r.get('operation')=='quote']
            sales=rows('shop_settled',lo,hi)
            known=[r for r in sales if type(r.get('cost')) in (int,float)]
            windows[label]={'seconds':hi-lo,'quote_decisions':len(quote),
                'hold_decisions':sum(r.get('chosen')=='hold' for r in quote),
                'settlements':len(sales),'known_gross_margin':sum(r['price']-r['cost'] for r in known),
                'model_calls':len(rows('model_usage',lo,hi)),
                'errors':{k:len(rows(k,lo,hi)) for k in ('cycle_supervisor_error','analytical_role_error','buyer_worker_error','market_control_error')}}
        epochs={k:kv(k) for k in ('shop_strategy_revision','buyer_strategy_revision','shop_revision_epoch','buyer_revision_epoch')}
    release=read_json(ROOT/'logs/v34-release-20261117.json')
    board=read_json(ROOT/'.market/board.json',{})
    out={'at':now,'runtime':runtime,'windows':windows,'epochs':epochs,
        'source_hashes_match':all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h for f,h in release['source_hashes'].items()),
        'health':read_json(ROOT/'.market/shop-health.json',{}),
        'own_shop':next((r for r in board.get('shops',[]) if r.get('name')=='seochanit'),{}),
        'limits':['Pre/post periods differ in duration and market conditions; counts are operational evidence, not causal economic uplift.',
            'Quotes are decision events, not distinct orders or completed payments; gross margin excludes future refunds and inventory costs.']}
    path=ROOT/'logs'/('v34-live-behavior-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:out[k] for k in ('windows','epochs','source_hashes_match')},ensure_ascii=True))


if __name__=='__main__':main()
