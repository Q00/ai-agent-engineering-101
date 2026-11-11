"""A bounded champion/challenger laboratory around the fast execution engine.

Profiles change exploration allocation, never money, certificate or receipt caps.
Randomized order assignments are frozen. Promotion uses completed quality cohorts.
"""
from collections import Counter
import math,statistics,time
from .state import identity
from .outcomes import latest

PROFILES={'contract_frontier':{'buyer_contract_probe_percent':8,'buyer_contract_gap_power':2,'buyer_contract_diverse_quotes':False},
          'coverage_probe':{'buyer_contract_probe_percent':15,'buyer_contract_gap_power':1,'buyer_contract_diverse_quotes':True},
          'selective_contracts':{'buyer_contract_probe_percent':4,'buyer_contract_gap_power':3,'buyer_contract_diverse_quotes':False}}
REVISION='system-v13-contract-frontier'


def assign(journal,order,config):
    from .strategy_lab import profiles
    available=dict(PROFILES,**profiles(journal)) if config.get('market_cycle_enabled') else PROFILES
    key='framework-order:'+str(order['order']);frozen=journal.get(key)
    if frozen is None:
        arena=journal.get('framework-arena',{})
        profile=arena.get('champion','contract_frontier');arm='champion'
        if order.get('kind')=='goods' and arena.get('active') and int(identity(order['order'])[:8],16)%100<10:
            profile=arena.get('challenger','coverage_probe');arm='challenger'
        if profile not in available:profile='contract_frontier'
        board=journal.snapshot('board.json',{})
        own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
        frozen={'order':order['order'],'kind':order.get('kind'),'profile':profile,'arm':arm,
                'experiment':arena.get('experiment'),'revision':REVISION,'saved_at_assignment':own.get('saved',0),
                'rules_sha256':journal.get('market_rules',{}).get('sha256')}
        if profile not in PROFILES:
            frozen['profile_patch']=available[profile]
        journal.put(key,frozen);journal.add('framework_order',frozen,eid=key,role='buyer')
    return dict(frozen.get('profile_patch') or available.get(frozen['profile'],PROFILES['contract_frontier']),framework_profile=frozen['profile'])


def summarize(journal,now):
    orders={r['order']:r for r in journal.events('buyer_order',4000) if 0<=now-r['_ts']<=14400}
    results={r['order']:r for r in journal.events('market_result',4000) if r.get('order') in orders}
    paid={r['order']:r for r in journal.events('purchase',4000) if r.get('order') in orders}
    quotes=journal.events('quote_comparison',6000);segments={}
    for kind in ('food','goods'):
        ids={oid for oid,r in orders.items() if r.get('kind')==kind};closed=ids&results.keys()
        rows=[r for r in quotes if r.get('order') in ids]
        segments[kind]={'orders':len(ids),'closed':len(closed),'paid':len(ids&paid.keys()),
                        'conversion':len(ids&paid.keys())/max(1,len(closed)),
                        'quotes':len(rows),'above_cap':sum(r['ask']>r['assessment']['cap'] for r in rows)}
    return segments


def evaluate(journal,arena,now):
    assignments=[r for r in journal.events('framework_order',6000) if r.get('experiment')==arena.get('experiment')
                 and r.get('kind')=='goods' and r['_ts']>=arena.get('since',now) and r['_ts']<=now-22200]
    purchases={r['order']:r for r in journal.events('purchase',10000)}
    results={r['order']:r for r in journal.events('market_result',10000)}
    outcomes=latest(journal);stats={}
    for arm in ('champion','challenger'):
        rewards=[];durations=[];npaid=0;pending=0
        for row in assignments:
            if row['arm']!=arm:continue
            oid=row['order'];result=results.get(oid)
            if not result:pending+=1;continue
            if bool(result.get('verified'))!=(oid in purchases):pending+=1;continue
            reward=0
            if oid in purchases:
                outcome=outcomes.get(oid,{})
                if not outcome.get('canonical') or outcome.get('refund_status')=='expected_by_warranty_rule':pending+=1;continue
                reward=outcome.get('saved_contribution',0)-row.get('saved_at_assignment',0);npaid+=1
            rewards.append(reward)
            durations.append(max(1,result['_ts']-row['_ts'])+15)
        n=len(rewards);avg=statistics.mean(rewards) if n else 0
        se=math.sqrt(statistics.variance(rewards)/n) if n>1 else 1
        stats[arm]={'closed':n,'paid':npaid,'pending':pending,'reward':avg,'standard_error':se,
                    'seconds':statistics.mean(durations) if durations else None,
                    'score_per_second':avg/statistics.mean(durations) if durations else 0}
    a,b=stats['champion'],stats['challenger']
    ready=min(a['closed'],b['closed'])>=50 and min(a['paid'],b['paid'])>=20
    better=ready and b['reward']-1.96*b['standard_error']>a['reward']+1.96*a['standard_error'] and b['score_per_second']>a['score_per_second']
    faster=ready and b['seconds']<=a['seconds']*1.2
    from .risk import wilson
    worse=min(a['closed'],b['closed'])>=50 and (wilson(b['paid'],b['closed'])<wilson(a['paid'],a['closed'],False) or
        ready and b['reward']+1.96*b['standard_error']<a['reward']-1.96*a['standard_error'])
    return {'at':now,'stats':stats,'ready':ready,'promote':bool(better and faster),'rollback':bool(worse),
            'note':'Saved uplift over assignment baseline; completed 6h+10m cohorts, minimum samples and latency guard.'}


def update(journal,config,now=None):
    if not config.get('system_framework_enabled'):return {}
    now=time.time() if now is None else now
    if 0<=now-journal.get('framework-last-update',0)<300:return journal.get('framework-plan',{})
    from .long_memory import refresh
    refresh(journal,now)
    segments=summarize(journal,now);arena=journal.get('framework-arena',{})
    rules=journal.get('market_rules',{});rule_hash=rules.get('sha256')
    if arena.get('revision')!=REVISION or arena.get('rules_sha256')!=rule_hash:
        arena={'revision':REVISION,'rules_sha256':rule_hash,'champion':'contract_frontier',
            'challenger':'coverage_probe','active':bool(config.get('framework_experiments_enabled')),
            'since':now,'experiment':identity([REVISION,rule_hash,now])}
        journal.add('framework_experiment',dict(arena,action='started'),role='buyer')
    elif not arena.get('active') and now>=arena.get('retry_at',float('inf')) and config.get('framework_experiments_enabled') and journal.get('adaptive:buyer',{}).get('status')!='trial':
        arena=dict(arena,active=True,since=now,experiment=identity([REVISION,rule_hash,now]))
        if config.get('market_cycle_enabled'):
            from .strategy_lab import next_candidate
            arena['challenger']=next_candidate(journal,arena['champion'],arena.get('challenger'))
        journal.add('framework_experiment',dict(arena,action='restarted_after_cooldown'),role='buyer')
    evaluation=evaluate(journal,arena,now)
    if arena.get('active') and evaluation['promote']:
        alternatives=[name for name in PROFILES if name not in (arena['champion'],arena['challenger'])]
        if config.get('market_cycle_enabled'):
            from .strategy_lab import next_candidate
            alternatives=[next_candidate(journal,arena['challenger'],arena['champion'])]
        arena=dict(arena,champion=arena['challenger'],challenger=alternatives[0] if alternatives else arena['champion'],since=now,
                   experiment=identity([REVISION,rule_hash,now]))
        journal.add('framework_experiment',dict(arena,action='promoted',evidence=evaluation),role='buyer')
    elif arena.get('active') and (evaluation['rollback'] or now-arena['since']>172800):
        arena=dict(arena,active=False,retry_at=now+21600)
        journal.add('framework_experiment',dict(arena,action='rollback' if evaluation['rollback'] else 'expired_without_verified_improvement',evidence=evaluation),role='buyer')
    issues=[];goods=segments['goods']
    if goods['closed']>=20 and goods['conversion']<.3:issues.append('goods_contract_conversion')
    if goods['quotes']>=20 and goods['above_cap']/goods['quotes']>.8:issues.append('price_frontier_no_overlap')
    reviews=journal.events('shop_review_observed',500)
    authors={r['author'] for r in reviews if r.get('identity_known')}
    issues.append('shop_rating_model_shadow' if len(authors)<5 else 'shop_rating_model_requires_holdout_validation')
    plan={'at':now,'revision':REVISION,'segments':segments,'issues':issues,'arena':arena,
          'evaluation':evaluation,'shop_rating':{'mode':'shadow','reviews':len(reviews),'verified_accounts':len(authors)},
          'next_actions':[{'issue':name,'stage':'observe' if 'rating' in name else 'bounded_contract_execution'} for name in issues],
          'memory':{'facts':'market_unit_ledger','episodes':'contract and customer summaries','procedures':'versioned bounded profiles'},
          'hard_guards':['receipt_signature','exact_sku','market_certificate','saved_price_cap','refund_reserve','no_uncertain_payment_retry']}
    journal.put('framework-arena',arena);journal.put('framework-plan',plan);journal.put('framework-last-update',now)
    journal.add('framework_evaluation',plan,role='system')
    return plan
