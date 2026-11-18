"""Bounded 2x2 quote experiment, evaluated by the existing publication worker."""
import hashlib,json,math,time
from pathlib import Path
from collections import Counter,defaultdict
from optimizer import db,read,atomic

def schema(c):
    c.execute('''create table if not exists extreme_quotes(
      task text primary key,at real,sku text,grade text,hours real,unit text,
      price_arm text,base_quote integer,requested_multiplier real,requested_quote integer,
      effective_quote integer,reason text,market_quote_valid integer)''')
    if 'experiment' not in {r[1] for r in c.execute('pragma table_info(extreme_quotes)')}:
        c.execute("alter table extreme_quotes add column experiment text not null default 'pricing_x50_v1'")
    c.execute('create table if not exists settled_review_guidance(task text primary key,at real)')
    c.execute('''create table if not exists price_policy_versions(
      task text primary key,version text,effective_at real,recorded_at real)''')
    if c.execute("select 1 from sqlite_master where name='liquidity_terms'").fetchone():
        c.execute('''insert or ignore into price_policy_versions
            select e.task,case when l.task is null then 'original-v1' else 'liquidity-v1' end,e.at,?
            from extreme_quotes e left join liquidity_terms l on l.task=e.task''',(time.time(),))
    else:
        c.execute("insert or ignore into price_policy_versions select task,'original-v1',at,? from extreme_quotes",(time.time(),))

def assignment(policy,task):
    bucket=int.from_bytes(hashlib.sha256(('round3-price-v1:'+task).encode()).digest()[:8],'big')%100
    return f"x{float(policy.get('multiplier',50)):g}" if policy.get('enabled',False) and bucket<policy.get('allocation',20) else 'baseline'

def planned_arm(md,task):
    """Read a locked assignment or the same deterministic draw used by terms."""
    import sqlite3
    from contextlib import closing
    md=Path(md)
    try:
        with closing(sqlite3.connect('file:'+str((md/'optimization.sqlite3').resolve())+'?mode=ro',uri=True)) as c:
            old=c.execute('select price_arm from extreme_quotes where task=?',(task,)).fetchone()
            if old:return old[0]
    except sqlite3.OperationalError:pass
    return assignment(read(md/'extreme-quote-policy.json',{}),task)

def record_version(c,task,at):
    liquid=c.execute("select 1 from sqlite_master where name='liquidity_terms'").fetchone()
    applied=liquid and c.execute('select 1 from liquidity_terms where task=?',(task,)).fetchone()
    c.execute('insert or ignore into price_policy_versions values(?,?,?,?)',
              (task,read(Path(c.execute('pragma database_list').fetchone()[2]).parent/'operating-policy.json',{}).get('version','liquidity-v1' if applied else 'original-v1'),at,time.time()))

def terms(md,task,unit,base_quote,hours,initial,can_fund,now=None):
    md=Path(md);now=time.time() if now is None else now
    policy=read(md/'extreme-quote-policy.json',{})
    multiplier=float(policy.get('multiplier',50));label=f'X{multiplier:g}'
    if multiplier not in (2,50):raise ValueError('Unsupported quote experiment multiplier')
    if (policy.get('enabled') and policy.get('status')!=f'KEEP_{label}_CANDIDATE'
            and now-policy.get('experiment_started_at',now)>=43200):
        policy.update(enabled=False,allocation=0,status=f'DISABLE_{label}_MAXIMUM_OBSERVATION',decided_at=now)
        atomic(md/'extreme-quote-policy.json',policy)
    with db(md) as c:
        schema(c);old=c.execute('select * from extreme_quotes where task=?',(task,)).fetchone()
        if old:
            record_version(c,task,old['at'])
            return dict(old)
        if not initial:return None
        arm=assignment(policy,task)
        requested=math.ceil(int(base_quote)*multiplier) if arm!='baseline' else int(base_quote)
        effective=requested;reason=None
        if arm!='baseline':
            if requested>2**53-1:reason=f'{arm}_infeasible_json_integer_range'
            elif policy.get('protocol_supported') is False:reason=f'{arm}_infeasible_protocol'
            elif not can_fund(requested):reason=f'{arm}_infeasible_existing_warranty_capacity'
            if reason:effective=int(base_quote)
        c.execute('''insert into extreme_quotes(task,at,sku,grade,hours,unit,price_arm,base_quote,
                  requested_multiplier,requested_quote,effective_quote,reason,market_quote_valid,experiment)
                  values(?,?,?,?,?,?,?,?,?,?,?,?,null,?)''',
                  (task,now,unit['sku'],unit.get('grade'),hours,unit.get('unit'),arm,int(base_quote),
                   multiplier if arm!='baseline' else 1,requested,effective,reason,
                   policy.get('experiment','pricing_x50_v1')))
        if arm!='baseline' and not reason:
            c.execute('update trials set strategy=? where task=?',(policy.get('experiment','pricing_x50_v1'),task))
        record_version(c,task,now)
        return dict(c.execute('select * from extreme_quotes where task=?',(task,)).fetchone())

def is_extreme(record):
    return bool(record and record['price_arm'] in ('x50','x2') and not record['reason'])

def negotiation(record,buyer_offer,rounds=0):
    quote=record['effective_quote']
    if record['price_arm']=='x2' and rounds>=8 and (buyer_offer is None or buyer_offer<quote):
        return {'action':'reject','type':'x2_round_limit'}
    return {'action':'accept','type':record['price_arm']} if buyer_offer is not None and buyer_offer>=quote else {'action':'ask','price':quote,'type':record['price_arm']+'_no_concession'}

def decide(quotes,settlements,profit,baseline,market_valid,hours,diverse=False):
    if market_valid is False:return 'DISABLE_X50_PROTOCOL'
    if hours<6:return 'CONTINUE_EXPERIMENT'
    if quotes<30:return 'EXTEND_OBSERVATION' if hours<12 else 'DISABLE_X50_INSUFFICIENT_DATA'
    if settlements==0:return 'DISABLE_X50_NO_SETTLEMENT'
    if market_valid is not True:return 'DISABLE_X50_UNVERIFIED_PROTOCOL'
    if not diverse:return 'DISABLE_X50_CONCENTRATED_OR_UNMATCHED'
    if profit>baseline+max(0,baseline*.10):return 'KEEP_X50_CANDIDATE'
    return 'DISABLE_X50_LOWER_EXPECTED_PROFIT'

def evaluate(md,now=None,include_records=False):
    md=Path(md);now=time.time() if now is None else now
    policy=read(md/'extreme-quote-policy.json',{})
    start=policy.get('experiment_started_at',now)
    experiment=policy.get('experiment','pricing_x50_v1')
    multiplier=policy.get('multiplier',50);arm=f'x{multiplier:g}';label=arm.upper()
    with db(md) as c:
        schema(c)
        exists=c.execute("select 1 from sqlite_master where name='purchase_guidance'").fetchone()
        rows=[dict(r) for r in c.execute('''select e.*,g.enabled as guidance,t.inquiry,t.cost,
            t.first_offer,t.negotiation_rounds,t.settled_at,t.price,t.margin,t.buyer,u.supplied,
            coalesce(v.version,'original-v1') as baseline_version
            from extreme_quotes e join purchase_guidance g on g.task=e.task
            join trials t on t.task=e.task left join units u on u.unit=e.unit
            left join price_policy_versions v on v.task=e.task
            where e.at>=? and e.experiment=?''',(start,experiment))] if exists else []
    rows=[r for r in rows if not r['inquiry']]
    # A market settlement is the only final proof that the actual quote clears.
    violation=any(is_extreme(r) and r['settled_at'] is not None and r['price']<r['effective_quote'] for r in rows)
    refunds={};expired={};journal=md/'ledger.jsonl'
    if journal.exists():
        from shop_profit import ownership_tags
        tags=ownership_tags()
        with journal.open(encoding='utf-8') as source:
            for line in source:
                if not any(kind in line for kind in ('"refund"','"broken"','"expire"')):continue
                try:e=json.loads(line)
                except ValueError:continue
                d=e.get('data',{})
                if e.get('round',3)!=3 or d.get('shop') not in tags:continue
                if e.get('kind') in ('refund','broken') and d.get('unit'):
                    refunds[d['unit']]=max(refunds.get(d['unit'],0),d.get('amount',0) if e['kind']=='refund' else d.get('refunded',0))
                if e.get('kind')=='expire' and d.get('unit') and e.get('ts',0)>=start:
                    expired[d['unit']]=d.get('cost',0)
    unit_counts=Counter(r['unit'] for r in rows)
    first_at={};paid_at={}
    for r in rows:
        unit=r['unit'];first_at[unit]=min(first_at.get(unit,now),r['at'])
        if r['settled_at']:paid_at[unit]=r['settled_at']
    from warranty_risk import cohort
    risk=dict(cohorts={},reserve_rate=.15,measured_rate=.08,mature_warranted_sales=0)
    risk.update(read(md/'warranty-risk.json',{}))
    for r in rows:
        r['observed_refund']=refunds.get(r['unit'],0) if r['settled_at'] else 0
        rate=cohort(risk,r['sku'],r['grade'],r['hours']).get('reserve_rate',.15)
        pending=r['settled_at'] and r['hours'] and now-r['settled_at']<min(6,r['hours'])*3600+300 and not r['observed_refund']
        r['pending_refund_estimate']=r['price']*rate if pending else 0
        elapsed=max(0,paid_at.get(r['unit'],now)-max(start,first_at[r['unit']],r['supplied'] or start))
        r['holding_estimate']=(r['cost'] or 0)*.01*elapsed/86400/max(1,unit_counts[r['unit']])
        r['expiry_allocation']=expired.get(r['unit'],0)/max(1,unit_counts[r['unit']]) if not paid_at.get(r['unit']) else 0
        r['estimated_net']=(r['margin'] or 0)-r['observed_refund']-r['pending_refund_estimate']-r['holding_estimate']-r['expiry_allocation']
    def metrics(items):
        paid=[r for r in items if r['settled_at']];known=[r for r in paid if r['margin'] is not None]
        return dict(quotes=len(items),valid_premium_quotes=sum(is_extreme(r) for r in items),
            fallback=sum(bool(r['reason']) for r in items),settled=len(paid),
            premium_settled=sum(is_extreme(r) and r['price']>=r['effective_quote'] for r in paid),
            conversion=len(paid)/max(1,len(items)),mean_rounds=sum(r['negotiation_rounds'] or 0 for r in items)/max(1,len(items)),
            mean_first_offer=sum(r['first_offer'] or 0 for r in items if r['first_offer'] is not None)/max(1,sum(r['first_offer'] is not None for r in items)),
            mean_settlement=sum(r['price'] for r in paid)/len(paid) if paid else None,
            price_to_quote=sum(r['price']/r['effective_quote'] for r in paid)/len(paid) if paid else None,
            revenue=sum(r['price'] for r in paid),gross=sum(r['margin'] for r in known),known_cost_sales=len(known),
            observed_refunds=sum(r['observed_refund'] for r in items),pending_refund_estimate=sum(r['pending_refund_estimate'] for r in items),
            expiry_allocation=sum(r['expiry_allocation'] for r in items),
            holding_estimate=sum(r['holding_estimate'] for r in items),estimated_net=sum(r['estimated_net'] for r in items),
            expected_profit_per_quote=sum(r['estimated_net'] for r in items)/max(1,len(items)))
    # Preserve outcome records for guidance measurement while keeping the
    # original price experiment's comparison free of new baseline policies.
    excluded=[r for r in rows if r['baseline_version']!='original-v1' or any(
        gap['start']<=r['at']<gap['end'] for gap in policy.get('allocation_interruptions',[]))]
    excluded_ids={r['task'] for r in excluded}
    comparison=[r for r in rows if r['task'] not in excluded_ids]
    groups={k:[] for k in 'ABCD'}
    for r in comparison:
        r['group']=('D' if r['guidance'] else 'C') if r['price_arm']==arm else ('B' if r['guidance'] else 'A')
        groups[r['group']].append(r)
    matched=defaultdict(lambda:defaultdict(list))
    for r in comparison:matched[(r['sku'],r['grade'],r['hours'])][r['price_arm']].append(r)
    strata=[];base_matched=[];high_matched=[]
    for key,arms in matched.items():
        if not arms['baseline'] or not arms[arm]:continue
        strata.append(dict(sku=key[0],grade=key[1],hours=key[2],baseline=metrics(arms['baseline']),premium=metrics(arms[arm])))
        base_matched.extend(arms['baseline']);high_matched.extend(arms[arm])
    base=metrics(groups['A']+groups['B']);high=metrics(groups['C']+groups['D'])
    settled_high=[r for r in comparison if is_extreme(r) and r['settled_at'] and r['price']>=r['effective_quote']]
    confirmed=bool(settled_high)
    with db(md) as c:
        for r in settled_high:c.execute('update extreme_quotes set market_quote_valid=1 where task=?',(r['task'],))
    diverse=len({r['sku'] for r in settled_high})>=2 and len({r['buyer'] for r in settled_high})>=2
    if settled_high:
        total=sum(max(0,r['estimated_net']) for r in settled_high)
        diverse=diverse and total>0 and max(max(0,r['estimated_net']) for r in settled_high)<=total*.5
    complete=base['known_cost_sales']==base['settled'] and high['known_cost_sales']==high['settled']
    proof=(False if violation or policy.get('protocol_supported') is False else True if confirmed else None)
    decision=decide(high['valid_premium_quotes'],len(settled_high),high['expected_profit_per_quote'],base['expected_profit_per_quote'],
                    proof,(now-start)/3600,diverse and complete and len(strata)>=2 and
                    metrics(high_matched)['expected_profit_per_quote']>metrics(base_matched)['expected_profit_per_quote']+max(0,metrics(base_matched)['expected_profit_per_quote']*.10)).replace('X50',label)
    if policy.get('status')=='STOPPED_FOR_LIQUIDITY':decision='STOPPED_FOR_LIQUIDITY'
    result=dict(at=now,experiment=experiment,multiplier=multiplier,price_arm=arm,experiment_started_at=start,elapsed_hours=(now-start)/3600,decision=decision,
                groups={k:metrics(v) for k,v in groups.items()},baseline=base,premium=high,
                actual_premium_only=metrics([r for r in comparison if is_extreme(r)]),same_terms=strata,
                separate_liquidity_cohort=metrics([r for r in rows if r['baseline_version']=='liquidity-v1']),
                excluded_interruption_quotes=sum(any(gap['start']<=r['at']<gap['end'] for gap in policy.get('allocation_interruptions',[])) for r in rows),
                protocol_settlement_confirmed=confirmed,price_mismatch=violation,
                limitation='Holding costs and pending refunds are estimates. Infeasible assigned tasks remain in intent-to-treat groups; only actual premium settlements prove market acceptance.')
    inv=read(md/'inventory.json',{});book=inv.get('balance',0)-inv.get('start',5000000)-inv.get('injected',0)+sum(u['cost'] for u in inv.get('units',[]))
    result['shop_book']=book
    result['shop_book_delta']=book-policy['initial_book'] if 'initial_book' in policy else None
    result['shop_scoreboard']=read(md/'patch36-scoreboard.json',{}).get('seller',{})
    for hours in (2,6,12):
        path=md/f'{experiment}-checkpoint-{hours}h.json'
        if now-start>=hours*3600 and not path.exists():atomic(path,result)
    if decision.startswith('DISABLE_') and policy.get('enabled'):
        policy.update(enabled=False,allocation=0,status=decision,decided_at=now)
        atomic(md/'extreme-quote-policy.json',policy)
    elif decision==f'KEEP_{label}_CANDIDATE' and policy.get('enabled'):
        policy.update(status=decision,decided_at=now);atomic(md/'extreme-quote-policy.json',policy)
    atomic(md/'extreme-quote-results.json',result)
    if include_records:result['_records']=rows
    return result

async def settled_review_notes(md,store,now=None):
    """Add a factual review invitation only after market settlement is recorded."""
    if store is None:return
    from a2a.types import TaskState
    from a2a.helpers import new_text_part
    from a2a.server.context import ServerCallContext
    now=time.time() if now is None else now
    with db(md) as c:
        schema(c)
        rows=c.execute('''select e.task from extreme_quotes e join trials t on t.task=e.task
            where t.settled_at is not null and e.task not in(select task from settled_review_guidance)
            order by t.settled_at desc limit 20''').fetchall()
    for r in rows:
        task=await store.get(r['task'],ServerCallContext())
        if task is None or task.status.state!=TaskState.TASK_STATE_COMPLETED:continue
        task.status.message.parts.append(new_text_part('The market recorded settlement. Please rate your actual product and service experience honestly; if it was positive, consider a high rating. Reviews do not affect warranty rights.'))
        await store.save(task,ServerCallContext())
        with db(md) as c:c.execute('insert or ignore into settled_review_guidance values(?,?)',(r['task'],now))
