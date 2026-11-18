"""Bounded premium bundle trials and six-hour realized-outcome optimization."""
from pathlib import Path
from collections import defaultdict
import hashlib
import math
from optimizer import read,atomic,append,db


DEFAULT={'capital_share':.80,'opening_multiplier':1.0,'default_hours':6,'default_fee':.15,'skus':{}}
WARRANTY_FEES={0:0,3:.08,6:.15,12:.22,24:.35,48:.55}
WARRANTY_MENU='보증 선택(무보증 상품가 기준 가산): 0시간 +0% / 3시간 +8% / 6시간 +15% / 12시간 +22% / 24시간 +35% / 48시간 +55%. 보증 여력·상품 적격 확인 후 견적 확정.'


WARRANTY_MENU='보증은 A등급 공산품에만 제공하며 B/C등급은 무보증으로 판매합니다. '+WARRANTY_MENU


def policy(md):
    value=dict(DEFAULT);value['skus']={}
    value.update(read(Path(md)/'premium-policy.json',{}))
    for key,low,high in [('capital_share',.3,.9),('opening_multiplier',.85,1.2),('default_fee',.03,.20)]:
        if not isinstance(value[key],(int,float)) or isinstance(value[key],bool) or not math.isfinite(value[key]) or not low<=value[key]<=high:
            raise ValueError('Invalid premium setting: '+key)
    return value


def terms(md,task,unit,requested_hours,flexible=True):
    """Bind once per task so restarts or configuration changes cannot reprice it."""
    with db(md) as connection:
        connection.execute('create table if not exists premium_terms(task text primary key,sku text,hours real,fee real)')
        old=connection.execute('select hours,fee from premium_terms where task=?',(task,)).fetchone()
        if old:
            return dict(old)
        config=policy(md)
        profile=config.get('skus',{}).get(unit['sku'],{})
        hours=requested_hours
        fee=WARRANTY_FEES.get(hours,0)
        if hours and requested_hours==3 and flexible:
            hours=profile.get('hours',config['default_hours'])
            fee=profile.get('fee',config['default_fee']) if hours else 0
            # Twenty percent of new tasks test supported bundles. Quotes-only
            # traffic is excluded when the six-hour controller evaluates sales.
            bucket=int(hashlib.sha256(task.encode()).hexdigest()[:8],16)%10
            options=[(0,0),(3,.03),(3,.06),(3,.10),(3,.15),(3,.20),(6,.06),(6,.10),(6,.15),(6,.20)]
            if bucket<2:
                hours,fee=options[int(hashlib.sha256((task+'bundle').encode()).hexdigest()[:8],16)%len(options)]
        if hours not in WARRANTY_FEES or not 0<=fee<=.55:
            raise ValueError('Unsupported warranty bundle')
        connection.execute('insert into premium_terms values(?,?,?,?)',(task,unit['sku'],hours,fee))
    return {'hours':hours,'fee':fee}


def review(md,board,shops,ledger,now):
    """Called by the existing six-hour optimizer; never launches a model."""
    md=Path(md);config=policy(md)
    from shop_profit import ownership_tags
    ours=ownership_tags()
    refunds=defaultdict(float);seen=set()
    for event in ledger:
        data=event.get('data',{});seq=event.get('seq')
        if seq in seen or event.get('round')!=3 or data.get('shop') not in ours or not 0<=now-event.get('ts',0)<7*86400:
            continue
        seen.add(seq)
        if event.get('kind') in ('broken','refund'):
            refunds[data.get('unit')]+=data.get('refunded',0) if event['kind']=='broken' else data.get('amount',0)
    with db(md) as connection:
        connection.execute('create table if not exists premium_terms(task text primary key,sku text,hours real,fee real)')
        rows=[dict(row) for row in connection.execute('select t.*,p.hours,p.fee from trials t join premium_terms p on p.task=t.task where t.inquiry=0 and t.at>? and t.at<?',(now-7*86400,now-3600))]
        supplied={r['unit']:r['supplied'] for r in connection.execute('select unit,supplied from units')}
        settled=[dict(r) for r in connection.execute('select * from trials where inquiry=0 and settled_at>=? and cost is not null',(now-21600,))]
    rate=read(md/'inventory.json',{}).get('storage_per_day',.01)
    def holding(row):
        start=supplied.get(row['unit'])
        return row['cost']*rate*max(0,row['settled_at']-start)/86400 if start is not None else 0
    groups=defaultdict(list)
    for row in rows:
        if row['settled_at'] and row['settled_at']+row['hours']*3600+300>now:
            continue
        groups[row['sku']].append(row)
    before=dict(config,skus=dict(config['skus']))
    changes={}
    for sku,observations in groups.items():
        if len(observations)<12:
            continue
        bands=defaultdict(list)
        for row in observations:
            bands[row['hours'],row['fee']].append(row)
        results=[]
        arrival_rate=len(observations)/max(1,(now-min(r['at'] for r in observations))/3600)
        for (hours,fee),sample in bands.items():
            paid=[r for r in sample if r['settled_at'] and r['cost'] is not None]
            if len(sample)<5 or len(paid)<2:
                continue
            net=sum(r['price']-r['cost']-refunds[r['unit']]-holding(r) for r in paid)
            # Failures contribute zero revenue; costly returns remain losses.
            results.append({'hours':hours,'fee':fee,'expected_net_per_quote':net/len(sample),
                            'estimated_net_per_hour':net/len(sample)*arrival_rate,
                            'paid':len(paid),'attempts':len(sample),'net':net})
        old=config['skus'].get(sku,{})
        new=dict(old)
        if results:
            winner=max(results,key=lambda r:r['estimated_net_per_hour'])
            if winner['expected_net_per_quote']>0:
                new.update(hours=winner['hours'],fee=winner['fee'])
        paid=sum(bool(r['settled_at']) for r in observations)
        multiplier=old.get('opening_multiplier',1.0)
        if paid/len(observations)<.15:
            multiplier=max(.85,round(multiplier-.05,2))
            new['min_margin']=max(.05,round(old.get('min_margin',.12)-.02,2))
            new['market_floor']=max(.85,round(old.get('market_floor',.92)-.02,2))
        elif paid/len(observations)>.5:
            multiplier=min(1.2,round(multiplier+.03,2))
            new['min_margin']=min(.25,round(old.get('min_margin',.12)+.01,2))
        new['opening_multiplier']=multiplier
        if new!=old:
            config['skus'][sku]=new
            changes[sku]={'before':old,'after':new,'attempts':len(observations),'paid':paid,'bundles':results}
    paid=[r for r in rows if r['settled_at'] and r['cost'] is not None and r['settled_at']+r['hours']*3600+300<=now]
    if len(paid)>=8:
        net=sum(r['price']-r['cost']-refunds[r['unit']]-holding(r) for r in paid)
        if net>0:
            config['capital_share']=min(.9,round(config['capital_share']+.05,2))
            atomic(md/'premium-last-good.json',config)
        else:
            previous_share=config['capital_share']
            config=read(md/'premium-last-good.json',config)
            config['capital_share']=max(.3,round(previous_share-.15,2))
    public=shops.get('shops',[]) if isinstance(shops,dict) else shops
    urls={s['name']:s['url'] for s in public}
    quality={}
    for shop in board.get('shops',[]):
        if shop['name'] in urls:
            n=max(0,shop.get('deals',0));broken=max(0,shop.get('broken',0));refunded=max(0,shop.get('refunds',0))
            quality[urls[shop['name']]]={'samples':n,'break_rate':min(.25,max(.01,(broken+2)/(n+25))),
                                       'refund_coverage':min(1,(refunded+1)/(broken+2))}
    atomic(md/'buyer-quality.json',{'at':now,'shops':quality})
    top=sorted([s for s in board.get('shops',[]) if not s.get('house')],key=lambda s:-(s.get('profit') or 0))[:5]
    prior=read(md/'premium-review.json',{})
    hours=(now-prior.get('at',now))/3600
    observation=[]
    for shop in top:
        previous=next((s for s in prior.get('top5',[]) if s['name']==shop['name']),{})
        stock=next((s.get('stock',{}) for s in public if s['name']==shop['name']),{})
        observation.append({'name':shop['name'],'profit':shop.get('profit',0),'revenue':shop.get('revenue',0),
                            'profit_per_hour':(shop.get('profit',0)-previous['profit'])/hours if previous and hours>0 else None,
                            'revenue_per_hour':(shop.get('revenue',0)-previous['revenue'])/hours if previous and hours>0 else None,'stock':stock})
    catalog=read(md/'catalog.json',{})
    comparison={}
    def high(row):
        product=catalog.get(row['sku'],{})
        return product.get('kind')=='goods' and product.get('list_price',0)>=200000
    for name,group in [('premium',[r for r in settled if high(r)]),('other',[r for r in settled if not high(r)])]:
        net=sum(r['price']-r['cost']-refunds[r['unit']]-holding(r) for r in group)
        comparison[name]={'known_cost_sales':len(group),'net_before_unallocated_costs':net,'net_per_hour':net/6,
                          'revenue_per_hour':sum(r['price'] for r in group)/6,
                          'return_on_acquisition':net/sum(r['cost'] for r in group) if group and sum(r['cost'] for r in group)>0 else None}
    result={'at':now,'top5':observation,'changes':changes,'capital_share':config['capital_share'],'mature_paid':len(paid),'comparison_6h':comparison,
            'limitation':'Sample estimates, not causal warranty uplift; candidate margins also include a storage/risk allowance.'}
    atomic(md/'premium-policy.json',config)
    atomic(md/'premium-review.json',result)
    append(md/'optimization/premium-history.jsonl',{'at':now,'before':before,'after':config,'review':result})
    return result
