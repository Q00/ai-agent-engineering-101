"""Check overnight stock flow, cohort ambiguity, ratings and controller actions."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
import json,re,sqlite3
from r3.state import ROOT,read_json,atomic_json
from review_progress_20261110 import KST,OLD,NEW,own


def main():
    old,new=[read_json(ROOT/p) for p in (OLD,NEW)]
    start,end=[r['responses']['/api/board']['finished_at'] for r in (old,new)]
    runtime=read_json(ROOT/'.market/runtime.json');watch=read_json(ROOT/'.market/round3-watch.json')
    day=datetime.fromtimestamp(runtime['started_at'],KST).date();previous=None;supplies=[];repairs=[];errors=Counter()
    for line in open(watch['market_log'],encoding='utf-8',errors='replace'):
        mt=re.match(r'^(\d\d:\d\d:\d\d) ',line)
        if not mt:continue
        clock=datetime.strptime(mt[1],'%H:%M:%S').time()
        sec=clock.hour*3600+clock.minute*60+clock.second
        if previous is not None and previous-sec>43200:day+=timedelta(days=1)
        previous=sec
        at=datetime.combine(day,clock,KST).timestamp()
        if not start<at<=end:continue
        m=re.search(r'\[supply\] (\d+) x (.*) for (\d+), balance (\d+)',line)
        if m:supplies.append({'at':at,'qty':int(m[1]),'cost':int(m[3])})
        if 'rejoin' in line.lower() or 'repair' in line.lower():repairs.append({'at':at,'line':line.strip()[:350]})
        if '[market] order failed:' in line:errors[line.split('[market] order failed:',1)[1].strip()[:100]]+=1
    boards=[r['responses']['/api/board']['data'] for r in (old,new)]
    before,after=[own(b,'shops') for b in boards]
    result={'stock_flow':{'before':before['stock_units'],'after':after['stock_units'],
        'successful_supply_calls':len(supplies),'supply_units':sum(r['qty'] for r in supplies),
        'supply_cost':sum(r['cost'] for r in supplies)},'repair_lines':repairs,'market_order_errors':dict(errors),
        'shop_public_component_fields':{k:v for k,v in after.items() if k not in ('card','reviews','stock')},'reviews':{}}
    for label,source in [('before',old),('after',new)]:
        shop=next(r for r in source['responses']['/api/shops']['data']['shops'] if r['name']=='seochanit')
        result['reviews'][label]=shop.get('reviews',[])
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        def ev(kind,t0=start):return [dict(json.loads(d),_ts=t) for t,d in c.execute('SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts',(kind,t0,end))]
        acts=ev('strategy_change')
        result['strategy_actions']=[{k:r.get(k) for k in ('_ts','action','reason','observed','evidence','after')} for r in acts]
        sales=ev('shop_settled');result['stock_flow']['local_sales']=len(sales)
        result['stock_flow']['residual']=before['stock_units']+sum(r['qty'] for r in supplies)-len(sales)-after['stock_units']
        purchases=[r for r in ev('purchase') if r.get('kind')=='goods' and r.get('paid_at',r['_ts'])+21600<=end]
        outcomes=defaultdict(list)
        for r in ev('outcome'):outcomes[r['order']].append(r)
        ambiguous={oid for r in ev('ambiguous_break',0) for oid in r.get('orders',[])}
        publicbreaks={r['unit']:r for r in ev('public_broken',0)}
        result['mature_goods_details']=[{'order':p['order'],'unit':p.get('unit'),'price':p['price'],'value':p['value'],
            'initial_saved':p['initial_saved_contribution'],'warranty_hours':p.get('warranty_hours'),
            'outcomes':outcomes[p['order']],'ambiguous':p['order'] in ambiguous,
            'public_break':publicbreaks.get(p.get('unit'))} for p in purchases]
    stamp=datetime.now().strftime('%Y%m%dT%H%M%S%f')
    path=ROOT/'logs'/f'overnight-details-{stamp}.json';atomic_json(path,result)
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k not in ('reviews','mature_goods_details')},ensure_ascii=False))
    print('REVIEWS',json.dumps(result['reviews'],ensure_ascii=False))
    print('GOODS',json.dumps([r for r in result['mature_goods_details'] if r['ambiguous'] or any(o.get('broken') for o in r['outcomes'])],ensure_ascii=False))


if __name__=='__main__':main()
