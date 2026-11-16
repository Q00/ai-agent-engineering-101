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

def capacity_recheck_wait(restock):
    """Recheck freed cash quickly only while the short supply window is open."""
    return 10 if isinstance(restock,dict) and restock.get('open') else 60

def settings():return json.loads((ROOT/'procurement_policy.json').read_text())

def warranty_risk_reserve(amounts, policy=None):
    """Reserve a conservative tail estimate instead of every warranty at once.

    The market can refund the full price of any warranted good, so the largest
    single claim is always covered.  Freezing every active warranty assumes a
    100% simultaneous break rate and eventually stops a successful shop from
    restocking.  The configured hazard deliberately exceeds our mature public
    breakage cohort; the configured high-sigma tail buffer retains several
    simultaneous large-claim equivalents while allowing accumulated cash to
    keep earning Book and Revenue.
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
    report_path=Path(report_path)
    try:report=read_json(report_path,{})
    except (OSError,ValueError,TypeError):return prior
    observed_at=report.get('at') if isinstance(report,dict) else None
    max_age=max(1.0,float(policy.get('warranty_hazard_max_age_seconds',90)))
    shops=((report.get('board') or {}).get('shops') or [])
    own=next((row for row in shops if str(row.get('name'))=='25512088'),None)
    fresh=(isinstance(observed_at,(int,float)) and 0<=now-observed_at<=max_age)
    claims,broken=(own.get('claims'),own.get('broken')) if isinstance(own,dict) else (None,None)
    minimum=max(1,int(policy.get('warranty_hazard_min_claims',200)))
    complete=(fresh and isinstance(claims,(int,float)) and not isinstance(claims,bool) and
              isinstance(broken,(int,float)) and not isinstance(broken,bool) and
              claims>=minimum and 0<=broken<=claims)
    # The report and the scheduled strategy snapshot update independently.
    # During their short hand-off the report can be fresh but contain an
    # incomplete own-shop row. The strategy snapshot has already persisted the
    # same official GET board in market_intel, so use that fresh copy only when
    # the report cohort is unusable instead of restoring the 15% prior for a
    # full loop and losing a third of the short supply window.
    if not complete:
        intel_path=report_path.parent/'market_intel.sqlite3'
        try:
            with sqlite3.connect('file:'+str(intel_path.resolve())+'?mode=ro',uri=True) as intel:
                source=intel.execute("SELECT ts,body FROM sources WHERE name='board'").fetchone()
            if source and 0<=now-source[0]<=max_age:
                board=json.loads(source[1]);rows=board.get('shops',[]) if isinstance(board,dict) else []
                own=next((row for row in rows if str(row.get('name'))=='25512088'),None)
                claims,broken=(own.get('claims'),own.get('broken')) if isinstance(own,dict) else (None,None)
                complete=(isinstance(claims,(int,float)) and not isinstance(claims,bool) and
                          isinstance(broken,(int,float)) and not isinstance(broken,bool) and
                          claims>=minimum and 0<=broken<=claims)
        except (OSError,ValueError,TypeError,sqlite3.Error):
            pass
    if not complete:return prior
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
    Legacy refunds from before official broken-event ingestion may remain
    reserved until expiry; that backward-looking bias is deliberately safe.
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

def own_sales_evidence(referrals, now=None):
    """Return refund-aware local sell-through and realized Book contribution.

    A refunded unit is gone and its sale price is returned, so its realized
    contribution is negative principal, not zero.  Counting only successful
    rows made a fast but failure-prone SKU look permanently profitable.
    """
    now=time.time() if now is None else now
    recent={row[0]:row[1] for row in referrals.execute(
        'SELECT sku,COUNT(*) FROM sales WHERE settled>? AND refunded=0 GROUP BY sku',
        (now-3600,))}
    rows=referrals.execute('''
        SELECT sku,COUNT(*),
               COALESCE(SUM(CASE WHEN refunded=1 THEN -cost
                                 ELSE amount-cost-COALESCE(reward,0) END),0),
               COALESCE(SUM(CASE WHEN refunded=0 AND amount>cost THEN 1 ELSE 0 END),0)
        FROM sales GROUP BY sku''').fetchall()
    return (recent,
            {row[0]:row[1] for row in rows},
            {row[0]:row[2] for row in rows},
            {row[0]:row[3] for row in rows})

def own_grade_profit_evidence(referrals, intel_path, grade='A'):
    """Join local cost/refund truth to the official certified sale grade."""
    path=Path(intel_path)
    if not path.exists():return {}
    alias='intel_grade'
    try:
        referrals.execute("ATTACH DATABASE ? AS "+alias,
                          ('file:'+str(path.resolve())+'?mode=ro',))
        rows=referrals.execute('''
            SELECT s.sku,COUNT(*),
                   COALESCE(SUM(CASE WHEN s.refunded=0 AND s.amount>s.cost
                                     THEN 1 ELSE 0 END),0),
                   COALESCE(SUM(CASE WHEN s.refunded=1 THEN -s.cost
                                     ELSE s.amount-s.cost-COALESCE(s.reward,0) END),0),
                   COALESCE(SUM(CASE WHEN s.refunded=1 THEN 1 ELSE 0 END),0)
            FROM sales s JOIN intel_grade.trades t ON t.seq=s.ledger
            WHERE t.grade=? GROUP BY s.sku''',(grade,)).fetchall()
        return {row[0]:{'sales':row[1],'profitable':row[2],
                        'gross':row[3],'refunds':row[4]} for row in rows}
    except sqlite3.Error:
        return {}
    finally:
        try:referrals.execute('DETACH DATABASE '+alias)
        except sqlite3.Error:pass

def recent_refund_cooldown_skus(referrals, intel_path, now=None, cooldown_hours=6,
                                repeat_cooldown_hours=24, repeat_refunds=2):
    """Temporarily stop restocking a SKU after our own official breakage.

    The public ledger supplies the actual breakage time while the local sales
    ledger proves ownership of the globally unique unit.  This avoids both
    trusting a competitor's claim and permanently banning a SKU on one small
    sample.  One breakage uses the ordinary short cooldown.  Repeated official
    refunds for the same local SKU inside the longer evidence window extend
    the cooldown, because a second failure is materially different from an
    isolated unlucky unit.  Held units remain sellable; only new procurement
    is cooled down.
    """
    now=time.time() if now is None else now
    hours=max(0.0,float(cooldown_hours or 0))
    repeat_hours=max(hours,float(repeat_cooldown_hours or 0))
    repeat_count=max(2,int(repeat_refunds or 2))
    path=Path(intel_path)
    if hours<=0 or not path.exists():return set()
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as intel:
            events={row[0]:row[1] for row in intel.execute(
                'SELECT unit,MAX(ts) FROM breakages '
                'WHERE refunded>0 AND ts>? AND unit IS NOT NULL GROUP BY unit',
                (now-repeat_hours*3600,))}
    except sqlite3.Error:
        return set()
    if not events:return set()
    try:
        by_sku={}
        for sku,unit in referrals.execute('SELECT sku,unit FROM sales WHERE refunded=1'):
            if unit in events:by_sku.setdefault(sku,[]).append(events[unit])
        ordinary_cutoff=now-hours*3600
        return {sku for sku,timestamps in by_sku.items()
                if any(ts>ordinary_cutoff for ts in timestamps)
                or len(timestamps)>=repeat_count}
    except sqlite3.Error:
        return set()

def mature_refund_risk_skus(referrals, min_sales=20, min_refunds=2,
                            min_book_margin=.03):
    """Stop restocking a mature repeat-refund cohort with negligible Book yield.

    Refunded units contribute negative principal, matching ``own_sales_evidence``.
    A small or single-failure sample remains governed by the temporary cooldown;
    this veto applies only after enough local settlements and repeated official
    refunds make the opportunity cost measurable.  Held stock remains sellable.
    """
    samples=max(1,int(min_sales or 1));refunds=max(1,int(min_refunds or 1))
    threshold=max(0.0,float(min_book_margin or 0))
    try:
        rows=referrals.execute('''
            SELECT sku,COUNT(*),COALESCE(SUM(refunded),0),
                   COALESCE(SUM(CASE WHEN refunded=1 THEN -cost
                                     ELSE amount-cost-COALESCE(reward,0) END),0),
                   COALESCE(SUM(cost),0)
            FROM sales GROUP BY sku''').fetchall()
    except sqlite3.Error:
        return set()
    return {sku for sku,count,failed,gross,capital in rows
            if count>=samples and failed>=refunds and capital>0
            and gross/capital<=threshold}

def active_high_value_warranty_skus(referrals, catalog, min_cost=500000,
                                    now=None, warranty_hours=6):
    """Do not repeat a costly goods trial before its warranty result matures.

    The block is SKU-local: another evidence-backed high-value SKU may still be
    tested, while food and lower-principal goods retain their normal cadence.
    Pending settlements are conservatively treated as active.
    """
    now=time.time() if now is None else now
    threshold=max(0,int(min_cost or 0));hours=max(0.0,float(warranty_hours or 0))
    if threshold<=0 or hours<=0:return set()
    try:
        rows=referrals.execute('''
            SELECT active.sku,MAX(active.cost) FROM sales AS active
            WHERE active.refunded=0 AND active.cost>=?
              AND (active.settled IS NULL OR active.settled>?)
            GROUP BY active.sku
            HAVING (SELECT COUNT(*) FROM sales AS history
                    WHERE history.sku=active.sku)<=1''',
            (threshold,now-hours*3600)).fetchall()
    except sqlite3.Error:
        return set()
    return {sku for sku,_ in rows
            if (catalog.get(sku,{}) or {}).get('kind')=='goods'}

def own_quote_conversion_evidence(adaptive_path, now=None, hours=6, grade=None):
    """Aggregate bounded quote-to-sign evidence by SKU and optional grade.

    Runtime events contain only structured numeric sale metadata. Matching by
    task/correlation lets procurement distinguish a profitable market-wide SKU
    from one whose arrivals repeatedly fail in this shop. The rolling window
    automatically expires old strategy versions and retries a SKU after its
    local evidence becomes stale.
    """
    now=time.time() if now is None else now;path=Path(adaptive_path)
    if not path.exists() or hours<=0:return {}
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            rows=db.execute("SELECT kind,body,correlation FROM runtime_events "
                            "WHERE ts>? AND kind IN ('quote_created','sale_signed')",
                            (now-float(hours)*3600,)).fetchall()
    except sqlite3.Error:
        return {}
    quotes={};signed=set()
    for kind,body,correlation in rows:
        try:event=json.loads(body)
        except (TypeError,ValueError):continue
        task=str(event.get('task') or correlation or '')
        if not task:continue
        if (kind=='quote_created' and event.get('sku') and
                (grade is None or event.get('grade')==grade)):
            quotes[task]=str(event['sku'])
        elif kind=='sale_signed':
            signed.add(task)
    result={}
    for task,sku in quotes.items():
        row=result.setdefault(sku,{'quotes':0,'signed':0})
        row['quotes']+=1;row['signed']+=task in signed
    for row in result.values():
        row['rate']=row['signed']/row['quotes'] if row['quotes'] else 0.0
    return result

def own_a_conversion_evidence(adaptive_path, now=None, hours=6):
    """Aggregate dominant A-grade conversion for durable goods policy."""
    return own_quote_conversion_evidence(adaptive_path,now,hours,grade='A')

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

def paid_window_units(rows, opening):
    """Return journaled paid units per SKU for one supply window."""
    result={}
    for row in rows:
        if row['status']!='paid' or row['created']<opening:continue
        keys=row.keys() if hasattr(row,'keys') else row
        qty=row['qty'] if 'qty' in keys else 1
        try:qty=max(1,int(qty or 1))
        except (TypeError,ValueError):qty=1
        result[row['sku']]=result.get(row['sku'],0)+qty
    return result

def paid_bridge_used(rows, opening):
    """Return whether this supply window actually spent its bridge slot.

    A normal high-value purchase must not consume the separate, one-unit
    medium-cost bridge allowance. Older rows did not persist allocation
    intent; an absent or malformed marker is treated as a normal purchase.
    """
    for row in rows:
        if row['status']!='paid' or row['created']<opening:continue
        keys=row.keys() if hasattr(row,'keys') else row
        raw=row['detail'] if 'detail' in keys else None
        try:detail=json.loads(raw or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):continue
        if detail.get('capital_bridge') is True:return True
    return False

def breadth_window_blocks(rows, opening, inventory, target, refill_targets=None):
    """Avoid duplicate SKU buys while inventory reflection can lag.

    ``allocation_plan`` already fills the evidenced need for one supply cadence
    in its first successful call.  A second call in the same short window is a
    stale-inventory duplicate, wastes a rate-limited slot, and can exceed the
    per-SKU cap. The journal is authoritative regardless of breadth target.
    """
    paid=paid_window_units(rows,opening)
    refill_targets=refill_targets or {}
    return {sku for sku,qty in paid.items()
            if sku and qty>=max(1,int(refill_targets.get(sku,1)))}

def refresh_window_blocks(blocked, previous_window, current_window, pending_skus):
    """Clear old-window refusals without clearing unresolved POSTs.

    A restart can inherit a ``sent`` supply journal row whose HTTP outcome is
    unknown.  The old ordering added that SKU to ``blocked`` and immediately
    erased it when initializing the current window, permitting a duplicate
    POST.  Pending/uncertain rows remain blocked across every window because
    they are never safe to retry.
    """
    pending=set(pending_skus)
    if previous_window!=current_window:return pending
    return set(blocked)|pending

def competitor_breadth_target(strategy):
    """Match the typical leading shop, not the weakest observed outlier."""
    value=(strategy.get('competition_breadth') or {}).get('top10_median_skus')
    return math.ceil(value) if isinstance(value,(int,float)) and value>0 else 0

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
    policy=policy or settings()
    ranked_by_sku={row.get('sku'):row for row in ranked if row.get('sku')}
    holding={};sellable_holding={};short_food_holding={};holding_cost={}
    journal_holding={sku:max(0,int(qty)) for sku,qty in
                     (policy.get('window_purchased_units') or {}).items()}
    now=time.time()
    food_sellable_seconds=max(0.0,float(policy.get('food_sellable_min_hours',48)))*3600
    for unit in inventory.get('units',[]):
        sku=unit['sku'];holding[sku]=holding.get(sku,0)+1
        holding_cost[sku]=holding_cost.get(sku,0)+unit.get('cost',0)
        kind=unit.get('kind') or ranked_by_sku.get(sku,{}).get('kind')
        expires=unit.get('expires_at')
        short_food=(kind=='food' and isinstance(expires,(int,float)) and
                    expires-now<food_sellable_seconds)
        if short_food:
            short_food_holding[sku]=short_food_holding.get(sku,0)+1
        else:
            # Missing expiry remains conservative: it blocks replacement
            # procurement instead of allowing an unbounded duplicate lot.
            sellable_holding[sku]=sellable_holding.get(sku,0)+1
    blocked=set(blocked)|set(policy.get('blocked_skus',()))
    blocked|={sku for sku in policy.get('blocked_until_zero_skus',()) if holding.get(sku,0)>0}
    # Concentration is a share of total wealth, not of cash left after warranty
    # reserves. ``available`` still strictly caps spending below; using it as
    # the denominator made a larger safety reserve paradoxically classify a
    # single sub-10%-of-wealth unit as over-concentrated.
    portfolio_capital=sum(holding_cost.values())+max(0,inventory.get('balance',available))
    # Keep cold-goods experiments attributable and bounded. After the first
    # guarded high-value canary sold profitably, permit one additional distinct
    # experiment while the first slow trial remains held. This is a count of
    # SKUs, not units; every normal cash, concentration and one-unit high-value
    # cap remains in force. Legacy boolean callers retain the old behaviour.
    max_active_unproven=max(1,int(policy.get('max_active_unproven_goods',1)))
    active_unproven_count=policy.get('active_unproven_goods_count')
    if active_unproven_count is None:
        legacy_active=policy.get('active_unproven_goods_present')
        if legacy_active is not None:
            active_unproven_count=max_active_unproven if legacy_active else 0
        else:
            active_unproven_count=len({
                unit.get('sku') for unit in inventory.get('units',[])
                if (unit.get('kind') or
                    ranked_by_sku.get(unit.get('sku'),{}).get('kind'))=='goods'
                and ranked_by_sku.get(unit.get('sku'),{}).get('own_sales_total')==0})
    active_unproven_count=max(0,int(active_unproven_count))
    unproven_capacity=max(0,max_active_unproven-active_unproven_count)
    high_value_count=policy.get('high_value_window_count')
    if high_value_count is None:
        high_value_count=int(bool(policy.get('high_value_window_used',False)))
    high_value_count=max(0,int(high_value_count))
    high_value_canary_limit=max(0,int(policy.get('high_value_canary_window_limit',0)))
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
        # Goods are dominated by A arrivals, so their A conversion is the
        # relevant local veto. Food B/C arrivals can be both profitable and
        # fast-selling even when A buyers reject the price; use all-grade food
        # conversion rather than discarding that useful cohort.
        if row.get('kind')=='food':
            local_quotes=row.get('own_quotes',0)
            local_rate=row.get('own_conversion_rate')
        else:
            local_quotes=row.get('own_a_quotes',0)
            local_rate=row.get('own_a_conversion_rate')
        if (local_quotes>=policy.get('min_own_a_conversion_samples',10) and
                isinstance(local_rate,(int,float)) and
                local_rate<policy.get('min_own_a_conversion_rate',.10)):
            continue
        cold_safe_trial=False
        high_value_canary=False
        own_profit_proven=False
        local_grade_proven=False
        if row.get('kind')=='goods':
            min_samples=policy.get('min_goods_a_profit_samples',0)
            min_probability=policy.get('min_goods_a_profit_probability',0)
            own_sales_total=row.get('own_sales_total',row.get('own_sales_1h',0))
            own_profitable=row.get('own_profitable_sales',0)
            local_grade_sales=row.get('own_dominant_grade_sales',0)
            local_grade_profitable=row.get('own_dominant_grade_profitable',0)
            local_grade_proven=(
                local_grade_sales>=policy.get(
                    'min_local_high_value_grade_sales',5) and
                row.get('own_dominant_grade_gross',0)>0 and
                row.get('own_dominant_grade_refunds',0)==0 and
                local_grade_profitable/max(1,local_grade_sales)>=policy.get(
                    'min_local_high_value_grade_profitable_share',.80))
            high_value_grade_guard=(
                row.get('upper_cost',0)>=
                policy.get('high_value_lane_min_unit_cost',math.inf))
            # Market-wide demand cannot rescue a product that has already
            # produced a mature negative Book contribution in this shop.
            # Keep selling held units, but stop restocking until later sales
            # make the refund-aware local cohort positive again.
            local_gross=row.get('own_gross_total')
            # A single refunded high-value unit can erase many ordinary wins.
            # Unlike cheap goods, require the SKU's complete local history to
            # recover above zero before risking another expensive unit. Held
            # stock remains sellable and can supply that recovery evidence.
            if (high_value_grade_guard and own_sales_total>=1 and
                    isinstance(local_gross,(int,float)) and local_gross<=0):
                continue
            if (own_sales_total>=policy.get('min_own_loss_veto_sales',5) and
                    isinstance(local_gross,(int,float)) and local_gross<=0):
                continue
            own_profit_proven=(
                own_sales_total>=policy.get('min_own_profitable_goods_sales',3) and
                row.get('own_gross_total',0)>0 and
                own_profitable/own_sales_total>=
                    policy.get('min_own_profitable_goods_share',.70))
            weak_grade_economics=(
                row.get('dominant_grade_samples',0)<min_samples or
                row.get('dominant_grade_profit_probability',0)<min_probability)
            # A few profitable local sales may rescue a cheap SKU whose grade
            # prior is still noisy. They must not rescue a high-value lot: one
            # unfavorable draw can immobilize over 500k and the current market
            # may clear below its cost. Expensive goods therefore always pass
            # the same minimum grade-economics gate before procurement.
            if (weak_grade_economics and not local_grade_proven and
                    (high_value_grade_guard or not own_profit_proven)):
                continue
            # Revenue is the live seller bottleneck, but the old proven-only
            # priority could leave every scarce supply slot to cheap stock.
            # Admit only the configured number of demanded, grade-supported
            # high-value canaries in a window. Each remains one unit per SKU,
            # and the active-unproven ceiling bounds overlap across windows.
            high_value_canary=(
                high_value_count<high_value_canary_limit and
                own_sales_total==0 and unproven_capacity>0 and
                not holding.get(row['sku']) and high_value_grade_guard and
                row.get('sales_1h',0)>=policy.get('high_value_canary_min_sales_1h',2) and
                row.get('distinct_buyers_6h',0)>=
                    policy.get('high_value_canary_min_distinct_buyers_6h',5) and
                row.get('estimated_sale_likelihood',0)>=
                    policy.get('high_value_canary_min_sale_likelihood',.30) and
                row.get('dominant_grade_samples',0)>=min_samples and
                row.get('dominant_grade_profit_probability',0)>=min_probability)
            cold_safe_trial=(cold_goods_frozen and unproven_capacity>0 and
                not holding.get(row['sku']) and
                row.get('sales_1h',0)>=policy.get('cold_safe_trial_sales_1h',math.inf) and
                row.get('dominant_grade_profit_probability',0)>=
                    policy.get('cold_safe_trial_profit_probability',1.1))
        own_sales_total=row.get('own_sales_total',row.get('own_sales_1h',0))
        proven_good=row.get('kind')=='goods' and own_sales_total>=1
        # Supply calls and cash are both scarce.  Stop opening additional goods
        # trials while too much principal is already tied up in goods that have
        # not sold from our shop in the last hour. Proven goods keep restocking,
        # and trials reopen automatically as the cold cohort sells down.
        if (cold_goods_frozen and row.get('kind')=='goods' and
                row.get('own_sales_1h',0)<policy.get('cold_goods_min_own_sales_1h',1) and
                not proven_good and
                not cold_safe_trial and not high_value_canary):
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
        is_food=row.get('kind')=='food'
        shelf_hours=row.get('shelf_hours')
        freshness_replacement_supported=(is_food and
            isinstance(shelf_hours,(int,float)) and
            shelf_hours>food_sellable_seconds/3600)
        effective_holding=max(journal_holding.get(row['sku'],0),
                          (sellable_holding.get(row['sku'],0)
                           if freshness_replacement_supported else
                           holding.get(row['sku'],0)))
        current_holding=max(holding.get(row['sku'],0),
                            journal_holding.get(row['sku'],0))
        need=max(0,math.ceil(forecast)-effective_holding)
        food_scale_ready=False
        strongly_proven_high_value=False
        if is_food:
            unit_cap=policy.get('max_food_units_per_sku',2)
            # Keep unproven/perishable food at the two-unit absolute cap.  A
            # single extra unit is justified only by this shop's own settled
            # profit, conversion and repeated stockout evidence; market-wide
            # popularity alone cannot expand a short-lived SKU.
            food_scale_ready=(
                own_sales_total>=policy.get('min_proven_food_sales',5) and
                row.get('own_gross_total',0)>0 and
                local_quotes>=policy.get('min_proven_food_quote_samples',10) and
                isinstance(local_rate,(int,float)) and
                local_rate>=policy.get('min_proven_food_conversion_rate',.20) and
                row.get('own_stockouts_6h',0)>=
                    policy.get('min_proven_food_stockouts_6h',5))
            if food_scale_ready:
                unit_cap=max(unit_cap,
                             policy.get('max_proven_food_units_per_sku',3))
        else:
            unit_cap=policy.get('max_units_per_sku',3)
            if row.get('upper_cost',0)>=policy.get('high_value_lane_min_unit_cost',math.inf):
                unit_cap=min(unit_cap,policy.get('max_high_value_units_per_sku',1))
                profitable_share=own_profitable/max(1,own_sales_total)
                strongly_proven_high_value=(
                    own_sales_total>=policy.get('strongly_proven_high_value_min_own_sales',math.inf)
                        and row.get('own_gross_total',0)>0
                        and profitable_share>=policy.get(
                            'strongly_proven_high_value_min_profitable_share',1.1))
                if strongly_proven_high_value:
                    unit_cap=min(policy.get('max_units_per_sku',3),max(
                        unit_cap,policy.get(
                            'max_strongly_proven_high_value_units_per_sku',unit_cap)))
                if high_value_canary:
                    unit_cap=min(unit_cap,1)
        # These are absolute per-SKU inventory ceilings. Demand velocity may
        # move a SKU up the queue, but it must expand breadth rather than turn
        # one fast seller into another concentrated inventory position. High-
        # value canaries are additionally limited to one live unit per SKU so
        # a slow first quote cannot duplicate a six-figure capital position.
        # Proven high-value SKUs may use their separately configured ceiling.
        # Portfolio size is bounded by available capital and evidence-backed
        # SKU breadth.  A fixed global unit count used to strand growing cash.
        sku_ceiling=unit_cap
        short_food_replacement=False
        if is_food:
            actual=holding.get(row['sku'],0)
            ordinary_room=max(0,sku_ceiling-actual)
            replacement_limit=max(0,int(policy.get('max_short_food_replacement_units',1)))
            replacement_room=(max(0,sku_ceiling+replacement_limit-actual)
                              if freshness_replacement_supported and
                              actual>=sku_ceiling and
                              sellable_holding.get(row['sku'],0)==0 and
                              short_food_holding.get(row['sku'],0)>0 else 0)
            inventory_room=max(ordinary_room,replacement_room)
            short_food_replacement=bool(replacement_room)
        else:
            inventory_room=max(0,sku_ceiling-current_holding)
            # A repeatedly profitable high-value SKU that sells out in seconds
            # should not be replenished one unit at a time merely because a
            # conservative short-window likelihood rounded to one.  This is a
            # target inventory floor, not a waiver: inventory, portfolio-
            # capital, cash, warranty, grade and refund guards below still cap
            # the actual purchase.  New/canary SKUs never enter this branch.
            if strongly_proven_high_value:
                need=max(need,min(
                    unit_cap,
                    int(policy.get(
                        'strongly_proven_high_value_min_units_when_sold_out',1)))-
                    current_holding)
        need=min(need,inventory_room)
        if cold_safe_trial:
            need=min(need,policy.get('max_cold_safe_trial_units',1))
        is_missing=not holding.get(row['sku'])
        # A sold-out SKU with an actual settled sale is proven inventory, not
        # a new trial.  Treating every zero-holding SKU as new kept capping our
        # fastest sellers at one unit just when they needed replenishment.
        # ``own_sales_total`` survives a quiet hour; callers that have not yet
        # supplied it retain the conservative recent-sale fallback.
        is_new=is_missing and own_sales_total<1
        breadth_target=max(0,policy.get('breadth_target_skus',0) or 0)
        breadth_expansion=len(holding)<breadth_target and is_missing
        if breadth_expansion and is_new:
            need=min(need,policy.get('max_breadth_trial_units',1))
        trial_units=(policy.get('initial_food_trial_units',2) if is_food else
                     policy.get('initial_trial_units',policy.get('min_trial_units',2)))
        # A single unit above the portfolio share is not a "small trial" and
        # recreates the historical concentrated-stock failure. Evidence may
        # scale the portfolio, but it never waives this wealth-relative cap.
        capital_share=policy.get('max_sku_capital_share',1)
        # A proven, refund-aware positive cohort may hold two expensive units
        # instead of repeatedly selling out after a one-unit restock, but only
        # when the dominant A-grade acquisition is itself robustly profitable.
        # An overall profitable history can be carried by cheap B/C arrivals;
        # it must not enlarge the capital cap for a marginal high-cost A lot.
        # New, unproven, and marginal-grade goods retain the original cap.
        scale_grade_probability=(
            policy.get('min_high_value_restock_grade_profit_probability',.60)
            if strongly_proven_high_value else
            policy.get('min_proven_scale_grade_profit_probability',.70))
        proven_scale_ready=(
            own_profit_proven and
            row.get('dominant_grade_samples',0)>=
                policy.get('min_proven_scale_grade_samples',
                           policy.get('min_goods_a_profit_samples',0)) and
            row.get('dominant_grade_profit_probability',0)>=
                scale_grade_probability)
        if proven_scale_ready:
            capital_share=max(capital_share,
                              policy.get('max_proven_sku_capital_share',capital_share))
        capital_ceiling=portfolio_capital*capital_share
        capital_room=max(0,capital_ceiling-holding_cost.get(row['sku'],0))
        need=min(need,int(capital_room//max(1,row['upper_cost'])))
        if need<=0:continue
        realized_hourly_contribution=0
        if own_profit_proven:
            realized_hourly_contribution=(
                row.get('own_sales_1h',0)*
                max(0,row.get('own_gross_total',0)/max(1,own_sales_total)))
        evidence=dict(row,trusted_inquiries_6h=trusted_inquiries,
                      trusted_stockouts_6h=trusted_stockouts,sku_inventory_ceiling=sku_ceiling,
                      food_scale_ready=food_scale_ready,
                      sellable_holding=effective_holding,
                      short_food_holding=short_food_holding.get(row['sku'],0),
                      short_food_replacement=short_food_replacement,
                      freshness_replacement_supported=freshness_replacement_supported,
                      cold_goods_cost=cold_goods_cost,cold_goods_limit=cold_goods_limit,
                      cold_goods_frozen=cold_goods_frozen,cold_safe_trial=cold_safe_trial,
                      active_unproven_goods_count=active_unproven_count,
                      max_active_unproven_goods=max_active_unproven,
                      new_unproven_goods=(row.get('kind')=='goods' and is_new),
                      own_profit_proven=own_profit_proven,
                      local_grade_proven=local_grade_proven,
                      proven_scale_ready=proven_scale_ready,
                      realized_hourly_contribution=realized_hourly_contribution)
        diversity=policy.get('new_sku_bonus',1) if breadth_expansion and is_new else 1
        # Official score contribution remains primary, with a sub-linear
        # capital-risk adjustment. This preserves high-value goods without
        # letting a handful of million-credit units consume an entire window.
        # Market-wide expected contribution remains the prior. Once our own
        # shop has a sufficiently large and mostly profitable cohort, add the
        # realized hourly gross contribution once. This makes a just-sold-out
        # proven item compete for scarce capital without granting any bonus to
        # a new or historically loss-making SKU.
        contribution=row.get('score',0)+realized_hourly_contribution
        # A live pace comparison showed that this shop was completing more
        # deals than the class top-10 median while producing only about 42%
        # of their Revenue growth.  Keep the adjustment sub-linear and
        # configurable, but relax it one measured step so official Revenue
        # opportunities are not systematically buried behind cheap turnover.
        # Per-SKU capital caps, grade economics, conversion vetoes, refund
        # vetoes, and the cash/liability reserve remain unchanged.
        risk_exponent=max(.5,min(1.0,float(policy.get('capital_risk_exponent',.75))))
        risk_adjusted=contribution/max(1,row['upper_cost'])**risk_exponent
        # Breadth is a score bonus, not an absolute lane.  Giving every missing
        # SKU unconditional precedence starved measured Revenue opportunities
        # until the full competitor breadth target was reached.  The bonus
        # still expands the catalogue when opportunities are comparable, while
        # materially stronger proven contribution can now win the same window.
        # The 500k+ market carries most live Revenue, but random supply costs
        # make broad expansion unsafe. Give a bounded number of sold-out,
        # previously profitable, grade-proven high-value SKUs the first slots
        # in a supply window. Every normal conversion, refund, concentration,
        # cash and warranty guard above still applies. The live limit is three
        # only after five recent guarded high-value settlements remained
        # positive and refund-free, including both lots in the first two-slot
        # window; callers without that explicit policy retain one slot.
        high_value_priority=2
        high_value_min=policy.get('high_value_lane_min_unit_cost',math.inf)
        high_value_limit=max(0,int(policy.get('high_value_window_limit',1)))
        high_value_restock_needed=(not holding.get(row['sku']) or
            (strongly_proven_high_value and
             current_holding<policy.get(
                 'strongly_proven_high_value_min_units_when_sold_out',1)))
        if (high_value_count<high_value_limit and
                row.get('kind')=='goods' and high_value_restock_needed and
                own_sales_total>=1 and row.get('own_gross_total',0)>0 and
                row.get('upper_cost',0)>=high_value_min and
                row.get('dominant_grade_samples',0)>=
                    policy.get('min_high_value_restock_grade_samples',
                               policy.get('min_goods_a_profit_samples',0)) and
                (row.get('dominant_grade_profit_probability',0)>=
                    policy.get('min_high_value_restock_grade_profit_probability',
                               policy.get('min_goods_a_profit_probability',0)) or
                 local_grade_proven)):
            high_value_priority=0
        elif high_value_canary:
            high_value_priority=1
        evidence['high_value_priority']=(high_value_priority<2)
        evidence['high_value_canary']=high_value_canary
        evidence['high_value_window_count']=high_value_count
        evidence['high_value_window_limit']=high_value_limit
        choices.append((high_value_priority,-(risk_adjusted*diversity),
                        row['sku'],need,evidence))
    # Each candidate is bounded by condition-specific per-SKU caps. When no
    # explicit test/canary slot limit is supplied, capital and those caps
    # determine the total portfolio instead of an arbitrary 250 units.
    ordered=sorted(choices)
    # Revenue opportunities can require several minutes of cash accumulation.
    # Spending every smaller remainder on cheap breadth made an already
    # approved high-value restock permanently unaffordable.  If no priority
    # high-value choice fits yet, preserve the bounded deployable amount; the
    # next inventory refresh will buy it once ordinary sales close the gap.
    high_value_choices=[choice for choice in ordered if choice[0]<2]
    if (high_value_choices and
            not any(choice[4]['upper_cost']<=available
                    for choice in high_value_choices)):
        # Do not let one temporarily unaffordable Revenue opportunity idle all
        # deployable cash. One sold-out, repeatedly profitable and currently
        # demanded medium-cost good may bridge the gap by turning a minority
        # of the cash into near-term sales. This is deliberately not a second
        # general procurement lane: one unit is allowed per supply window,
        # at least half the deployable cash remains reserved for the priority
        # good, and weak/new/refund-negative cohorts cannot qualify.
        bridge=[]
        if not policy.get('capital_bridge_window_used',False):
            max_bridge_cost=available*max(
                0.0,min(1.0,float(policy.get('capital_bridge_max_available_share',.5))))
            for choice in ordered:
                priority,_,sku,need,row=choice
                sales=max(0,int(row.get('own_sales_total',0) or 0))
                profitable=max(0,int(row.get('own_profitable_sales',0) or 0))
                profit_share=profitable/max(1,sales)
                if (priority==2 and row.get('kind')=='goods' and
                        not holding.get(sku) and need>0 and
                        sales>=policy.get('capital_bridge_min_own_sales',5) and
                        row.get('own_gross_total',0)>0 and
                        profit_share>=policy.get('capital_bridge_min_profitable_share',.70) and
                        row.get('sales_1h',0)>=
                            policy.get('capital_bridge_min_market_sales_1h',3) and
                        row.get('estimated_sale_likelihood',0)>=
                            policy.get('capital_bridge_min_sale_likelihood',.30) and
                        row.get('dominant_grade_samples',0)>=
                            policy.get('capital_bridge_min_grade_samples',3) and
                        row.get('dominant_grade_profit_probability',0)>=
                            policy.get('capital_bridge_min_grade_profit_probability',.60) and
                        row.get('upper_cost',math.inf)<=max_bridge_cost):
                    evidence=dict(row,capital_bridge=True,
                                  capital_bridge_profit_share=profit_share,
                                  capital_bridge_max_cost=max_bridge_cost)
                    bridge.append((priority,choice[1],sku,need,evidence))
        if not bridge:
            return []
        _,_,sku,_,row=bridge[0]
        return [{'sku':sku,'qty':1,'upper_unit_cost':row['upper_cost'],
                 'capital':row['upper_cost'],'evidence':row}]
    plan=[];slots=sum(row[3] for row in choices) if available_slots is None else available_slots
    remaining_unproven=unproven_capacity
    remaining_canaries=max(0,high_value_canary_limit-high_value_count)
    remaining_high_value=max(0,max(0,int(policy.get('high_value_window_limit',1)))-
                             high_value_count)
    for _,_,sku,need,row in ordered:
        if row.get('new_unproven_goods') and remaining_unproven<=0:continue
        if row.get('high_value_canary') and remaining_canaries<=0:continue
        if row.get('high_value_priority') and remaining_high_value<=0:continue
        # Official supply API accepts at most 20 units per call. Remaining
        # evidenced need is purchased on subsequent inventory refreshes.
        qty=min(policy.get('max_lot_units',20),need,int(available//row['upper_cost']),max(0,slots))
        if qty<=0:continue
        plan.append({'sku':sku,'qty':qty,'upper_unit_cost':row['upper_cost'],
                     'capital':qty*row['upper_cost'],'evidence':row})
        if row.get('new_unproven_goods'):remaining_unproven-=1
        if row.get('high_value_canary'):remaining_canaries-=1
        if row.get('high_value_priority'):remaining_high_value-=1
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
    db.execute('CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY,campaign TEXT,sku TEXT,qty INTEGER,upper_cost INTEGER,cost INTEGER,status TEXT,created REAL,detail TEXT)')
    if 'qty' not in {row[1] for row in db.execute('PRAGMA table_info(purchases)')}:
        db.execute('ALTER TABLE purchases ADD COLUMN qty INTEGER')
    db.commit()
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
        current_window=restock.get('opens_at')
        blocked=refresh_window_blocks(blocked,window,current_window,pending_skus)
        window=current_window
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
            (own_sales_1h,own_sales_total,own_gross_total,
             own_profitable_sales)=own_sales_evidence(referrals)
            own_grade=own_grade_profit_evidence(
                referrals,state/'market_intel.sqlite3','A')
            refund_cooldowns=recent_refund_cooldown_skus(
                referrals,state/'market_intel.sqlite3',
                cooldown_hours=policy.get('refund_restock_cooldown_hours',6),
                repeat_cooldown_hours=policy.get('repeat_refund_cooldown_hours',24),
                repeat_refunds=policy.get('repeat_refund_cooldown_count',2))
            mature_refund_risks=mature_refund_risk_skus(
                referrals,
                min_sales=policy.get('mature_refund_veto_min_sales',20),
                min_refunds=policy.get('mature_refund_veto_min_refunds',2),
                min_book_margin=policy.get('mature_refund_veto_book_margin',.03))
            active_high_value_warranties=active_high_value_warranty_skus(
                referrals,catalog,
                min_cost=policy.get('high_value_lane_min_unit_cost',500000))
        a_conversion=own_a_conversion_evidence(
            state/'adaptive.sqlite3',hours=policy.get('own_a_conversion_window_hours',6))
        all_conversion=own_quote_conversion_evidence(
            state/'adaptive.sqlite3',hours=policy.get('own_a_conversion_window_hours',6))
        ranked=[]
        for source in (strategy.get('candidates') or strategy.get('top10',[])):
            sku=source.get('sku');conversion=a_conversion.get(sku,{})
            all_grades=all_conversion.get(sku,{})
            ranked.append(dict(source,kind=catalog.get(sku,{}).get('kind'),
                shelf_hours=catalog.get(sku,{}).get('shelf_hours'),
                own_sales_1h=own_sales_1h.get(sku,0),
                own_sales_total=own_sales_total.get(sku,0),
                own_gross_total=own_gross_total.get(sku,0),
                own_profitable_sales=own_profitable_sales.get(sku,0),
                own_dominant_grade_sales=own_grade.get(sku,{}).get('sales',0),
                own_dominant_grade_profitable=own_grade.get(sku,{}).get('profitable',0),
                own_dominant_grade_gross=own_grade.get(sku,{}).get('gross',0),
                own_dominant_grade_refunds=own_grade.get(sku,{}).get('refunds',0),
                own_a_quotes=conversion.get('quotes',0),
                own_a_signed=conversion.get('signed',0),
                own_a_conversion_rate=conversion.get('rate'),
                own_quotes=all_grades.get('quotes',0),
                own_signed=all_grades.get('signed',0),
                own_conversion_rate=all_grades.get('rate')))
        if not ranked:
            log(event='no_qualified_top10');time.sleep(20);continue
        ranked=[r for r in ranked if r.get('expected_net',0)>0]
        available=capital_available(policy,inv,exposure,pending)
        slots=None
        # Starting capital is 5M. More accumulated cash funds more demand
        # windows, while capital_available still preserves cash/liabilities.
        aggression=max(1.0,min(policy.get('max_demand_windows',4),inv.get('balance',0)/3_000_000))
        runtime_policy=dict(policy)
        # Use the complete settled-sales history, not merely the current ranked
        # candidate list, to decide whether a cold-goods experiment is active.
        # This prevents a new expensive trial while any held good has never sold.
        runtime_policy['active_unproven_goods_count']=len({
            unit.get('sku') for unit in inv.get('units',[])
            if (unit.get('kind') or catalog.get(unit.get('sku'),{}).get('kind'))=='goods'
            and unit.get('sku') not in own_sales_total})
        target=competitor_breadth_target(strategy)
        if target>0:runtime_policy['breadth_target_skus']=target
        ranked_by_sku={row.get('sku'):row for row in ranked if row.get('sku')}
        high_value_min=policy.get('high_value_lane_min_unit_cost',math.inf)
        high_value_window_count=sum(
            row['status']=='paid' and row['created']>=opening and
            ranked_by_sku.get(row['sku'],{}).get('upper_cost',0)>=high_value_min
            for row in rows)
        runtime_policy['high_value_window_count']=high_value_window_count
        runtime_policy['high_value_window_used']=(
            high_value_window_count>=max(1,int(policy.get('high_value_window_limit',1))))
        # The bridge is intentionally limited to one unit in a supply window.
        # Replay the persisted allocation kind: a normal high-value purchase
        # does not consume the separate medium-cost bridge allowance.
        runtime_policy['capital_bridge_window_used']=paid_bridge_used(rows,opening)
        refill_targets={
            row['sku']:int(policy.get(
                'strongly_proven_high_value_min_units_when_sold_out',1))
            for row in ranked
            if row.get('kind')=='goods' and
               row.get('upper_cost',0)>=high_value_min and
               row.get('own_sales_total',0)>=policy.get(
                   'strongly_proven_high_value_min_own_sales',math.inf) and
               row.get('own_gross_total',0)>0 and
               row.get('own_profitable_sales',0)/max(1,row.get('own_sales_total',0))>=
                   policy.get('strongly_proven_high_value_min_profitable_share',1.1)}
        runtime_policy['window_purchased_units']=paid_window_units(rows,opening)
        window_blocks=breadth_window_blocks(rows,opening,inv,target,refill_targets)
        plan=allocation_plan(ranked,inv,available,
                             set(blocked)|window_blocks|refund_cooldowns|
                             mature_refund_risks|active_high_value_warranties,
                             slots,aggression,runtime_policy)
        if not plan:
            if blocked:time.sleep(10);continue
            log(event='evidence_or_capacity_wait',lifetime_spent=spent,inventory_cost=inventory_cost,
                deployable_capital=available,available_slots='capital_and_per_sku_bounded',protected_liabilities=exposure,
                demand_windows=aggression)
            time.sleep(capacity_recheck_wait(restock));continue
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
            cursor=db.execute('INSERT INTO purchases(campaign,sku,qty,upper_cost,status,created) VALUES(?,?,?,?,?,?)',(policy['campaign'],sku,qty,upper,'sent',time.time()));rid=cursor.lastrowid
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
            with db:db.execute("UPDATE purchases SET status='paid',cost=?,detail=? WHERE id=?",(
                cost,json.dumps({'balance':body.get('balance'),
                                 'capital_bridge':bool(
                                     selected['evidence'].get('capital_bridge'))}),rid))
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
