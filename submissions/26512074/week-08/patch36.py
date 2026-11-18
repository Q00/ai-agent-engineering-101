"""Margin-share purchasing, public cost estimates and mature failure cohorts."""
import math
import time
from collections import defaultdict
from pathlib import Path
from optimizer import atomic,read

ROOT=Path(__file__).resolve().parent
_cache={}
_documents={}


def document(path):
    path=Path(path)
    try:stamp=path.stat().st_mtime_ns
    except FileNotFoundError:return {}
    key=str(path.absolute());old=_documents.get(key)
    if old is None or old[0]!=stamp:_documents[key]=(stamp,read(path,{}))
    return _documents[key][1]


def market(md=None):
    path=(Path(md) if md else ROOT/'.market')/'patch36-market.json'
    try:stamp=path.stat().st_mtime_ns
    except FileNotFoundError:return {}
    key=str(path.absolute());cached=_cache.get(key)
    if cached is None or cached[0]!=stamp:
        cached=(stamp,read(path,{}));_cache[key]=cached
    return cached[1] if 0<=time.time()-cached[1].get('at',0)<86400 else {}


def cost_estimate(value,sku=None,grade=None,md=None):
    data=market(md);cohort=data.get('costs',{}).get(f'{sku}:{grade}',{})
    prior={'A':.65,'B':.50,'C':.35}.get(grade,.61)
    kind=data.get('cost_kinds',{}).get(sku,'goods')
    prior=data.get('cost_priors',{}).get(f'{kind}:{grade}',{}).get('ratio',prior)
    n=cohort.get('n',0)
    ratio=(cohort.get('ratio',prior)*n+prior*2)/(n+2)
    return max(0,min(value*.99,value*ratio))


def margin_share(value,price,cost):
    if price<cost:return 1
    return min(1,(value-price)/max(1,value-cost))


def expected_margin_share(value,price,sku=None,grade=None,md=None):
    data=market(md);local=data.get('costs',{}).get(f'{sku}:{grade}',{})
    kind=data.get('cost_kinds',{}).get(sku,'goods')
    prior=data.get('cost_priors',{}).get(f'{kind}:{grade}',{})
    if not local.get('samples') and not prior.get('samples'):
        return margin_share(value,price,cost_estimate(value,sku,grade,md))
    n=local.get('n',0)
    ls=local.get('samples') or [local.get('ratio',prior.get('ratio',.61))]
    ps=prior.get('samples') or [prior.get('ratio',{'A':.65,'B':.5,'C':.35}.get(grade,.61))]
    def mean_score(samples):return sum(margin_share(value,price,value*max(0,r)) for r in samples)/len(samples)
    # Average capped scores, not average costs: E[min(1,q(C))] differs from
    # min(1,q(E[C])). Two shared observations shrink sparse SKU estimates.
    return (n*mean_score(ls)+2*mean_score(ps))/(n+2)


def warranty_bucket(hours):return '6plus' if hours>=6 else 'partial' if hours>0 else 'none'


def calibrated_probabilities(md,kind,grade,normal,refunded,failed,warranty=0):
    calibration=document((Path(md) if md else ROOT/'.market')/'buyer-cohort-calibration.json')
    observed=calibration.get('final_outcomes',{}).get(f'{kind}:{grade}',{})
    from strategy_policy import load
    if kind=='goods' and load(Path(md) if md else ROOT/'.market').get('contract_risk_calibration_enabled'):
        # Never transfer refund/failure frequencies from uncovered purchases
        # into a six-hour covered contract (or in the opposite direction).
        observed=calibration.get('contract_outcomes',{}).get(f'{kind}:{grade}:{warranty_bucket(warranty)}',{})
    if 0<=time.time()-calibration.get('at',0)<86400 and observed.get('complete') and observed.get('n',0)>0:
        n=observed['n'];normal=(observed['normal']+20*normal)/(n+20);refunded=(observed['refunded']+20*refunded)/(n+20);failed=(observed['failed']+20*failed)/(n+20)
    return normal,refunded,failed


def outcome_components(value,price,sku=None,grade=None,kind='goods',warranty=0,url=None,md=None):
    data=market(md)
    q=expected_margin_share(value,price,sku,grade,md)
    if kind=='food':
        normal,refunded,failed=calibrated_probabilities(md,kind,grade,1.,0.,0.)
        return dict(raw_score=q,normal=normal,refunded=refunded,failed=failed,expected_score=normal*q-failed,outcome_source='food_final_use_only_when_observed')
    prior={'A':.043,'B':.111,'C':.371}.get(grade,.10)
    cohort=data.get('risk',{}).get(f'{sku}:{grade}') or data.get('risk',{}).get(f'*:{grade}',{})
    n=cohort.get('n',0);b=(cohort.get('broken',0)+prior*20)/(n+20)
    coverage=0
    if warranty>0:
        seller=data.get('coverage',{}).get(url,{})
        coverage=(seller.get('refunded',0)+.9*5)/(seller.get('broken',0)+5)
        coverage*=min(1,warranty/6)
    # A broken unit is a failure-risk proxy; final market use outcomes can differ.
    normal=1-b;refunded=b*min(1,max(0,coverage));failed=b-refunded
    normal,refunded,failed=calibrated_probabilities(md,kind,grade,normal,refunded,failed,warranty)
    return dict(raw_score=q,normal=normal,refunded=refunded,failed=failed,expected_score=normal*q-failed,outcome_source='broken_proxy_not_final_use')


def outcome(value,price,sku=None,grade=None,kind='goods',warranty=0,url=None,md=None):
    result=outcome_components(value,price,sku,grade,kind,warranty,url,md)
    calibration=document((Path(md) if md else ROOT/'.market')/'buyer-cohort-calibration.json')
    correction=calibration.get('price_bias',{}).get(f'{kind}:{grade}',{}).get('correction',0) if 0<=time.time()-calibration.get('at',0)<86400 else 0
    return min(1,result['expected_score']+result['normal']*correction)


def saved_objective(share,reliability,md=None):
    state=document((Path(md) if md else ROOT/'.market')/'patch36-scoreboard.json')
    saved=state.get('buyer',{}).get('saved',0) if 0<=time.time()-state.get('at',0)<25200 else 0
    from score_coordination import all_value
    directory=Path(md) if md else ROOT/'.market'
    targets=document(directory/'score-targets.json')
    buyer=state.get('buyer',{});deals=buyer.get('deals',0)
    modeled=all_value(targets,'buyer',{'saved':(share-saved)/max(1,deals+101),'deals':1},time.time())
    return reliability*modeled if modeled is not None else reliability*(share-saved)


def ceiling(value,budget,warranty,reliability,url,sku=None,grade=None,kind='goods',md=None,minimum_score=None):
    # Spend authority is absolute; the score baseline adapts instead of old 70/85% caps.
    state=document((Path(md) if md else ROOT/'.market')/'patch36-scoreboard.json')
    saved=state.get('buyer',{}).get('saved',.5794)
    target=max(0,saved+.01) if minimum_score is None else max(0,minimum_score)
    lo,hi=0,min(math.floor(budget),math.floor(value))
    for _ in range(22):
        mid=(lo+hi)/2
        if outcome(value,mid,sku,grade,kind,warranty,url,md)>=target:lo=mid
        else:hi=mid
    return max(0,math.floor(lo))


def target_price(value,sku=None,grade=None,md=None,share=.95):
    cost=cost_estimate(value,sku,grade,md)
    return max(1,math.floor(cost+(1-share)*(value-cost)))


def settled_price(url,sku,grade,md=None):
    cohort=market(md).get('prices',{}).get(f'{url}|{sku}|{grade}',{})
    return cohort.get('median') if cohort.get('n',0)>=2 else None


def comparison_price(url,sku,grade,ask,warranty=0,md=None):
    own=settled_price(url,sku,grade,md)
    if own is not None:return min(ask,own)
    key=f'{url}|{sku}|{grade}|{float(warranty):g}'
    observed=market(md).get('public_prices',{}).get(key,{})
    n=observed.get('n',0)
    if not n:return ask
    # Other buyers really paid these prices, but their terms can differ.
    # Shrink toward our actual quote rather than promising the same discount.
    return min(ask,(n*observed['median']+2*ask)/(n+2))


def observe_buyer_market(md,events,catalog,now):
    costs={};sales=[];broken={};groups=defaultdict(list)
    for e in events:
        d=e.get('data',{})
        if e['kind']=='supply' and d.get('qty')==1:
            for u in d.get('units',[]):costs[u]=d['total']
        elif e['kind']=='settle':sales.append(e)
        elif e['kind']=='broken':broken[d.get('unit')]=d
    risks=defaultdict(lambda:dict(n=0,broken=0));coverage=defaultdict(lambda:dict(broken=0,refunded=0));prices=defaultdict(list);public_prices=defaultdict(list)
    for e in sales:
        d=e['data'];sku=d.get('sku',d.get('item'));v=catalog.get(sku,{})
        if not v.get('list_price'):continue
        grade=d.get('grade');unit=d.get('unit');key=f'{sku}:{grade}'
        if d.get('buyer')=='totato2203-r3':prices[f"{d.get('shop')}|{sku}|{grade}"].append(d['amount'])
        if e['ts']>=now-7200:public_prices[f"{d.get('shop')}|{sku}|{grade}|{float(d.get('warranty_hours') or 0):g}"].append(d['amount'])
        if unit in costs:groups[key].append(costs[unit]/v['list_price'])
        if v.get('kind')!='goods' or e['ts']+21600>now:continue
        f=broken.get(unit)
        for k in (key,f'*:{grade}'):
            risks[k]['n']+=1;risks[k]['broken']+=bool(f)
        if f and (d.get('warranty_hours') or 0)>=6:
            c=coverage[d.get('shop')];c['broken']+=1;c['refunded']+=bool(f.get('refunded'))
    from statistics import median
    def sampled(values):
        values=sorted(values)
        return values if len(values)<=32 else [values[min(len(values)-1,int((i+.5)*len(values)/32))] for i in range(32)]
    pooled=defaultdict(list)
    for key,values in groups.items():
        sku,grade=key.rsplit(':',1);pooled[f"{catalog.get(sku,{}).get('kind','goods')}:{grade}"].extend(values)
    result=dict(at=now,costs={k:dict(n=len(v),ratio=sum(v)/len(v),samples=sampled(v)) for k,v in groups.items()},
                cost_priors={k:dict(n=len(v),ratio=sum(v)/len(v),samples=sampled(v)) for k,v in pooled.items()},
                cost_kinds={sku:p.get('kind','goods') for sku,p in catalog.items()},risk=dict(risks),coverage=dict(coverage),
                prices={k:dict(n=len(v),median=median(v[-20:])) for k,v in prices.items()},
                public_prices={k:dict(n=min(20,len(v)),median=median(v[-20:])) for k,v in public_prices.items()},
                semantics='qty=1 public acquisition costs; mature six-hour breakage proxy; no private budget')
    atomic(Path(md)/'patch36-market.json',result)
    return result


def refresh_scoreboard(md,now=None):
    md=Path(md);now=time.time() if now is None else now
    previous=read(md/'patch36-scoreboard.json',{})
    attempted=read(md/'scoreboard-refresh.json',{}).get('at',0)
    if 0<=now-max(previous.get('at',0),attempted)<900:return previous
    atomic(md/'scoreboard-refresh.json',{'at':now})
    try:
        import json
        from urllib.request import urlopen
        with urlopen('https://market.ourolabs.ai/api/board?round=3',timeout=3) as response:
            return snapshot(md,json.load(response),now)
    except Exception:return previous


def snapshot(md,board,now=None):
    md=Path(md);now=time.time() if now is None else now
    own='totato2203-r3'
    seller=next((s for s in board.get('shops',[]) if s.get('name')==own),{})
    buyer=next((s for s in board.get('buyers',[]) if s.get('name')==own),{})
    players=sorted((p for p in board.get('players',[]) if p.get('student')),key=lambda p:-p.get('student_total',0))
    index=next((i for i,p in enumerate(players) if p.get('player')=='totato2203'),None)
    result=dict(at=now,seller=seller,buyer=buyer,class_rank=None if index is None else index+1,
                class_gap_above=None if index in (None,0) else players[index-1]['student_total']-players[index]['student_total'],
                automatic_paid_dislikes=False,scoring='patch36-margin-share')
    atomic(md/'patch36-scoreboard.json',result)
    from strategy_policy import load,database
    if load(md).get('buyer_reconciliation_enabled'):
        import json
        safe=dict(observed_at=now,name=own,deals=buyer.get('deals'),saved=buyer.get('saved'),orders=buyer.get('orders'),served=buyer.get('served'),held=buyer.get('held'),source='official_board_observed_snapshot_no_per_order_membership')
        with database(md,True) as c:c.execute('insert or replace into official_buyer_snapshots values(?,?)',(now,json.dumps(safe)))
    return result
