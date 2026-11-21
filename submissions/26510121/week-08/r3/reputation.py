"""Account-balanced review scenarios with temporal model selection and no identity guessing.

The score effect is a planning surrogate: full historic account counts and the
next review are unknown. Positive effects require a disjoint holdout improvement.
Independently, supported low-rating cohorts supply a conservative downside check.
"""
from collections import Counter,defaultdict
import math
import statistics
import time
from .state import identity

KEY='shop-reputation:v24'
FEATURES=[('grade',),('kind','grade'),('kind','insured'),('kind','grade','insured'),('kind','grade','cheap')]


def feature(row,fields):return '|'.join(str(row[k]) for k in fields)


def innovations(rows):
    """Forecast changes in a reviewer's mean, not the next raw star alone.

    Only past reviews initialize each account. First local observations are
    left-censored, so require three observations before labeling a change.
    """
    accounts=defaultdict(list);result=[]
    for row in rows:
        past=accounts[row['author']]
        if len(past)>=3:
            prior=statistics.mean(past)
            result.append(dict(row,innovation=(row['stars']-prior)/(len(past)+1),
                prior_account_mean=prior,prior_account_n=len(past)))
        past.append(row['stars'])
    return result


def fit(rows,fields,prior):
    owners=defaultdict(list);groups=defaultdict(lambda:defaultdict(list))
    for r in rows:
        owners[r['author']].append(r['innovation']);groups[feature(r,fields)][r['author']].append(r['innovation'])
    baseline=statistics.mean(statistics.mean(v) for v in owners.values()) if owners else 0
    stats={}
    for key,accounts in groups.items():
        means=[statistics.mean(v) for v in accounts.values()];n=sum(map(len,accounts.values()))
        if n<10 or len(means)<3:continue
        mean=statistics.mean(means);se=statistics.stdev(means)/math.sqrt(len(means))
        stats[key]={'mean':(mean*len(means)+baseline*prior)/(len(means)+prior),
            'empirical_mean':mean,'upper90':mean+1.645*se,
            'owners':len(means),'reviews':n}
    return {'fields':list(fields),'prior':prior,'baseline':baseline,'groups':stats,'owners':len(owners)}


def loss(rows,model,baseline=False):
    errors=defaultdict(list)
    for r in rows:
        pred=model['baseline'] if baseline else model['groups'].get(feature(r,model['fields']),{}).get('mean',model['baseline'])
        if baseline=='zero':pred=0
        errors[r['author']].append((r['innovation']-pred)**2)
    return statistics.mean(statistics.mean(v) for v in errors.values()) if errors else None


def select(rows):
    n=len(rows);a=int(n*.6);b=int(n*.8);train,tune,test=rows[:a],rows[a:b],rows[b:]
    candidates=[fit(train,fields,p) for fields in FEATURES for p in (3,10,30)]
    chosen=min(candidates,key=lambda m:(loss(tune,m) if tune else float('inf'),len(m['fields']),-m['prior']))
    evaluated=fit(rows[:b],chosen['fields'],chosen['prior'])
    mse,base=loss(test,evaluated),loss(test,evaluated,True)
    zero=loss(test,evaluated,'zero')
    covered=sum(feature(r,evaluated['fields']) in evaluated['groups'] for r in test)
    ready=(len(train)>=150 and len(tune)>=40 and len(test)>=40 and
        len({r['author'] for r in test})>=8 and covered>=len(test)*.5 and
        mse is not None and base>0 and zero>0 and mse<min(base,zero)*.98)
    return fit(rows,chosen['fields'],chosen['prior']),{'mode':'validated_predictive_surrogate' if ready else 'shadow',
        'train':len(train),'tune':len(tune),'test':len(test),'test_owners':len({r['author'] for r in test}),
        'test_mse':mse,'baseline_test_mse':base,'zero_change_test_mse':zero,'covered_test':covered,
        'fields':chosen['fields'],'prior':chosen['prior'],'split':[.6,.2,.2],
        'selection_uses_tune_only':True,'official_account_count_complete':False,
        'target':'next change in observed reviewer account mean; first three observations excluded'}


def update(journal,config,now=None):
    now=time.time() if now is None else now
    old=journal.get(KEY,{})
    if 0<=now-old.get('at',0)<60:return old
    catalog=journal.snapshot('catalog.json',{});sales={r['_event_id'].partition('settlement:')[2]:r for r in journal.events('shop_settled',20000)}
    unique={}
    for r in journal.events('shop_review_observed',20000):
        if (r.get('source')!='verified_market_result_and_accepted_own_task' or not r.get('identity_known') or
                not r.get('author') or not 0<=now-r['_ts']<=172800):continue
        sale=sales.get(str(r.get('ledger')));L=catalog.get((sale or {}).get('sku'),{}).get('list_price')
        if not sale or not L or type(r.get('stars')) not in (int,float) or not 1<=r['stars']<=5:continue
        unique.setdefault(str(r['ledger']),{'author':r['author'],'stars':r['stars'],'at':r['_ts'],
            'grade':sale.get('grade'),'kind':catalog.get(sale['sku'],{}).get('kind'),
            'insured':(sale.get('warranty_hours') or 0)>=6,'cheap':sale['price']/L<=.75})
    rawrows=sorted(unique.values(),key=lambda r:r['at']);rows=innovations(rawrows);model,summary=select(rows)
    owners=Counter(r['author'] for r in rawrows)
    # Independent coarse contract cells prevent a sparse price grid from
    # hiding repeated low ratings. These cells never award a positive bonus.
    downside=fit(rows,('kind','grade','insured'),0)['groups']
    result={'at':now,'expires_at':now+180,'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'model':model,'summary':summary,'downside':downside,'owner_counts_observed':dict(owners),
        'evidence_reviews':len(rows),'raw_reviews':len(rawrows),'observed_accounts':len(owners)}
    result['id']=identity(result);journal.put(KEY,result)
    journal.add('shop_reputation_prepared',{'at':now,'id':result['id'],'summary':summary,'reviews':len(rows),'accounts':len(owners)})
    return result


def packet(journal,now):
    from .decision_view import fact
    row=fact(journal,'shop-reputation-v24',lambda:journal.get(KEY,{}))
    if (not -2<=now-row.get('at',-1000)<180 or now>=row.get('expires_at',0) or
            row.get('rules_hash')!=journal.get('market_rules',{}).get('sha256') or
            row.get('id')!=identity({k:v for k,v in row.items() if k!='id'}) or
            not -2<=now-journal.get('board_checked_at',0)<180):return {}
    return row


def augment(journal,config,unit,candidates,now=None):
    now=time.time() if now is None else now;row=packet(journal,now)
    if not row:return candidates
    from .decision_view import fact
    own=fact(journal,'reputation-own-board-v24',lambda:next((r for r in journal.snapshot('board.json',{}).get('shops',[]) if r.get('name')=='seochanit'),{}))
    current=own.get('stars_rank');model=row['model'];counts=list(row['owner_counts_observed'].values())
    if type(current) not in (int,float) or not counts:return candidates
    # Partial observed account counts deliberately remain a disclosed scenario.
    coverage=min(1,row.get('raw_reviews',row['evidence_reviews'])/max(1,own.get('reviews',0)))
    impact=coverage/max(1,row['observed_accounts'])
    for candidate in candidates:
        if candidate.get('action')=='hold':continue
        features={'kind':unit.get('kind'),'grade':unit.get('grade'),
            'insured':(candidate.get('warranty_hours') or 0)>=6,'cheap':candidate['price']/max(1,unit['list_price'])<=.75}
        group=model['groups'].get(feature(features,model['fields']),{})
        coarse=row['downside'].get(feature(features,('kind','grade','insured')), {})
        prediction=None;mode=None
        if row['summary']['mode']=='validated_predictive_surrogate' and group:
            prediction=group['mean'];mode='validated_account_balanced_model'
        elif coarse.get('reviews',0)>=20 and coarse.get('owners',0)>=5 and coarse.get('upper90',1)<0:
            prediction=coarse['upper90'];mode='supported_low_rating_cohort_upper_scenario'
        if prediction is None:continue
        difference=prediction
        scale=impact
        probability=candidate.get('evidence',{}).get('quote_completion',{}).get('completion',1)
        delta=probability*difference*scale
        candidate['expected_delta']['stars_rank']=delta
        candidate.setdefault('evidence',{})['account_reputation']={'mode':mode,'prediction':prediction,
            'current_account_score':current,'delta_scenario':delta,'observed_accounts':row['observed_accounts'],
            'prediction_is_account_mean_change':True,'historical_review_coverage_scenario':coverage,
            'partial_account_counts':True,'official_mapping_exact':False,'model_id':row['id']}
    return candidates


def unit_priority(unit,grade_now,remaining_hours,config):
    # Prefer a better condition when the same requested SKU is available.
    # FIFO still breaks ties; expiry clearance remains explicit in pricing.
    return ({'A':0,'B':1,'C':2}.get(grade_now,3),remaining_hours,unit.get('supplied_at',0))
