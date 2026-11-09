"""Save actual post-restart trades and successful supply console lines, read only."""
from datetime import datetime,timezone
import json
import re
import sqlite3
import time
from r3.state import ROOT,read_json,policy


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json')
    start=runtime['started_at'];watch=read_json(ROOT/'.market/round3-watch.json')
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        def kv(key):
            row=c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None
        def events(kind):
            return [dict(json.loads(d),_ts=t) for t,d in c.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts',(kind,start,now))]
        roles={role:kv('adaptive:'+role) for role in ['shop','buyer']}
        sales=[r for r in events('shop_settled') if r['strategy_version']==roles['shop']['version']]
        purchases=[r for r in events('purchase') if r['strategy_version']==roles['buyer']['version']]
        plans=events('restock_decision')
        supplies=[]
        console=watch.get('market_log')
        if console:
            for line in open(console,encoding='utf-8',errors='replace'):
                match=re.search(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.*) for (\d+), balance (\d+)',line)
                if match:supplies.append({'time_kst':match[1],'qty':int(match[2]),'name':match[3],
                                          'cost':int(match[4]),'balance_after':int(match[5])})
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'runtime':runtime,
                'minutes_since_restart':(now-start)/60,'policy':policy(),
                'revisions':{role:kv(role+'_strategy_revision') for role in ['shop','buyer']},
                'strategies':roles,'health':read_json(ROOT/'.market/shop-health.json'),
                'control':kv('market_control'),'progress':kv('championship_progress'),
                'shop_quality_cohorts':kv('shop_quality_cohorts_v9'),
                'own_sales':{'count':len(sales),'revenue':sum(r['price'] for r in sales),
                             'gross_margin_before_later_costs':sum(r['price']-r['cost'] for r in sales),
                             'below_cost_count':sum(r['price']<r['cost'] for r in sales),'events':sales},
                'buyer_purchases':{'count':len(purchases),'events':purchases},
                'restock_plans':plans,'successful_supplies':supplies,
                'actual_supply_units':sum(r['qty'] for r in supplies),
                'actual_supply_cost':sum(r['cost'] for r in supplies),
                'forum_deliveries':events('forum_delivery'),
                'post_restart_errors':{k:events(k) for k in ['market_control_error','forum_error','strategy_error']},
                'limits':['Short post-release observation, not proof of improved ranks or durable profit.',
                          'Supply success is parsed only from this launch console; plans are separate.',
                          'Gross margin excludes subsequent refunds, storage and expiration.']}
    path=ROOT/'logs'/('championship-live-evidence-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print(json.dumps({'at':result['at'],'revisions':result['revisions'],'sales':len(sales),
                      'purchases':len(purchases),'supply_units':result['actual_supply_units'],
                      'supply_cost':result['actual_supply_cost'],'health':result['health'],
                      'control':result['control'].get('status'),
                      'errors':{k:len(v) for k,v in result['post_restart_errors'].items()}},ensure_ascii=True))


if __name__=='__main__':main()
