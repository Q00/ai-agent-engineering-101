"""Slow LLM policy review with rejection evidence, versioned changes and expiry.

Runs outside trading hooks. Only bounded configuration is changed; source code,
market constraints, outstanding reservations and already signed terms are immutable.
"""
import argparse,fcntl,json,os,time,uuid,urllib.request,sqlite3,math
from pathlib import Path
from collections import Counter
from harness import decide_json
from market_memory import events,record
from inventory_observation import timestamp
from replenishment_plan import inventory_risk
from procurement_cost import ceiling
from seller_meta_policy import DEFAULT,BOUNDS,FOCUS,active,validate,upgrade,POLICY_VERSION
from market_scoring import VERSION,BOOK_CONTRACT,book_metrics
from capital_policy import WINDOW_MAX_UNITS
from inventory_portfolio import MIN_VARIETY, INFLOW_MIN_PUBLIC_DEALS
from stock_policy import GROWTH_COVERAGE_HOURS
from inventory_observation import MAX_SNAPSHOT_GAP
from supply_reconciliation import pending_quantity,reserved_quantity
import meta_effects
REVIEW_SECONDS=1800
DATA_RETRY_SECONDS=60
FAILURE_RETRY_SECONDS=120
CHANGE_SECONDS=7200
SCHEMA={'type':'object','additionalProperties':False,'properties':{
 'action':{'type':'string','enum':['hold','change','rollback']},
 'parameter':{'type':'string','enum':list(DEFAULT)},
 'numeric_value':{'type':['number','null']},
 'focus_value':{'type':['string','null'],'enum':[None,*FOCUS]},
 'reason':{'type':'string'},'expected_effect':{'type':'string'},
 'evidence':{'type':'array','items':{'type':'string'},'maxItems':5}},
 'required':['action','parameter','numeric_value','focus_value','reason','expected_effect','evidence']}

def read(path,default):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return default

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));os.replace(tmp,path)

def public_sample():
    rows=[]
    with urllib.request.urlopen('https://market.ourolabs.ai/api/ledger?after=-2000',timeout=12) as r:batch=json.load(r)
    rows.extend(batch)
    for _ in range(3):
        if not batch:break
        with urllib.request.urlopen('https://market.ourolabs.ai/api/ledger?after='+str(batch[-1]['seq']),timeout=12) as r:batch=json.load(r)
        rows.extend(batch)
    return rows


def public_competition():
    """Count other shops with live stock, without storing owners or cards."""
    with urllib.request.urlopen('https://market.ourolabs.ai/api/shops',timeout=4) as response:
        shops = json.load(response)['shops']
    if not shops or any(not isinstance(shop.get('stock'), dict) for shop in shops):
        raise ValueError('unexpected public stock format')
    return Counter(sku for shop in shops if shop.get('name') != 'outseop-26510122'
                   for sku,qty in shop['stock'].items() if type(qty) is int and qty>0)


def market_watch_rows(opportunities, public):
    """Keep volume candidates outside the bounded revenue/volume review display."""
    rows = {row['sku']:row for row in opportunities}
    paid_counts = Counter()
    amounts = Counter()
    for event in public:
        if event.get('kind') != 'settle':
            continue
        item = event.get('data') or {}
        sku = item.get('sku',item.get('item'))
        amount = item.get('amount')
        if isinstance(sku,str) and type(amount) is int and amount>0:
            paid_counts[sku]+=1
            amounts[sku]+=amount
    for sku,count in paid_counts.most_common(100):
        if count<INFLOW_MIN_PUBLIC_DEALS:
            break
        rows.setdefault(sku,{'sku':sku,'public_settles':count,'sample_mean_paid':round(amounts[sku]/count)})
    return list(rows.values())

class InventoryUnavailable(ValueError):
    def __init__(self,reason,age=None):
        super().__init__(reason)
        self.reason=reason;self.age=age


def snapshot(runtime,now,public):
    path=runtime/'.market/inventory.json'
    try:
        # Read metadata from the same file descriptor as the JSON. The kit may
        # atomically replace the path while the review is collecting evidence.
        with path.open() as source:
            inv=json.load(source);source_time=os.fstat(source.fileno()).st_mtime
    except (OSError,ValueError) as error:
        raise InventoryUnavailable('inventory missing or temporarily unreadable') from error
    age=now-source_time
    if not -2<=age<=30:raise InventoryUnavailable('stale inventory',age)
    catalog=read(runtime/'.market/catalog.json',{})
    if inv.get('round')!=3 or not catalog:raise ValueError('missing Round 3 evidence')
    history=events(runtime/'logs/shop-events.jsonl');risk=inventory_risk(inv,now)
    current=active(now,runtime/'state/seller-meta-policy.json');budget=min(risk['available_to_invest'],current['window_budget'])
    settled={e['ledger']:e for e in history if e.get('event')=='settled' and e.get('ledger') is not None and now-10800<=(timestamp(e.get('at')) or 0)<=now}
    costs={e['unit']:e['cost'] for e in history if e.get('event')=='receipt_signed' and e.get('unit') and type(e.get('cost')) is int}
    tallies=Counter(e['event'] for e in history if now-10800<=(timestamp(e.get('at')) or 0)<=now)
    pc=Counter();pr=Counter()
    for r in public:
        if r.get('kind')=='settle':
            d=r['data'];sku=d.get('sku',d.get('item'));pc[sku]+=1;pr[sku]+=d.get('amount',0)
    reasons=Counter();examples=[];owned={u['sku'] for u in inv.get('units',[])}
    volume=[sku for sku,_ in pc.most_common(20)]
    revenue=[sku for sku,_ in pr.most_common(20)]
    # Interleave before the bounded model-input cut. Appending revenue after
    # volume used to silently discard every revenue-only opportunity.
    selected=dict.fromkeys(sku for pair in zip(volume,revenue) for sku in pair)
    for sku in selected:
        count=pc[sku]
        p=catalog.get(sku)
        if not p:continue
        cost=ceiling(p);why=[]
        route='inventory_or_replenishment' if sku in owned else 'new_trial'
        if cost>risk['available_to_invest']:why.append('live_cash_or_total_exposure')
        if cost>budget:why.append('current_window_budget')
        if p.get('kind')=='food' and (p.get('shelf_hours') or 0)<48:why.append('food_trial_shelf_policy')
        reasons.update(why)
        examples.append({'sku':sku,'name':p.get('name'),'kind':p.get('kind'),'public_settles':count,
                         'sample_mean_paid':round(pr[sku]/count),'cost_ceiling_with_max_surge':cost,
                         'eligible':not why,'review_route':route,'blocked_by':why,
                         'selection_sources':[source for source,ranking in [('volume',volume),('revenue',revenue)] if sku in ranking],
                         'volume_rank':volume.index(sku)+1 if sku in volume else None,
                         'revenue_rank':revenue.index(sku)+1 if sku in revenue else None,
                         'would_fail_removed_500k_cap':math.ceil(p['list_price']*.75)>500000})
    matched=[e for e in settled.values() if e.get('unit') in costs]
    portfolio=read(runtime/'state/inventory-portfolio.json',{})
    units=[u for u in inv.get('units',[]) if u.get('grade') in ('A','B','C')
           and (u.get('expires_at') is None or u['expires_at']>now)]
    observations=[timestamp(e.get('source_time')) for e in history
                  if e.get('event')=='inventory_snapshot' and e.get('round')==3]
    observed_at=max((t for t in observations if t is not None and t<=now),default=None)
    data={'at':now,'score_version':VERSION,'book':book_metrics(inv,now),
            'score_contract':BOOK_CONTRACT,'inventory_source_time':source_time,'inventory_age_seconds':max(0,age),'active_policy':current,'risk':risk,'effective_window_budget':budget,
            'operator_bounds':BOUNDS,'focus_options':FOCUS,
            'demand_observation_health':{'latest_snapshot_source_time':observed_at,
                'latest_snapshot_age_seconds':now-observed_at if observed_at is not None else None,
                'recent_observation_available':observed_at is not None and now-observed_at<=MAX_SNAPSHOT_GAP,
                'snapshot_gap_limit_seconds':MAX_SNAPSHOT_GAP,
                'note':'Missing exposure observations mean unknown coverage, not zero demand. '
                       'Forecasts include deduplicated conversion-weighted stockouts; these are not paid sales.'},
            'own_3h':{'settlements':len(settled),'revenue':sum(e.get('price',0) for e in settled.values()),
                      'known_cost_sales':len(matched),'acquisition_spread_before_holding_refunds':sum(e['price']-costs[e['unit']] for e in matched),
                      'sold_skus':dict(Counter(e.get('sku') for e in settled.values())),
                      'events':dict(tallies)},
            'market_sample':{'entries':len(public),'settles':sum(pc.values()),
                             'start':min((r['ts'] for r in public),default=None),'end':max((r['ts'] for r in public),default=None)},
            'candidate_rejection_counts':dict(reasons),
            'candidate_eligibility_counts':{'eligible':sum(e['eligible'] for e in examples),'blocked':sum(not e['eligible'] for e in examples)},
            'market_opportunities':examples[:20],
            'inventory_state':{'stock_units':len(units),'stocked_variety':len({u.get('sku') for u in units}),
                'pending_units':sum(pending_quantity(i) for i in portfolio.get('intents',[])),
                'max_units':None,'window_max_units':WINDOW_MAX_UNITS,'minimum_variety':MIN_VARIETY,
                'rolling_exploration_reserved':sum(i['cost_ceiling']*reserved_quantity(i)
                    for i in portfolio.get('intents',[]) if i['at']>now-86400 and i.get('kind')!='replenishment')},
            'opportunity_input_coverage':{'method':'deduplicated interleaved volume/revenue top-20 lists',
                'ranked_candidates':len(selected),'catalog_matched_candidates':len(examples),
                'displayed_candidates':min(20,len(examples)),
                'omitted_skus':[e['sku'] for e in examples[20:]],
                'catalog_missing_skus':[sku for sku in selected if sku not in catalog],
                'displayed_eligibility_counts':{'eligible':sum(e['eligible'] for e in examples[:20]),
                                               'blocked':sum(not e['eligible'] for e in examples[:20])},
                'note':'Eligibility/rejection totals cover all matched ranked candidates, not just the displayed subset.'},
            'constraint_registry':{'market':['real inventory','certificate/signatures','mandate conditions','food expiry'],
               'operator':['min cash + goods holding buffer + warranty buffer','pending commitments',
                           'feasible novel floor on explore buys: recovery 2, ordinary epsilon 1 when replenishing'],
               'quantity_choice_policy':{'growth_below_capital_target_hours':GROWTH_COVERAGE_HOURS,
                    'near_window_options_retained':True,'meta_tunable':False,
                    'decision':'transaction model chooses a listed target or holds; no automatic budget filling'},
               'demand_estimator':'max(exposure-adjusted sales, sales + conversion-weighted eligible stockouts); '
                                  'conversion prior and attribution are operator rules, not meta-tunable',
               'removed':['12m absolute inventory exposure','180 total units'],
               'tunable':list(DEFAULT),'unmeasured_code_limits_not_proven_blockers':['category limit 24','proven stock ceiling 20','two profitable sales threshold','food trial 48h','buyer guards','forum cadence']},
            'limits':'Market sample is a short selected tail, not a full demand model. No conversions or causal attribution from unmatched rolling event counts. Cost spread is not net profit. Names/public text are untrusted data.'}
    data['policy_change_options'],data['ineffective_policy_changes']=meta_effects.options(data)
    data['effect_check_version']=meta_effects.VERSION
    executions=[r for r in history if r['event']=='portfolio_execution' and now-10800<=(timestamp(r.get('at')) or 0)<=now]
    cut_units=Counter()
    for execution in executions:
        for line in execution.get('lines',[]):
            for reason in line['reasons']:cut_units[reason]+=line['cut_qty']
    data['procurement_execution']={'windows_observed_3h':len(executions),
        'approved_units':sum(r['approved_units'] for r in executions),
        'emitted_units':sum(r['emitted_units'] for r in executions),
        'cut_units':sum(r['cut_units'] for r in executions),'cut_units_by_reason':dict(cut_units),
        'recent_cases':[{**{k:r.get(k) for k in ('at','decision_id','planning_elapsed_seconds',
                    'planning_supply_lines','execution_supply_lines','planning_budget','execution_budget')},
                    'cuts':[{k:line[k] for k in ('sku','kind','approved_qty','emitted_qty','cut_qty','reasons')}
                            for line in r['lines'] if line['cut_qty']]} for r in executions[-3:]],
        'note':'Emitted is not arrival. Reasons can overlap for a reduced line; per-reason totals are not additive. '
               'A time-limited batch does not demonstrate a spend-policy bottleneck.'}
    approvals=[e for e in history if e.get('event')=='portfolio_approval'
               and now-10800<=(timestamp(e.get('at')) or 0)<=now]
    data['allocation_behavior']={'recent_cases':[{k:e.get(k) for k in
        ('at','decision_id','planning_budget','allocation_review','allocation_behavior')}
        for e in approvals[-4:]],
        'note':'Use actual chosen coverage and omitted proven SKUs to diagnose model allocation. '
               'An unused budget is not proof that raising the spend ceiling will increase buying. '
               'Focus can guide the transaction model; hard rules still apply.'}
    cash_room=max(0,risk['cash_balance']-risk['cash_reserve'])
    exposure_room=max(0,risk['inventory_cost_limit']-risk['stock_acquisition_cost']-risk['pending_cost_ceiling'])
    spend_limits={'cash_after_reserves':cash_room,'inventory_exposure_after_pending':exposure_room,
                  'policy_window_budget':current['window_budget']}
    data['constraint_breakdown']={**spend_limits,'effective_window_budget':budget,
        'binding_spend_limits':[key for key,value in spend_limits.items() if value==budget],
        'exposure_share':current['inventory_capital_share'],'total_stock_count_limit':None,
        'note':'Exposure share is an actual tunable constraint. Target share is a soft goal. No absolute currency or total-unit ceiling.'}
    return data

def paid_metrics(history,start,end):
    costs={e['unit']:e['cost'] for e in history if e.get('event')=='receipt_signed' and e.get('unit') and type(e.get('cost')) is int}
    paid={e['ledger']:e for e in history if e.get('event')=='settled' and e.get('ledger') is not None and start<=(timestamp(e.get('at')) or 0)<end}
    matched=[e for e in paid.values() if e.get('unit') in costs]
    return {'start':start,'end':end,'seconds':max(0,end-start),'settlements':len(paid),
            'revenue':sum(e.get('price',0) for e in paid.values()),'known_cost_sales':len(matched),
            'acquisition_spread_before_holding_refunds':sum(e['price']-costs[e['unit']] for e in matched)}


def evaluation(runtime,old,data,now):
    changed=old.get('changed_at',0);end=now-600
    if not changed or end<=changed:return None
    span=end-changed;history=events(runtime/'logs/shop-events.jsonl');baseline=old.get('baseline',{})
    previous_book=baseline.get('book',{}).get('book');current_book=data.get('book',{}).get('book')
    return {'changed_at':changed,'maturation_seconds':600,'equal_window_seconds':span,
            'before':paid_metrics(history,changed-span,changed),'after':paid_metrics(history,changed,end),
            'capital_at_change':baseline.get('risk'), 'capital_now':data['risk'],
            'book_at_change':previous_book,'book_now':current_book,
            'observed_book_change':current_book-previous_book if previous_book is not None and current_book is not None else None,
            'policy_expired':data['active_policy'].get('meta_status')!='active',
            'caution':'Observed Book change includes storage, spoilage, refunds and sales; it is not causal attribution. '
                      'Cash changes include purchases and are not Book change. Acquisition spread excludes holding and refunds.'}


def revised(config,decision):
    key=decision['parameter'];value=decision['focus_value'] if key=='focus' else decision['numeric_value']
    if key in ('window_budget','rolling_exploration_budget') and type(value) in (int,float) and float(value).is_integer():value=int(value)
    result=validate({**config,key:value})
    if key in ('target_capital_share','inventory_capital_share') and abs(result[key]-config[key])>.100001:raise ValueError('share step too large')
    if key=='epsilon' and abs(result[key]-config[key])>.050001:raise ValueError('epsilon step too large')
    if key in ('window_budget','rolling_exploration_budget') and not .5<=result[key]/config[key]<=2:raise ValueError('budget step too large')
    if result==config:raise ValueError('no change')
    return result

def review(runtime,*,model=decide_json,now=None,public=None,shadow=False):
    runtime=Path(runtime);injected_clock=now is not None;now=time.time() if now is None else now
    statepath=runtime/'state/seller-meta-review.json';policy=runtime/'state/seller-meta-policy.json'
    statepath.parent.mkdir(parents=True,exist_ok=True)
    with statepath.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read(statepath,{});old=read(policy,{})
        # Old releases wrote last_attempt before collecting evidence, so a
        # transient failure incorrectly consumed the full review interval.
        last_success=state.get('last_success',state.get('last_review',{}).get('at',0))
        next_review=state.get('next_review_at',last_success+REVIEW_SECONDS if last_success else 0)
        if not shadow and now<next_review and state.get('attempt_score_version')==VERSION and state.get('effect_check_version')==meta_effects.VERSION:
            return {'status':'cooldown','next_review_at':next_review}
        if not shadow:
            state.update(last_attempt=now,next_review_at=now+FAILURE_RETRY_SECONDS,attempt_score_version=VERSION,
                         effect_check_version=meta_effects.VERSION)
            save(statepath,state)  # also back off after a model/network failure or crash
        try:
            sample=public_sample() if public is None else public
            # Public sampling can take seconds. Do not compare a newly refreshed
            # inventory file with the timestamp captured before that network IO.
            now=now if injected_clock else time.time()
            data=snapshot(runtime,now,sample)
        except InventoryUnavailable as error:
            if shadow:raise
            retry_at=(now if injected_clock else time.time())+DATA_RETRY_SECONDS
            state.update(next_review_at=retry_at,last_failure={'at':now,'reason':error.reason,'inventory_age_seconds':error.age})
            save(statepath,state)
            record('meta_deferred',path=runtime/'logs/seller-meta-events.jsonl',
                   reason=error.reason,inventory_age_seconds=error.age,retry_at=retry_at)
            return {'status':'data_retry','reason':error.reason,'inventory_age_seconds':error.age,'next_review_at':retry_at}
        score_transition=bool(old and old.get('score_version')!=VERSION)
        capital_transition=bool(old and old.get('policy_contract_version')!=POLICY_VERSION)
        rebase=score_transition or capital_transition
        data['policy_evaluation']=None if rebase else evaluation(runtime,old,data,now)
        data['score_transition']={'rebased':score_transition,'previous_version':old.get('score_version'),
                                  'current_version':VERSION,'note':'Old scoring-policy evidence is not a causal baseline for this code change.'}
        data['capital_policy_transition']={'rebased':capital_transition,'current_version':POLICY_VERSION,
            'note':'Rebase observed outcomes after the operator changes the capital policy; do not attribute historical results to the new capacity.'}
        record('meta_snapshot',path=runtime/'logs/seller-meta-events.jsonl',snapshot=data)
        if not shadow:
            watch={'at':now,'source':'public ledger sample','window':data['market_sample'],
                   'rows':market_watch_rows(data['market_opportunities'],sample)}
            if public is None:
                try:
                    competition=public_competition()
                    watch['competition_at']=time.time()
                    watch['rows']=[{**row,'competing_shops':competition[row['sku']]} for row in watch['rows']]
                except (OSError,ValueError,KeyError,TypeError,AttributeError):
                    pass
            save(runtime/'state/seller-meta-market.json',watch)
        decision=model('seller meta policy reviewer',{
            'instruction':'Review whether the operating policy blocks valuable market opportunities. '
            'Inspect allocation_behavior for repeated near-window targets, omitted proven sellers and '
            'novelty crowding out replenishment while capital is available. Use focus guidance when useful; '
            'do not propose budget increases as a cure for an unused budget or a source-level review failure. '
            'Prefer expansion when confirmed paid demand, margins and capital support it; identify rejection reasons '
            'even for products the transaction model never sees. Change only ONE tunable parameter, or hold. '
            'Eligible candidates are NOT blocked: they await transaction-model review. Already-stocked SKUs route '
            'to inventory/replenishment review and are not rejected new opportunities. Only call a constraint binding '
            'when an actual rejection reason demonstrates it; unmeasured code limits are not observed bottlenecks. '
            'Do not change code, mandatory cash reserves, contracts or pending reservations. Exposure share and both budgets '
            'are operator-authorized tunables: no absolute inventory currency or global stock-count cap remains. Do not tighten merely because '
            'cash falls after procurement. Optimize Book under score_contract, not Cash. Buying at cost is Book-neutral; '
            'use observed Book changes after refunds, storage and spoilage. Revenue or cheap trade count alone does not prove Book growth. '
            'A candidate is NOT a purchase authorization. Rollback uses the previous reviewed policy. '
            'Report code-level bottlenecks in reason even when no runtime parameter can fix them. '
            'Numeric step limits: both budgets x0.5..x2, exposure/target share +/-0.10, epsilon +/-0.05. '
            'For change, select an exact entry from policy_change_options. ineffective_policy_changes are excluded. '
            'Never propose an excluded/no-effect edit: target edits remain soft guidance and can be clipped by exposure share; '
            'epsilon edits do nothing when recovery already forces exploration. Window budget is not available capital. '
            'Only positive effective_budget_delta increases spend headroom. Guidance-only changes alter the model prompt, '
            'not physical capacity; do not describe them as unlocking funds or guaranteed purchases. '
            'Use constraint_breakdown and procurement_execution to distinguish budget, window time, quantity and target cuts. '
            'Model approval, emitted requests and confirmed arrivals are different. Do not treat a time cut as a cash cap. '
            'All proposed changes and rollbacks are checked again against refreshed inventory before application. '
            'Allow at least two hours and 20 paid sales since the prior change before another change. '
            'Use policy_evaluation for equal-length mature pre/post windows and capital snapshots; '
            'overlapping market or product changes still prevent causal claims. '
            'Insufficient evidence, missing data or overlapping changes favor hold; do not invent outcomes.',
            'snapshot':data,'previous_review':state.get('last_review'),
            'seconds_since_change':now-old.get('changed_at',0)},SCHEMA)
        proposed_decision=dict(decision)
        config={k:data['active_policy'][k] for k in DEFAULT};status='held';new=config;change_effect=None
        changed_at=now if rebase else old.get('changed_at',0)
        if decision.get('action') in ('change','rollback'):
            candidate=(upgrade(old.get('previous_config',config),old.get('policy_contract_version')) if decision['action']=='rollback'
                       else revised(config,decision))
            try:
                effect_data=data if injected_clock else snapshot(runtime,time.time(),sample)
                change_effect=meta_effects.evaluate(effect_data,config,candidate)
                change_effect['inventory_source_time']=effect_data['inventory_source_time']
            except InventoryUnavailable as error:
                change_effect={'has_effect':False,'no_effect_reasons':[error.reason],'check_status':'data_unavailable'}
            if not change_effect['has_effect']:
                status='effect_check_deferred' if change_effect.get('check_status')=='data_unavailable' else 'ineffective_change_blocked'
                decision={**decision,'action':'hold','numeric_value':None,'focus_value':None,
                          'reason':'No verified current execution effect: '+', '.join(change_effect['no_effect_reasons']),
                          'expected_effect':'Retain the current policy; no additional spend capacity is demonstrated.'}
            else:
                mature_sales=len({e['ledger'] for e in events(runtime/'logs/shop-events.jsonl') if e.get('event')=='settled' and e.get('ledger') is not None and changed_at<=(timestamp(e.get('at')) or 0)<=now-600})
                if old and (now-changed_at<CHANGE_SECONDS or mature_sales<20):status='evaluation_pending'
                else:
                    new=candidate;status='rollback' if decision['action']=='rollback' else 'changed'
        elif decision.get('action')!='hold':raise ValueError('unknown meta action')
        data['proposed_change_effect']=change_effect
        data['proposed_policy_decision']=proposed_decision
        if not shadow:
            version=uuid.uuid4().hex if rebase or status in ('changed','rollback') else old.get('version',uuid.uuid4().hex)
            applied_at=now if injected_clock else time.time()
            baseline={'at':applied_at,'risk':data['risk'],'book':data['book'],'own_3h':data['own_3h']} if not old or rebase or status in ('changed','rollback') else old.get('baseline')
            payload={'schema_version':2,'policy_contract_version':POLICY_VERSION,'score_version':VERSION,'version':version,'at':applied_at,'expires_at':applied_at+14400,
                     'baseline':baseline,
                     'changed_at':applied_at if not old or rebase or status in ('changed','rollback') else changed_at,
                     'config':new,'previous_config':config if new!=config else upgrade(old.get('previous_config',config),old.get('policy_contract_version'))}
            save(policy,payload);state['last_review']={'at':applied_at,'status':status,'decision':decision,
                'proposed_decision':proposed_decision,'change_effect':change_effect,'version':version}
        with sqlite3.connect(runtime/'state/seller-meta.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, at REAL, status TEXT, decision TEXT, before_config TEXT, after_config TEXT, evidence TEXT)')
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,now,'shadow' if shadow else status,
                json.dumps(decision,ensure_ascii=False),json.dumps(config),json.dumps(new),json.dumps(data,ensure_ascii=False)))
        record('meta_review',path=runtime/'logs/seller-meta-events.jsonl',status='shadow' if shadow else status,
               decision=decision,proposed_decision=proposed_decision,change_effect=change_effect,
               effect_check_version=meta_effects.VERSION,before=config,after=new,market_writes=False)
        if not shadow:
            state.update(last_success=applied_at,next_review_at=applied_at+REVIEW_SECONDS)
            state.pop('last_failure',None);save(statepath,state)
        return {'status':'shadow' if shadow else status,'decision':decision,'proposed_decision':proposed_decision,
                'change_effect':change_effect,'before':config,'after':new}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,default=Path.cwd());parser.add_argument('--loop',action='store_true');parser.add_argument('--shadow',action='store_true');args=parser.parse_args();runtime=args.runtime.resolve();os.chdir(runtime)
    while True:
        try:print(json.dumps(review(runtime,shadow=args.shadow),ensure_ascii=False),flush=True)
        except Exception as e:
            record('meta_error',path=runtime/'logs/seller-meta-events.jsonl',error=type(e).__name__,detail=str(e)[:300]);print('Meta held: '+type(e).__name__,flush=True)
        if not args.loop:break
        time.sleep(60)
