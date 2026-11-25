"""Read-only proof of procurement approval and actual supplier execution."""
from collections import Counter
from datetime import datetime, timezone
import json
import sqlite3
import time
from r3.state import ROOT, read_json


def main():
    runtime=read_json(ROOT/'.market/runtime.json',{})
    start=runtime.get('started_at',time.time())
    now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None
        kinds=('supply_execution_status','restock_decision','restock_blocked','supply_blocked',
               'cycle_supply_error','shop_prepared_plan_used','shop_settled','model_usage')
        events={kind:[dict(json.loads(raw),event_at=ts) for ts,raw in db.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts',
            (kind,start,now))] for kind in kinds}
        approval=kv('company-procurement:v30')
        control=kv('market_control')
        feed=kv('public_feed_caught_up_at')
    inv=read_json(ROOT/'.market/inventory.json',{})
    complete=[r for r in events['supply_execution_status'] if r.get('status')=='complete']
    board=read_json(ROOT/'.market/board.json',{})
    out={'at':now,'runtime':runtime,'since':start,
        'completed_supply_lines':len(complete),
        'completed_supply_units':sum(r.get('qty',0) for r in complete),
        'completed_supply_by_sku':dict(Counter({s:sum(r.get('qty',0) for r in complete if r.get('sku')==s)
            for s in {r.get('sku') for r in complete}})),
        'event_counts':{k:len(v) for k,v in events.items()},
        'execution_evidence':{k:v[-15:] for k,v in events.items() if k not in ('shop_settled','model_usage')},
        'procurement':approval,'market_control':control,
        'feed_age_seconds':now-feed if type(feed) in (int,float) else None,
        'inventory':{'balance':inv.get('balance'),'units':len(inv.get('units',[])),
            'restock':inv.get('restock')},
        'shop':next((r for r in board.get('shops',[]) if r.get('name')=='seochanit'),{}),
        'limitations':['Completed supply is an executed purchase, not proof of subsequent profit or score uplift.']}
    path=ROOT/'logs'/('v33-procurement-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:out[k] for k in ('completed_supply_lines','completed_supply_units',
        'event_counts','feed_age_seconds','inventory')},ensure_ascii=True))


if __name__=='__main__':
    main()
