"""Mature own-purchase outcomes. Exact losses and conditional Saved bounds stay distinct."""
import argparse
from collections import defaultdict,Counter
import json,math,os,time,statistics
from pathlib import Path
import buyer_breakage as breakage
from market_scoring import saved_at_price,buyer_cost_reference

VERSION='buyer-cohort-v1'
WINDOW=48*3600
HORIZON=6*3600
DELIVERY_LAG=300
REFRESH=300
MAX_CACHE_AGE=900
MIN_COUNT=5


def outcome_bounds(sale,product,failure,observed):
    if failure:return (0.,0.,0.) if failure.get('refund_amount',0)>0 else (-1.,-1.,-1.)
    if sale.get('value')==0:return (-1.,-1.,-1.) if product.get('kind')=='food' else None
    price=sale.get('price');listing=product.get('list_price');kind=product.get('kind')
    if (not observed or type(sale.get('value')) not in (int,float) or not math.isfinite(sale['value']) or sale['value']<=0
        or type(price) is not int or type(listing) is not int or not 0<price<=listing):return None
    # Goods grade and acquisition cost are fixed at arrival. Food's current
    # grade can change as it ages, so its original cost may be from any grade.
    share={'A':.55,'B':.40,'C':.25}.get(sale.get('grade')) if kind=='goods' else .25 if kind=='food' else None
    if share is None:return None
    minimum_cost=math.floor(listing*share)
    low=1. if price<=minimum_cost else math.floor(((listing-price)/(listing-minimum_cost))*1000000)/1000000
    scenario=saved_at_price(listing,price,(buyer_cost_reference(product) or {}).get('unit_cost'))
    return low,1.,scenario


def build(history,catalog,notices,*,now=None,coverage=None):
    now=time.time() if now is None else now;coverage=coverage or {}
    asof=min(now,coverage.get('as_of',now));paid=breakage.paid_orders(history)
    facts={}
    for f in history:
        sale=paid.get(f.get('settled_ledger')) if f.get('event')=='verified_breakage' else None
        if (sale and f.get('source')=='public_ledger_own_paid_order_sku'
            and f.get('order')==sale.get('order') and f.get('sku')==sale.get('sku')
            and breakage.epoch(sale.get('at'))-60<=(f.get('broke_at') or breakage.epoch(f.get('at')))<=asof):
            facts[f['settled_ledger']]=f
    unmatched=defaultdict(list);notice_count=matched=0
    facts_by_name=defaultdict(list)
    for f in facts.values():
        name=(catalog.get(f['sku']) or {}).get('name') or paid[f['settled_ledger']].get('name')
        facts_by_name[name].append(f)
    seen=set()
    for x in notices:
        name=x.get('name');at=x.get('broke_at')
        if type(at) not in (int,float) or not asof-WINDOW<=at<=asof or (name,at) in seen:continue
        seen.add((name,at));notice_count+=1
        fs=[f for f in facts_by_name.get(name,[]) if abs((f.get('broke_at') or breakage.epoch(f.get('at')))-at)<2]
        if len(fs)==1:matched+=1
        else:unmatched[name].append(at)
    groups={};stats=Counter();sealed=set();prior_sealed={x for x in coverage.get('sealed_ledgers',[]) if type(x) is int}
    for ledger,sale in paid.items():
        at=breakage.epoch(sale.get('at'))
        if not asof-WINDOW<=at<=asof-HORIZON-DELIVERY_LAG:continue
        if sale.get('event')!='settled':stats['payment_without_settlement_details']+=1;continue
        sku=sale.get('sku');product=catalog.get(sku) or {};kind=product.get('kind')
        name=sale.get('shop_name') or (history.resolved(sale.get('shop'),sale.get('order')) if hasattr(history,'resolved') else None)
        if not name or kind not in ('goods','food'):stats['missing_identity_or_product']+=1;continue
        opportunity=getattr(history,'opportunities',{}).get(sale.get('order'),{})
        hours=opportunity.get('use_within_hours') if kind=='food' else None
        if kind=='food' and type(hours) not in (int,float):stats['food_horizon_unknown']+=1;continue
        failure=facts.get(ledger)
        ambiguous=any(at-1<=t<=at+HORIZON+1 for t in unmatched.get(product.get('name') or sale.get('name'),[]))
        observed=(kind=='food' or ledger in prior_sealed or bool(coverage.get('trusted') and coverage.get('since',now)<=at)) and not ambiguous
        result=outcome_bounds(sale,product,failure,observed)
        if observed and result is not None:sealed.add(ledger)
        key=(name,sku,hours)
        row=groups.setdefault(key,{'shop':name,'sku':sku,'name':product.get('name') or sku,'kind':kind,'use_within_hours':hours,
            'n':0,'unrefunded':0,'refunded':0,'unknown':0,'exact':0,'lower_sum':0.,'upper_sum':0.,'scenario_sum':0.,
            'cover_count':0,'grades':Counter(),'prices':[],'ledgers':[],'first_paid_at':at,'last_paid_at':at})
        row['n']+=1;stats['mature_paid']+=1;row['first_paid_at']=min(row['first_paid_at'],at);row['last_paid_at']=max(row['last_paid_at'],at)
        if failure or kind=='food' and sale.get('value')==0:row['refunded' if failure and failure.get('refund_amount',0)>0 else 'unrefunded']+=1
        if result is None:row['unknown']+=1;stats['unknown_outcome']+=1
        else:
            low,high,scenario=result;row['lower_sum']+=low;row['upper_sum']+=high;row['scenario_sum']+=scenario
            row['exact']+=int(low==high);stats['known_outcome']+=1
        if type(sale.get('price')) is int:row['prices'].append(sale['price'])
        row['grades'][sale.get('grade')]+=1;row['cover_count']+=int(type(sale.get('warranty_hours')) in (int,float) and sale['warranty_hours']>=6)
        row['ledgers'].append(ledger)
    rows=[]
    for row in groups.values():
        n=row['n'];unknown=row['unknown'];prices=sorted(row.pop('prices'))
        # Unknowns span the whole possible contribution interval and cannot
        # produce a selection preference. Normal transactions use bounds;
        # scenario estimates are never labelled official realized Saved.
        row['net_lower']=math.floor(((row.pop('lower_sum')-unknown)/n)*1000000)/1000000
        row['net_upper']=math.ceil(((row.pop('upper_sum')+unknown)/n)*1000000)/1000000
        row['net_scenario']=round(row.pop('scenario_sum')/n,6) if not unknown else None
        row['median_paid']=statistics.median_low(prices) if prices else None
        row['q75_paid']=prices[math.ceil(.75*len(prices))-1] if prices else None
        row['cover_share']=round(row['cover_count']/n,4)
        row['ledgers']=row['ledgers'][-8:];rows.append(row)
    return {'version':VERSION,'generated_at':now,'as_of':asof,'purchase_window':[asof-WINDOW,asof-HORIZON-DELIVERY_LAG],
        'horizon_seconds':HORIZON,'delivery_lag_seconds':DELIVERY_LAG,'coverage':coverage,
        'notice_audit':{'notices':notice_count,'matched':matched,'unmatched':notice_count-matched},
        'stats':dict(stats),'groups':rows,'sealed_ledgers':sorted(sealed),
        'contract':'Each unique own paid ledger counted once after6h+5m, by shop/SKU and exact food horizon. Verified unusable=-1, refunded=0. Normal transaction Saved is bounded by public minimum grade cost and1; category-cost scenario is an estimate. Unknown outcomes never earn priority. Absence of failure is supported by audited running kit news, not a lifetime-quality guarantee. No price or warranty guards are weakened.'}


def load(runtime,*,now=None):
    now=time.time() if now is None else now
    try:
        d=json.loads((Path(runtime)/'state/buyer-cohort.json').read_text())
        if d.get('version')!=VERSION or not 0<=now-d['generated_at']<=MAX_CACHE_AGE:return {}
        return d
    except (OSError,ValueError,KeyError,TypeError):return {}


def for_choice(cache,choice,order,baseline=None):
    try:
        hours=order.get('use_within_hours') if order.get('kind')=='food' else None
        row=next((r for r in cache.get('groups',[]) if r['shop']==choice['shop'] and r['sku']==choice['sku'] and r.get('use_within_hours')==hours),None)
        if not row:return {}
        result=dict(row);reference=(baseline or {}).get('saved');reference=reference if type(reference) in (int,float) else None
        price=(choice.get('price_guidance') or {}).get('median_paid')
        if type(price) is not int:price=row.get('median_paid')  # historical reference, never a current quote
        comparable=type(price) is int and type(row.get('q75_paid')) is int and price<=row['q75_paid']
        qualified=row['n']>=MIN_COUNT and row['unknown']==0
        better_cover=max(order.get('required_warranty_hours',0),(choice.get('risk_policy') or {}).get('required_warranty_hours',0))>=6 and row['unrefunded']>0 and row['cover_share']<1
        improved_price=type(price) is int and type(row.get('median_paid')) is int and price<row['median_paid']
        selection_estimate=((row['n']*row['net_scenario']+10*reference)/(row['n']+10) if qualified and reference is not None else None)
        result.update(selection_estimate=selection_estimate,qualified=qualified,reference_price_comparable=comparable,
            preference='positive' if qualified and comparable and reference is not None and selection_estimate>reference+.015 and row['net_upper']>=reference else 'ordinary',
            historical_below_current=bool(qualified and reference is not None and row['net_upper']<reference and not better_cover and not improved_price),
            as_of=cache.get('as_of'))
        return result
    except (KeyError,TypeError,ValueError,OverflowError):return {}


def compact(row):
    if not row:return None
    result={k:row[k] for k in ('n','unrefunded','refunded','net_scenario','median_paid','cover_share') if k in row and row[k] is not None}
    result['net_range']=[row.get('net_lower'),row.get('net_upper')]
    if row.get('unknown'):result['unknown']=row['unknown']
    return result



def refresh(runtime,*,now=None):
    from buyer_memory import read_history,PreparedHistory
    runtime=Path(runtime).resolve();now=time.time() if now is None else now
    status=json.loads((runtime/'running.json').read_text())
    stream=json.loads((runtime.parent/'seller-runtime/state/counterparty-ledger.json').read_text())
    dates=[e.get('ts',0) for e in stream.get('rows',[])]+[e.get('at',0) for e in stream.get('recent_purchases',[])]+[e.get('ts',0) for e in stream.get('buyer_breakages',[])]
    latest=max((t for t in dates if type(t) in (int,float)),default=0)
    kit=Path('/proc')/str(status['kit_pid']);alive=kit.exists()
    try:alive=alive and b'--buyer-only' in (kit/'cmdline').read_bytes() and str(runtime).encode() in (kit/'cmdline').read_bytes()
    except OSError:alive=False
    # A monotonic long-lived kit streams every broken notice. Its own news is
    # reconciled against exact public proofs; any unresolved product/time
    # interval makes the affected purchases unknown, never assigns a shop.
    trusted=alive and status.get('started_at',now)<=now-WINDOW and 0<=now-latest<=180 and stream.get('cursor')==stream.get('latest_seq')
    notices=[]
    with (runtime/'.market/broken.jsonl').open() as f:
        for line in f:
            try:notices.append(json.loads(line))
            except ValueError:continue
    history=PreparedHistory(read_history(runtime/'logs/buyer-events.jsonl',now=now,order_age_seconds=WINDOW))
    catalog=json.loads((runtime/'.market/catalog.json').read_text())
    previous=load(runtime,now=now)
    if not previous:
        try:previous=json.loads((runtime/'state/buyer-cohort.json').read_text())
        except (OSError,ValueError):previous={}
    sealed=previous.get('sealed_ledgers',[]) if previous.get('version')==VERSION else []
    report=build(history,catalog,notices,now=now,coverage={'sealed_ledgers':sealed,'trusted':trusted,'since':status.get('started_at'),
        'as_of':latest if trusted else now,'kit_pid':status['kit_pid'],'public_cursor':stream.get('cursor'),'public_latest_seq':stream.get('latest_seq'),
        'status':'audited_kit_news_and_exact_public_failure_proofs' if trusted else 'stream_not_current'})
    report['history_source']=history.reader_source
    path=runtime/'state/buyer-cohort.json';temp=path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
    temp.write_text(json.dumps(report,ensure_ascii=False,separators=(',',':')));os.replace(temp,path)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--loop',action='store_true');args=parser.parse_args()
    while True:
        try:
            r=refresh(args.runtime);print(json.dumps({'event':'buyer_cohort_refreshed','at':r['generated_at'],'coverage':r['coverage']['trusted'],'stats':r['stats'],'groups':len(r['groups']),'notice_audit':r['notice_audit']}),flush=True)
        except Exception as error:
            print(json.dumps({'event':'buyer_cohort_error','error':type(error).__name__}),flush=True)
            if not args.loop:raise
        if not args.loop:break
        time.sleep(REFRESH)
if __name__=='__main__':main()
