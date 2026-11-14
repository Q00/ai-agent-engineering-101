"""Evidence-based seller procurement. Durable spend; never retry unknown POSTs."""
import argparse,fcntl,importlib.util,json,math,os,sqlite3,time
from pathlib import Path
from network_reads import retry_read
from seller_market import snapshot
from market_intel import read_json

ROOT=Path(__file__).resolve().parent
PENDING_HOLD_SECONDS=300
SUPPLY_INTERVAL_SECONDS=10.05

def supply_interval_wait(last_success, now=None, interval=SUPPLY_INTERVAL_SECONDS):
    """Return the remaining successful-supply cooldown.

    The supply endpoint accepts roughly one successful request every ten
    seconds. Retrying after the generic one-second loop delay wastes a network
    round trip on a predictable 429 and reduces breadth within the short
    supply window.
    """
    now=time.time() if now is None else now
    if not isinstance(last_success,(int,float)) or last_success<=0:return 0.0
    return max(0.0,last_success+max(0.0,float(interval))-now)

def settings():return json.loads((ROOT/'procurement_policy.json').read_text())

def warranty_risk_reserve(amounts, policy=None):
    """Reserve a conservative tail estimate instead of every warranty at once.

    The market can refund the full price of any warranted good, so the largest
    single claim is always covered.  Freezing every active warranty assumes a
    100% simultaneous break rate and eventually stops a successful shop from
    restocking.  The configured hazard deliberately exceeds our mature public
    breakage cohort; four standard deviations retain a wide tail buffer while
    allowing accumulated cash to keep earning Book and Revenue.
    """
    policy=policy or settings()
    clean=[int(value) for value in amounts
           if isinstance(value,(int,float)) and not isinstance(value,bool) and value>0]
    if not clean:return 0
    total=sum(clean)
    hazard=max(.01,min(1.0,float(policy.get('warranty_reserve_hazard',.15))))
    sigma=max(0.0,min(10.0,float(policy.get('warranty_reserve_sigma',4.0))))
    expected=hazard*total
    deviation=math.sqrt(hazard*(1-hazard)*sum(value*value for value in clean))
    return min(total,max(max(clean),math.ceil(expected+sigma*deviation)))

def observed_warranty_hazard(report_path, policy=None, now=None):
    """Use a fresh, mature official cohort without trusting a point estimate.

    The board's cumulative ``claims``/``broken`` counters are authoritative.
    Until the cohort is large and the local official snapshot is fresh, the
    original conservative prior remains in force.  A 99.9% Wilson upper bound,
    enlarged again by a safety multiplier and an absolute floor, prevents a
    short lucky run from releasing warranty capital too aggressively.
    """
    policy=policy or settings();now=time.time() if now is None else now
    prior=max(.01,min(1.0,float(policy.get('warranty_reserve_hazard',.15))))
    try:report=read_json(Path(report_path),{})
    except (OSError,ValueError,TypeError):return prior
    observed_at=report.get('at') if isinstance(report,dict) else None
    max_age=max(1.0,float(policy.get('warranty_hazard_max_age_seconds',90)))
    if not isinstance(observed_at,(int,float)) or now-observed_at<0 or now-observed_at>max_age:
        return prior
    shops=((report.get('board') or {}).get('shops') or [])
    own=next((row for row in shops if str(row.get('name'))=='25512088'),None)
    if not isinstance(own,dict):return prior
    claims,broken=own.get('claims'),own.get('broken')
    minimum=max(1,int(policy.get('warranty_hazard_min_claims',200)))
    if (not isinstance(claims,(int,float)) or isinstance(claims,bool) or
            not isinstance(broken,(int,float)) or isinstance(broken,bool) or
            claims<minimum or broken<0 or broken>claims):return prior
    z=max(0.0,float(policy.get('warranty_hazard_wilson_z',3.29)))
    n=float(claims);p=float(broken)/n;z2=z*z
    upper=(p+z2/(2*n)+z*math.sqrt(p*(1-p)/n+z2/(4*n*n)))/(1+z2/n)
    multiplier=max(1.0,float(policy.get('warranty_hazard_safety_multiplier',1.5)))
    floor=max(.01,min(prior,float(policy.get('warranty_hazard_floor',.08))))
    return min(prior,max(floor,upper*multiplier))

def active_warranty_amounts(referrals, policy=None, now=None):
    """Reconstruct still-live legacy warranties plus the new six-hour cohort.

    Before ``fixed_warranty_hours_at`` the seller expanded warranty duration
    from the count of earlier settled sales for the same SKU.  The duration was
    not stored with the warranty row, so a six-hour lookback silently omitted
    valid 24/48/72-hour liabilities after the expansion code was removed.
    Replaying the same chronological thresholds restores those liabilities.
    Official refunds are not reconciled into the local flag, so already-refunded
    rows may remain reserved until expiry; that bias is deliberately safe.
    """
    policy=policy or settings();now=time.time() if now is None else now
    fixed=float(policy.get('fixed_warranty_hours_at',0) or 0)
    rows=referrals.execute('''
        SELECT w.amount,w.issued,s.sku,s.task,s.settled
        FROM warranties w JOIN sales s ON s.task=w.task
        WHERE s.refunded=0
        ORDER BY s.sku,COALESCE(s.settled,w.issued),s.task''').fetchall()
    previous={};active=[]
    for amount,issued,sku,task,settled in rows:
        prior=previous.get(sku,0);previous[sku]=prior+1
        if not isinstance(amount,(int,float)) or amount<=0 or not isinstance(issued,(int,float)):
            continue
        if fixed and issued>=fixed:hours=6
        elif prior>=30:hours=72
        elif prior>=15:hours=48
        elif prior>=5:hours=24
        else:hours=6
        if issued+hours*3600>now:active.append(int(amount))
    return active

def reserve_cash(inv,exposure=0):
    return max(settings()['cash_floor'],inv.get('bankrupt_line',500000))+exposure

def capital_available(policy,inv,exposure=0,pending=0):
    """Scale deployable capital with wealth while preserving cash and liabilities."""
    inventory_cost=sum(u.get('cost',0) for u in inv.get('units',[]))
    capital=max(0,inv.get('balance',0))+inventory_cost
    inventory_ceiling=math.floor(capital*policy.get('max_inventory_share',.7))
    liquid=max(0,inv.get('balance',0)-reserve_cash(inv,exposure)-pending)
    capacity=max(0,inventory_ceiling-inventory_cost-pending)
    return max(0,min(liquid,capacity))

def breadth_window_blocks(rows, opening, inventory, target):
    """Avoid duplicate SKU buys while inventory reflection can lag.

    ``allocation_plan`` already fills the evidenced need for one supply cadence
    in its first successful call.  A second call in the same short window is a
    stale-inventory duplicate, wastes a rate-limited slot, and can exceed the
    per-SKU cap. The journal is authoritative regardless of breadth target.
    """
    return {r['sku'] for r in rows
            if r['status']=='paid' and r['created']>=opening and r['sku']}

def choose(policy,catalog,spent,reserved,balance,exposure,turn,blocked=()):
    # spent is current inventory principal, not lifetime procurement spend.
    remaining=min(policy['budget']-spent-reserved,balance-policy['cash_floor']-exposure)
    priorities=policy['priority'];ordered=priorities[turn%len(priorities):]+priorities[:turn%len(priorities)]
    blocked=set(blocked)|set(policy.get('blocked_skus',()))
    for sku in ordered:
        product=catalog.get(sku,{})
        if sku in blocked or product.get('kind')!='goods':continue
        upper=math.ceil(product['list_price']*.75)
        if upper<=remaining:return sku,upper
    return None

def allocation_plan(ranked, inventory, available, blocked=(), available_slots=None, aggression=1.0, policy=None):
    """Allocate by sell-through, holdings, next-window need and contribution.

    Buy small, evidence-backed lots across distinct SKUs. Quantity is bounded
    by demand, per-SKU unit/capital caps, available cash, and exposure slots.
    """
    policy=policy or settings();holding={};holding_cost={}
    for unit in inventory.get('units',[]):
        sku=unit['sku'];holding[sku]=holding.get(sku,0)+1
        holding_cost[sku]=holding_cost.get(sku,0)+unit.get('cost',0)
    blocked=set(blocked)|set(policy.get('blocked_skus',()))
    blocked|={sku for sku in policy.get('blocked_until_zero_skus',()) if holding.get(sku,0)>0}
    # Concentration is a share of total wealth, not of cash left after warranty
    # reserves. ``available`` still strictly caps spending below; using it as
    # the denominator made a larger safety reserve paradoxically classify a
    # single sub-10%-of-wealth unit as over-concentrated.
    portfolio_capital=sum(holding_cost.values())+max(0,inventory.get('balance',available))
    ranked_by_sku={row.get('sku'):row for row in ranked if row.get('sku')}
    recent_goods={row.get('sku') for row in ranked
                  if row.get('kind')=='goods' and
                  row.get('own_sales_1h',0)>=policy.get('cold_goods_min_own_sales_1h',1)}
    cold_goods_cost=sum(unit.get('cost',0) for unit in inventory.get('units',[])
                        if (unit.get('kind') or ranked_by_sku.get(unit.get('sku'),{}).get('kind'))=='goods'
                        and unit.get('sku') not in recent_goods)
    cold_goods_limit=portfolio_capital*policy.get('max_cold_goods_capital_share',1)
    cold_goods_frozen=cold_goods_cost>cold_goods_limit
    choices=[]
    for row in ranked:
        if row.get('sku') in blocked or row.get('expected_net',0)<=0:continue
        cold_safe_trial=False
        if row.get('kind')=='goods':
            min_samples=policy.get('min_goods_a_profit_samples',0)
            min_probability=policy.get('min_goods_a_profit_probability',0)
            if (row.get('dominant_grade_samples',0)<min_samples or
                    row.get('dominant_grade_profit_probability',0)<min_probability):
                continue
            cold_safe_trial=(cold_goods_frozen and not holding.get(row['sku']) and
                row.get('sales_1h',0)>=policy.get('cold_safe_trial_sales_1h',math.inf) and
                row.get('dominant_grade_profit_probability',0)>=
                    policy.get('cold_safe_trial_profit_probability',1.1))
        # Supply calls and cash are both scarce.  Stop opening additional goods
        # trials while too much principal is already tied up in goods that have
        # not sold from our shop in the last hour. Proven goods keep restocking,
        # and trials reopen automatically as the cold cohort sells down.
        if (cold_goods_frozen and row.get('kind')=='goods' and
                row.get('own_sales_1h',0)<policy.get('cold_goods_min_own_sales_1h',1) and
                not cold_safe_trial):
            continue
        velocity=row.get('sales_1h',0)+row.get('sales_6h',0)/6
        distinct=max(0,row.get('distinct_buyers_6h',0))
        if row.get('sales_1h',0)<policy.get('require_sales_1h',0):continue
        if distinct<policy.get('min_distinct_buyers_6h',0):continue
        inquiry_cap=min(policy.get('max_anonymous_inquiries',10),max(2,distinct*2))
        trusted_inquiries=min(row.get('own_inquiries_6h',0),inquiry_cap)
        trusted_stockouts=min(row.get('own_stockouts_6h',0),max(2,distinct*2))
        unmet=trusted_stockouts*.5+trusted_inquiries*.2
        forecast=(velocity+unmet)*row.get('estimated_sale_likelihood',0)*max(1.0,float(aggression))
        need=max(0,math.ceil(forecast)-holding.get(row['sku'],0))
        is_food=row.get('kind')=='food'
        if is_food:
            unit_cap=policy.get('max_food_units_per_sku',2)
            if row.get('sales_1h',0)>=policy.get('fast_food_sales_1h',math.inf):
                unit_cap=max(unit_cap,policy.get('max_fast_food_units_per_sku',unit_cap))
            if row.get('sales_1h',0)>=policy.get('ultra_fast_food_sales_1h',math.inf):
                unit_cap=max(unit_cap,policy.get('max_ultra_fast_food_units_per_sku',unit_cap))
        else:
            unit_cap=policy.get('max_units_per_sku',3)
            # Preserve the normal three-unit diversity cap. Only a SKU that
            # actually sold through two full normal lots in the last hour may
            # hold a slightly deeper cohort between supply windows.
            if (row.get('sku') not in policy.get('blocked_until_zero_skus',())
                    and row.get('own_sales_1h',0)>=policy.get('fast_goods_sales_1h',math.inf)):
                unit_cap=max(unit_cap,policy.get('max_fast_goods_units_per_sku',unit_cap))
        # Portfolio size is bounded by available capital and evidence-backed
        # SKU breadth.  A fixed global unit count used to strand growing cash.
        sku_ceiling=unit_cap
        need=min(need,max(0,sku_ceiling-holding.get(row['sku'],0)))
        if cold_safe_trial:
            need=min(need,policy.get('max_cold_safe_trial_units',1))
        trial_units=(policy.get('initial_food_trial_units',2) if is_food else
                     policy.get('initial_trial_units',policy.get('min_trial_units',2)))
        # A single unit above the portfolio share is not a "small trial" and
        # recreates the historical concentrated-stock failure. Evidence may
        # scale the portfolio, but it never waives this wealth-relative cap.
        capital_ceiling=portfolio_capital*policy.get('max_sku_capital_share',1)
        capital_room=max(0,capital_ceiling-holding_cost.get(row['sku'],0))
        need=min(need,int(capital_room//max(1,row['upper_cost'])))
        if need<=0:continue
        evidence=dict(row,trusted_inquiries_6h=trusted_inquiries,
                      trusted_stockouts_6h=trusted_stockouts,sku_inventory_ceiling=sku_ceiling,
                      cold_goods_cost=cold_goods_cost,cold_goods_limit=cold_goods_limit,
                      cold_goods_frozen=cold_goods_frozen,cold_safe_trial=cold_safe_trial)
        is_new=not holding.get(row['sku'])
        breadth_target=max(0,policy.get('breadth_target_skus',0) or 0)
        breadth_priority=0 if len(holding)<breadth_target and is_new else 1
        diversity=policy.get('new_sku_bonus',1) if is_new else 1
        # Official score contribution remains primary, with a sub-linear
        # capital-risk adjustment. This preserves high-value goods without
        # letting a handful of million-credit units consume an entire window.
        risk_adjusted=row.get('score',0)/max(1,row['upper_cost'])**.75
        choices.append((breadth_priority,-(risk_adjusted*diversity),
                        row['sku'],need,evidence))
    # Each candidate is bounded by condition-specific per-SKU caps. When no
    # explicit test/canary slot limit is supplied, capital and those caps
    # determine the total portfolio instead of an arbitrary 250 units.
    plan=[];slots=sum(row[3] for row in choices) if available_slots is None else available_slots
    for _,_,sku,need,row in sorted(choices):
        # Official supply API accepts at most 20 units per call. Remaining
        # evidenced need is purchased on subsequent inventory refreshes.
        qty=min(policy.get('max_lot_units',20),need,int(available//row['upper_cost']),max(0,slots))
        if qty<=0:continue
        plan.append({'sku':sku,'qty':qty,'upper_unit_cost':row['upper_cost'],
                     'capital':qty*row['upper_cost'],'evidence':row})
        available-=qty*row['upper_cost']
        slots-=qty
    return plan

def supply_result(status, body, upper):
    """Classify a received response; no response is handled as uncertain."""
    if status>=500:return 'uncertain',None
    if status!=200:return 'refused',None
    cost=body.get('total') if isinstance(body,dict) else None
    # HTTP 200 is an accepted supply purchase.  The market may legitimately
    # charge above our estimate because grade and surge are drawn server-side;
    # treating that accepted purchase as unknown used to stop all procurement.
    if type(cost) is not int or cost<=0:return 'reconcile',None
    return 'paid',cost

def preserve_stale_pending(db,campaign,now=None,hold=PENDING_HOLD_SECONDS):
    """Release the global pipeline without ever repeating an unknown POST.

    A fresh unknown request keeps its capital reserved and its SKU blocked.
    Once it is older than the reconciliation window it becomes ``preserved``:
    it is never retried, but unrelated evidence-backed products may be bought.
    """
    now=time.time() if now is None else now
    rows=db.execute("SELECT id,sku,upper_cost,created,detail FROM purchases WHERE campaign=? AND status IN ('sent','uncertain')",
                    (campaign,)).fetchall()
    stale=[r for r in rows if now-r['created']>=hold]
    with db:
        for row in stale:
            detail=json.dumps({'previous':row['detail'],'resolution':'preserved_no_retry',
                               'resolved_at':now},sort_keys=True)
            db.execute("UPDATE purchases SET status='preserved',detail=? WHERE id=?",(detail,row['id']))
    return stale

def own_registration(body, name='25512088'):
    rows=body.get('shops',[]) if isinstance(body,dict) else body
    me=next((s for s in rows if s.get('name')==name),None)
    if not me:raise ConnectionError('own shop absent from registry snapshot')
    return me

def own_board_registration(body,name='25512088'):
    """Return the live card URL from the authoritative leaderboard snapshot."""
    rows=body.get('shops',[]) if isinstance(body,dict) else []
    me=next((s for s in rows if str(s.get('name'))==str(name)),None)
    url=(me.get('card') or {}).get('url') if me else None
    if not url:raise ConnectionError('own live shop card absent from board snapshot')
    return {'name':name,'url':url}

def retry_join(operation, report=None, attempts=None, sleep=time.sleep):
    def guarded():
        try:return operation()
        except SystemExit as exc:raise ConnectionError('market join/card check failed') from exc
    return retry_read(guarded,attempts=attempts,report=report,sleep=sleep)

def join_current_registration(market,key,get_registration):
    """Refresh the seller URL on every retry; tunnels change after restarts."""
    me=get_registration()
    return market.join('shop','25512088',key,me['url'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market');args=ap.parse_args()
    state=Path(args.state).resolve();policy=settings();state.mkdir(exist_ok=True)
    lock=(state/'procurement.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another campaign worker owns the lock')
    db=sqlite3.connect(state/'procurement.sqlite3');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY,campaign TEXT,sku TEXT,upper_cost INTEGER,cost INTEGER,status TEXT,created REAL,detail TEXT)');db.commit()
    spec=importlib.util.spec_from_file_location('kit',ROOT.parents[2]/'scripts/market_kit.py');kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)
    assert (ROOT/'shop_key.pem').exists()
    key=kit.load_key(ROOT/'shop_key.pem');market=kit.Market('https://market.ourolabs.ai',os.environ.get('MARKET_OWNER'))
    def log(**data):print(json.dumps(dict(ts=time.time(),**data),ensure_ascii=False),flush=True)
    registration_attempt=0
    def get_registration():
        # The board and shop registry are eventually consistent and either one
        # may briefly retain the previous tunnel.  Alternate the preferred
        # source after a failed join so a fresh URL is never hidden by a stale
        # but otherwise valid record.
        nonlocal registration_attempt
        sources=(('/api/shops',own_registration),('/api/board',own_board_registration))
        if registration_attempt%2:sources=tuple(reversed(sources))
        registration_attempt+=1
        error=None
        for endpoint,parser in sources:
            try:
                response=market.c.get(market.base+endpoint);response.raise_for_status()
                return parser(response.json())
            except Exception as exc:error=exc
        raise ConnectionError('own live shop absent from both registries') from error
    retry_join(lambda:join_current_registration(market,key,get_registration),report=log)
    catalog=json.loads((state/'catalog.json').read_text());blocked=set();window=None
    def log(**data):print(json.dumps(dict(ts=time.time(),**data),ensure_ascii=False),flush=True)
    log(event='started',campaign=policy['campaign'],capital_policy=policy['budget_basis'])
    last_supply_success=0.0
    while True:
        stale=preserve_stale_pending(db,policy['campaign'])
        for row in stale:
            log(event='uncertain_preserved',purchase_id=row['id'],sku=row['sku'],
                reserved=row['upper_cost'],action='never_retry_continue_other_skus')
        rows=db.execute('SELECT * FROM purchases WHERE campaign=?',(policy['campaign'],)).fetchall()
        spent=sum(r['cost'] or 0 for r in rows if r['status']=='paid');pending=sum(r['upper_cost'] for r in rows if r['status'] in ('sent','uncertain'))
        pending_skus={r['sku'] for r in rows if r['status'] in ('sent','uncertain')}
        if pending:
            blocked.update(pending_skus)
            log(event='uncertain_isolated',spent=spent,reserved=pending,
                blocked_skus=sorted(pending_skus),action='continue_other_skus')
        def get_inventory():
            response=market.call('GET','/api/inventory','shop');response.raise_for_status();return response.json()
        inv=retry_read(get_inventory, attempts=None, report=log)
        if inv.get('round')!=3:log(event='wrong_round');break
        exposure=0
        reserve_policy=dict(policy)
        reserve_policy['warranty_reserve_hazard']=observed_warranty_hazard(
            state/'market-report.json',policy)
        with sqlite3.connect(state/'referrals.sqlite3') as referrals:
            if referrals.execute("SELECT 1 FROM sqlite_master WHERE name='warranties'").fetchone():
                amounts=active_warranty_amounts(referrals,policy)
                exposure=warranty_risk_reserve(amounts,reserve_policy)
        with sqlite3.connect(state/'referrals.sqlite3') as referrals:
            exposure+=referrals.execute('SELECT COALESCE(SUM(credit),0) FROM members').fetchone()[0]
            exposure+=referrals.execute('SELECT COALESCE(SUM(reward),0) FROM sales WHERE awarded=0 AND refunded=0').fetchone()[0]
        inventory_cost=sum(u['cost'] for u in inv['units'])
        restock=inv.get('restock',{})
        if window!=restock.get('opens_at'):window=restock.get('opens_at');blocked.clear()
        opening=restock.get('opens_at')
        strategy=read_json(state/'strategy.json',{})
        if not isinstance(opening,(int,float)):
            log(event='missing_supply_window');time.sleep(20);continue
        if (strategy.get('opens_at')!=opening or strategy.get('version')!=3) and time.time()>=opening-180:
            try:strategy=snapshot(state,catalog,opening,market.base) or {}
            except Exception as exc:
                log(event='selection_retry',error=type(exc).__name__);time.sleep(20);continue
        if strategy.get('opens_at')!=opening or strategy.get('version')!=3:
            log(event='waiting_selection',select_at=opening-180,spent=spent)
            time.sleep(min(20,max(1,opening-180-time.time())));continue
        with sqlite3.connect(state/'referrals.sqlite3') as referrals:
            own_sales_1h={row[0]:row[1] for row in referrals.execute(
                'SELECT sku,COUNT(*) FROM sales WHERE settled>? AND refunded=0 GROUP BY sku',
                (time.time()-3600,))}
        ranked=[dict(row,kind=catalog.get(row.get('sku'),{}).get('kind'),
                     own_sales_1h=own_sales_1h.get(row.get('sku'),0))
                for row in (strategy.get('candidates') or strategy.get('top10',[]))]
        if not ranked:
            log(event='no_qualified_top10');time.sleep(20);continue
        ranked=[r for r in ranked if r.get('expected_net',0)>0]
        available=capital_available(policy,inv,exposure,pending)
        slots=None
        # Starting capital is 5M. More accumulated cash funds more demand
        # windows, while capital_available still preserves cash/liabilities.
        aggression=max(1.0,min(policy.get('max_demand_windows',4),inv.get('balance',0)/3_000_000))
        runtime_policy=dict(policy)
        target=(strategy.get('competition_breadth') or {}).get('top10_min_skus')
        if type(target) is int and target>0:runtime_policy['breadth_target_skus']=target
        window_blocks=breadth_window_blocks(rows,opening,inv,target)
        plan=allocation_plan(ranked,inv,available,set(blocked)|window_blocks,
                             slots,aggression,runtime_policy)
        if not plan:
            if blocked:time.sleep(10);continue
            log(event='evidence_or_capacity_wait',lifetime_spent=spent,inventory_cost=inventory_cost,
                deployable_capital=available,available_slots='capital_and_per_sku_bounded',protected_liabilities=exposure,
                demand_windows=aggression)
            time.sleep(60);continue
        if not restock.get('open'):
            log(event='waiting_supply',opens_at=restock.get('opens_at'),spent=spent)
            time.sleep(min(20,max(2,restock.get('opens_at',time.time()+20)-time.time())));continue
        selected=plan[0];sku=selected['sku'];qty=selected['qty'];upper=selected['capital']
        cooldown=supply_interval_wait(last_supply_success)
        if cooldown:
            closes=restock.get('closes_at')
            if isinstance(closes,(int,float)) and time.time()+cooldown>=closes:
                time.sleep(max(1,min(5,closes-time.time())))
                continue
            time.sleep(cooldown)
        with db:
            cursor=db.execute('INSERT INTO purchases(campaign,sku,upper_cost,status,created) VALUES(?,?,?,?,?)',(policy['campaign'],sku,upper,'sent',time.time()));rid=cursor.lastrowid
        try:
            result=market.call('POST','/api/supply','shop',json={'sku':sku,'qty':qty})
        except Exception as exc:
            with db:db.execute("UPDATE purchases SET status='uncertain',detail=? WHERE id=?",(type(exc).__name__,rid))
            blocked.add(sku)
            log(event='uncertain_isolated',sku=sku,purchase_id=rid,
                action='never_retry_continue_other_skus');time.sleep(5);continue
        body=result.json() if result.status_code==200 else {}
        outcome,cost=supply_result(result.status_code,body,upper)
        if outcome=='paid':
            last_supply_success=time.time()
            if cost is None:
                log(event='unexpected_cost_stop',sku=sku);break
            with db:db.execute("UPDATE purchases SET status='paid',cost=?,detail=? WHERE id=?",(cost,json.dumps({'balance':body.get('balance')}),rid))
            log(event='purchased',sku=sku,qty=qty,cost=cost,spent=spent+cost,balance=body.get('balance'),
                estimated_upper=upper,cost_overrun=max(0,cost-upper),
                allocation={'holding':sum(u['sku']==sku for u in inv['units']),
                            'sales_1h':selected['evidence'].get('sales_1h'),
                            'next_window_need':qty,'expected_net':selected['evidence'].get('expected_net'),
                            'demand_windows':aggression})
        elif outcome in ('uncertain','reconcile'):
            with db:db.execute("UPDATE purchases SET status='uncertain',detail=? WHERE id=?",(str(result.status_code),rid))
            blocked.add(sku)
            log(event='uncertain_isolated' if outcome=='uncertain' else 'reconciliation_isolated',
                sku=sku,purchase_id=rid,status=result.status_code,
                action='never_retry_continue_other_skus');time.sleep(5);continue
        else:
            with db:db.execute("UPDATE purchases SET status='refused',detail=? WHERE id=?",(str(result.status_code),rid))
            log(event='refused',sku=sku,status=result.status_code,reason=result.text[:180])
            if result.status_code==402:break
            if result.status_code!=429:blocked.add(sku)
            wait=3
            if result.status_code==429:
                try:wait=max(1,min(60,float(result.json().get('wait',3))+.25))
                except (TypeError,ValueError):pass
            time.sleep(wait)
        time.sleep(1)

if __name__=='__main__':main()
