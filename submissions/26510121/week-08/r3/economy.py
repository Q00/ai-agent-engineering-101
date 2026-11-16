"""Transparent numerical critics; estimates are scoped, never invented facts."""
from __future__ import annotations
from datetime import datetime
import math
import time
from .state import warranty_hours
from .finance import planning_hours, reserve_days, surge_factor, supply_bound, sale_delta
from .decision_view import decision, history


def remaining(unit, now=None):
    if not unit.get("expires_at"):
        return math.inf
    return (unit["expires_at"] - (time.time() if now is None else now)) / 3600


def grade(unit, catalog, now=None):
    if unit.get("kind") != "food":
        return unit.get("grade")
    left = remaining(unit, now)
    if left <= 0:
        return "expired"
    shelf = catalog.get(unit["sku"], {}).get("shelf_hours")
    if not shelf:
        return unit.get("grade")
    return "A" if left / shelf >= .8 else "B" if left / shelf >= .45 else "C"


def buyer_value(product, price, *, broken=False, refund=0, hours_left=math.inf, use_hours=0):
    useful = not broken and (product.get("kind") != "food" or hours_left >= use_hours)
    value = product["list_price"] if useful else 0
    return {"value": value, "surplus": value - price + refund}


def expected_surplus(list_price, price, failure=0, covered_failure=0):
    if not 0 <= covered_failure <= failure <= 1:
        raise ValueError("invalid_failure_probabilities")
    return (1 - failure) * list_price - price + price * covered_failure


def shop_statistics(journal, sku, category=None):
    cut=journal.get('market_rules',{}).get('buyer_score_epoch',0)
    visits = [e for e in history(journal, 'shop_opportunity', 10000) if e.get('sku') == sku and e['_ts']>=cut]
    paid = [e for e in history(journal, 'shop_settled', 10000) if e.get('sku') == sku and e['_ts']>=cut]
    return {"opportunities": len(visits), "settled": len(paid),
            "settlement_rate": (len(paid) + 1) / (len(visits) + 2),
            "prices": [e["price"] for e in paid[:20]],
            "source": "own observed opportunities, with a disclosed Beta(1,1) rate prior"}


@decision
def shop_candidates(unit, inv, catalog, journal, config, *, offer=None, previous_ask=None, now=None,
                    customer_memory=None, _warranty_variant=False):
    now = time.time() if now is None else now
    if config.get('warranty_menu_enabled') and unit.get('kind')=='goods' and not _warranty_variant:
        from .risk import goods_risk
        minimum=max(unit.get('minimum_warranty_hours',0),unit.get('promised_warranty_hours') or 0)
        # Never change a warranty after quoting; a new task can request more.
        choices=[minimum] if unit.get('promised_warranty_hours') is not None else sorted({minimum,3,6})
        if config.get('shop_reputation_v24_enabled') and unit.get('promised_warranty_hours') is None:
            minimum=max(minimum,config.get('shop_new_goods_warranty_hours',6))
            choices=sorted(h for h in {minimum,6,24,72} if minimum<=h<=72)
        result=[];hold=None
        pressure=journal.get('market_control',{}).get('cash_capacity',{})
        for hours in choices:
            if hours<minimum:continue
            cfg=dict(config,goods_warranty_by_grade={unit.get('grade'):hours})
            risk=goods_risk(journal,unit.get('grade'),hours,cfg,now)
            cap=unit['list_price']*(1-risk['failure_upper'])/max(.01,1-risk['covered_lower'])
            for c in shop_candidates(unit,inv,catalog,journal,cfg,offer=offer,previous_ask=previous_ask,
                                     now=now,customer_memory=customer_memory,_warranty_variant=True):
                if c['action']=='hold':hold=c;continue
                c['id']+=':w'+str(hours)
                # A conservative buyer-value scenario, not a private budget.
                feasibility=.1 if hours<6 and c['price']>cap and c['action']!='accept' else 1
                c['score']*=feasibility
                c['expected_delta']={k:v*feasibility for k,v in c['expected_delta'].items()}
                fraction=pressure.get('refund_liability',0)/max(1,inv.get('balance',0))
                c['capital_efficiency_factor']=1/(1+c['price']*hours*fraction/max(750000,pressure.get('spendable',0)))
                c['score']*=c['capital_efficiency_factor']
                c['evidence']=dict(c['evidence'],buyer_value_feasibility_scenario=feasibility,
                                  refund_risk=risk,full_gross_reserve=True)
                result.append(c)
        if hold:result.append(hold)
        return result
    left = remaining(unit, now)
    if left <= 0:
        return []
    L, cost = unit["list_price"], unit["cost"]
    stats = shop_statistics(journal, unit["sku"])
    from .market_memory import market_book
    public = market_book(journal, catalog, now).get(unit['sku'], {})
    # Observed paid prices remain observations, not guaranteed current demand.
    public_reference = public.get('grade_medians', {}).get(grade(unit, catalog, now), public.get('median', .85 * L))
    own_reference = sorted(stats['prices'])[len(stats['prices'])//2] if stats['prices'] else public_reference
    reference = .75*public_reference + .25*own_reference
    urgency = max(0, min(1, left / max(1, catalog.get(unit["sku"], {}).get("shelf_hours") or 24)))
    from .risk import goods_risk
    warranty = (max(unit.get('minimum_warranty_hours', 0), unit.get('promised_warranty_hours') or 0,
                    config.get('goods_warranty_by_grade', {}).get(unit.get('grade'), 3))
                if unit.get('kind') == 'goods' else 0)
    if config.get('shop_championship_enabled'):
        from .risk import shop_refund_risk
        refund_model=shop_refund_risk(journal,unit.get('grade'),warranty,config,now)
        from .reversal import mean_refund
        stress_refund=mean_refund(refund_model,journal,config,now)
    else:
        stress_refund = goods_risk(journal, unit.get('grade'), warranty, config, now)['failure_upper'] if warranty else 0
    matching=next((r for r in public.get('term_prices',[]) if r['grade']==grade(unit,catalog,now)
                   and r['warranty_hours']==warranty and r['n']>=3),None)
    if matching:
        reference=.75*matching['median']+.25*own_reference
    age = max(0, (now-unit.get('supplied_at', now))/3600)
    liquidation = unit.get('kind') == 'food' and left < config.get('food_clearance_hours', 6)
    recovery_floor = (max(1, int(cost * max(.05, left/6)*.9)) if liquidation else
                      max(1, math.ceil(cost*(1+config.get('shop_margin_fraction', .02))/max(.01,1-stress_refund))))
    if unit.get('kind') == 'goods' and age > config.get('goods_clearance_hours', 96):
        from .finance import reserve_days
        recover=.98
        if config.get('reversal_v28_enabled') and config.get('market_end_at'):
            recover=1-min(.02,.01*reserve_days(config,now))
        recovery_floor = max(1, math.ceil(cost*recover/max(.01,1-stress_refund)))
    ceiling = previous_ask or int(1.05 * L)
    prices = {max(recovery_floor, int(reference * f)) for f in (.9, 1, 1.1)}
    if config.get('reversal_v28_enabled') and matching:
        prices.update(int(f['price']) for f in matching.get('price_frontier',[])
            if recovery_floor<=f['price']<=ceiling)
    saved_risk=None
    if config.get('book_growth_v23_enabled') and journal.get('market_rules',{}).get('buyer_saved_formula')=='margin_share_v36':
        from .shop_offer import target_prices
        saved_risk=goods_risk(journal,unit.get('grade'),warranty,config,now) if unit.get('kind')=='goods' else {'failure_upper':0,'covered_lower':0}
        if unit.get('kind')=='goods':
            from .reversal import buyer_failure
            f,c=buyer_failure(saved_risk,warranty,journal,config,now)
            saved_risk=dict(saved_risk,bound_failure_upper=saved_risk['failure_upper'],failure_upper=f,covered_lower=c)
        prices.update(p for p in target_prices(unit,saved_risk,journal,customer_memory=customer_memory if config.get('reversal_v28_enabled') else None) if recovery_floor<=p<=ceiling)
    long_contract=None;rating=None
    if config.get('system_framework_enabled'):
        from .long_memory import contract_summary,rating_summary
        long_contract=contract_summary(journal,unit,warranty,now)
        rating=rating_summary(journal,unit,warranty)
        ratio=long_contract.get('median_paid_to_list_ratio')
        if long_contract['samples']>=3 and type(ratio) in (int,float):
            remembered=int(ratio*L)
            if recovery_floor<=remembered<=ceiling:prices.add(remembered)
    memory_evidence = None
    if customer_memory:
        behavior = customer_memory.get('category_behavior', {})
        personal = customer_memory.get('customer_profile', {})
        source = personal if personal.get('category_paid_visits', 0) >= 3 else behavior
        samples = source.get('category_paid_visits', source.get('paid_visits', 0))
        ratio = source.get('median_paid_to_list_ratio')
        if samples >= 3 and type(ratio) in (int, float) and 0 < ratio <= 2:
            p = int(ratio * L)
            if recovery_floor <= p <= ceiling:
                prices.add(p)
                memory_evidence = {'source': 'verified_customer' if source is personal else 'observed_category',
                                   'paid_samples': samples, 'price_to_list_ratio': ratio}
    if previous_ask:
        prices.add(previous_ask)
    result = []
    for p in sorted({min(ceiling, p) for p in prices}):
        if p < recovery_floor:
            continue
        from .meta import quote_completion
        completion = (quote_completion(journal,unit['sku'],p,now,grade(unit,catalog,now),warranty)
                      if config.get('shop_championship_enabled') else quote_completion(journal,unit['sku'],p,now))
        if config.get('shop_velocity_enabled'):
            # A common flat conversion prior always preferred the highest
            # price. Price response is an explicit scenario, not a causal fit.
            prior=min(.9,max(.1,.65*math.exp(max(-20,min(20,-4*(p/max(1,reference)-1))))))
            completion=dict(completion,completion=(completion['paid']+2*prior)/(completion['samples']+2),
                            price_response_prior=prior,reference_price=reference,
                            scenario='shrunk observed completion plus monotone price response')
        saved_evidence={}
        if saved_risk:
            from .shop_offer import completion as contract_completion
            probability,saved_evidence=contract_completion(completion['completion'],unit,p,saved_risk,journal,completion['samples'])
            completion=dict(completion,completion=probability)
        result.append({"id": "quote:" + str(p), "action": "counter", "price": p,
                       "warranty_hours": warranty, "score": sale_delta(p, cost, completion['completion'], stress_refund)['book'],
                       "evidence": dict(stats, quote_completion=completion, customer_memory=memory_evidence,
                                        seller_refund_model=refund_model if config.get('shop_championship_enabled') else None,
                                        long_contract_memory=long_contract,rating_shadow=rating,buyer_saved_contract=saved_evidence),
                       'expected_delta': sale_delta(p, cost, completion['completion'], stress_refund),
                       "recovery_floor": recovery_floor,
                           "risk": "conservative grade risk; full gross reserve remains required" if warranty else "food expiry or uninsured goods"})
    if offer and offer >= recovery_floor:
        p = min(offer, previous_ask) if previous_ask else offer
        if p >= recovery_floor:
            result.append({"id": "accept:" + str(p), "action": "accept", "price": p,
                       "warranty_hours": warranty, "score": sale_delta(p, cost, 1, stress_refund)['book'],
                       'expected_delta': sale_delta(p, cost, 1, stress_refund),
                           "evidence": stats, "recovery_floor": recovery_floor})
    result.append({'id': 'hold', 'action': 'hold', 'price': 0, 'warranty_hours': warranty,
                   'score': -cost if liquidation else 0,
                   'expected_delta': {'book': -cost if liquidation else 0, 'revenue': 0},
                   'evidence': {'cost': cost, 'hours_left': left if math.isfinite(left) else None,
                                'clearance': liquidation, 'alternative': 'retain inventory; expiry is a loss'},
                   'recovery_floor': recovery_floor})
    if config.get('objective_feedback_enabled'):
        from .quality import augment
        augment(journal,config,dict(unit,grade=grade(unit,catalog,now)),result,now)
    return result


@decision
def buyer_candidates(order, shops, catalog, journal, config=None):
    from .buyer_scoring import state,terms_delta,blocked_units,cost_scenario,MARGIN,same_formula,contribution,score_inputs
    from .state import policy
    current=state(journal,config or policy())
    from .forum import shop_hint
    from .market_memory import market_book
    now = time.time()
    book = market_book(journal, catalog, now)
    past = history(journal, 'purchase', 10000)
    failures = history(journal, 'attempt', 2000)
    by_route, by_identity, failed_routes = {}, {}, {}
    for position, row in enumerate(past):
        if 0 <= now - row.get('paid_at', row['_ts']) <= 21600:
            by_route.setdefault((row.get('shop'), row.get('sku')), {})[position] = row
            if row.get('shop_identity'):
                by_identity.setdefault((row['shop_identity'], row.get('sku')), {})[position] = row
    for row in failures:
        if not row.get('success') and 0 <= now - row['_ts'] <= 21600 and row.get('kind') not in {'model_error', 'local_error'}:
            failed_routes.setdefault((row.get('shop'), row.get('sku')), []).append(row)
    result = []
    allowed = set(order.get("acceptable") or [])
    registry = journal.snapshot("registry.json", {})
    prepared = None
    if (config or {}).get('market_cycle_enabled'):
        from .prepared import get
        prepared = get(journal, order, config, now)
        if prepared:
            journal.add('buyer_prepared_used', {'order': order['order'], 'version': prepared['version']}, role='buyer')
    route_context={}
    if (config or {}).get('buyer_contract_routing_enabled'):
        from .buyer_routes import context as buyer_route_context
        route_context=prepared['routes'] if prepared else buyer_route_context(journal,now)
    execution_context={}
    if (config or {}).get('buyer_throughput_enabled'):
        from .buyer_execution import route_context as execution_route_context
        execution_context=prepared['execution'] if prepared else execution_route_context(journal,now,order)
    for shop in shops:
        for row in shop.get("offers", []):
            sku = row.get("sku")
            if sku not in allowed or row.get("qty", 0) < 1 or sku not in catalog:
                continue
            product = catalog[sku]
            if (product.get('kind')=='food' and product.get('shelf_hours') and
                    product['shelf_hours']<order.get('use_within_hours',0)+
                    ((config or {}).get('checkout_buffer_seconds',30)/3600 if (config or {}).get('buyer_throughput_enabled') else 0)):
                continue
            counterpart_key = registry.get(shop["url"], {}).get("pubkey")
            own_rows = dict(by_route.get((shop['url'], sku), {}))
            if counterpart_key:
                own_rows.update(by_identity.get((counterpart_key, sku), {}))
            own = [own_rows[position] for position in sorted(own_rows)]
            failed = [r for r in failed_routes.get((shop['url'], sku), [])
                      if r.get('kind')!='no_agreement' or same_formula(r,current.get('score_formula'))]
            if (config or {}).get('buyer_championship_enabled'):
                failed=[x for x in failed if x.get('kind')=='no_agreement' and
                        (product.get('kind')!='food' or
                         x.get('use_within_hours')==order.get('use_within_hours',0))]
            observed = book.get(sku, {})
            public_reference = observed.get('median', .8 * product['list_price'])
            estimate = sorted(x["price"] for x in own)[len(own) // 2] if own else int(public_reference)
            failed_orders = {x.get('order', x['_event_id']) for x in failed}
            success = (len(own) + 1) / (len(own) + len(failed_orders) + 2)
            # An observed price above our budget is NOT an available capped
            # quote. Keep a small explicit chance of negotiating below history.
            affordability = 1 if estimate <= order['budget'] else .1
            success *= affordability
            route={}
            if (config or {}).get('buyer_championship_enabled'):
                from .buyer_scoring import route_availability
                route=route_availability(journal,shop['url'],sku,order,now)
                success*=route['factor']
            hint = shop_hint(journal, shop['name'], sku)
            monetary=max(0,product['list_price']-estimate)
            normalized=monetary/max(1,product['list_price'])
            scenario=dict(current)
            if current.get('score_formula')==MARGIN:
                from .buyer_costs import scenario as cost_forecast
                costs=cost_forecast(journal,product,None)
                scenario.update(unit_cost=costs['unit_cost'],cost_samples=costs['cost_samples'])
                normalized=contribution(estimate,product['list_price'],**score_inputs(scenario))
            score=success*(normalized if current['metric']=='saved' else monetary)
            changes=terms_delta(scenario,estimate,product['list_price'],completion=success)
            result.append({"id": shop["name"] + ":" + sku, "shop": shop["url"], "name": shop["name"],
                           "sku": sku, "product": product, "reference_price": estimate,
                           "score": score * (1.03 if hint else 1),
                           'expected_delta': changes,
                           'avoid_units':blocked_units(journal,shop['url'],sku,order),
                           "evidence": {"own_paid": len(own), "peer_failures": len(failed_orders),
                                        'public_sku_prices': observed,
                                        "forum_announcement_hint": hint, "forum_hint_is_unverified": True,
                                        'budget_feasibility_scenario': affordability,
                                        'route_availability':route,
                                        "price_is_prior": not bool(own) and not bool(observed)}})
            if (config or {}).get('buyer_contract_routing_enabled'):
                from .buyer_routes import adjust
                adjust(result[-1],order,journal,config,route_context)
            if (config or {}).get('buyer_throughput_enabled'):
                from .buyer_execution import route_adjust
                route_adjust(result[-1],order,journal,config,execution_context)
                result[-1]['checkout_buffer_seconds']=config.get('checkout_buffer_seconds',30)
    from .reversal import route_key
    result=sorted(result,key=lambda x:route_key(x,journal,config or {}),reverse=True)
    from .contracts import prepare
    from .reversal import prune_routes
    return prepare(prune_routes(result,order,journal,config or {}),order,journal,config or {})


def supply_open(inv, now=None):
    now = time.time() if now is None else now
    window = inv.get('restock')
    if window is None:
        return True  # Older inventories do not expose a window; the server remains authoritative.
    if not isinstance(window, dict) or window.get('open') is not True:
        return False
    return (type(window.get('opens_at')) in (int, float) and type(window.get('closes_at')) in (int, float)
            and window['opens_at'] <= now < window['closes_at'] - 15)


def cash_capacity(inv, journal, config, now=None):
    now = time.time() if now is None else now
    days = reserve_days(config, now)
    # A confirmed full refund has already reduced the authoritative balance.
    # Keep its raw history, but do not collateralize the discharged obligation twice.
    closed = {}
    for e in journal.events('public_broken', 20000):
        amount = e.get('refunded')
        if (0 <= now - e['_ts'] and e.get('unit') and (e.get('item') or e.get('sku')) and
                type(amount) in (int, float) and math.isfinite(amount) and amount > 0):
            key = (e['unit'], e.get('item') or e.get('sku'))
            closed[key] = max(closed.get(key, 0), amount)
    pending = [e for e in journal.events('shop_settled', 10000)
               if 0 <= now - e.get('paid_at', 0) < warranty_hours(e.get('warranty_hours')) * 3600]
    released = sum(e.get('price', 0) for e in pending
                   if closed.get((e.get('unit'), e.get('sku')), 0) >= e.get('price', 0) > 0)
    liability = sum(e.get('price', 0) for e in pending) - released
    storage = sum(u.get('cost', 0) for u in inv.get('units', []) if u.get('kind') == 'goods') * .01 * days
    balance = inv.get('balance', 0)
    from .limits import capital_caps
    floor = max(config.get('cash_floor', 750000), balance * config['cash_reserve_fraction']) if capital_caps(config) else 0
    schedule = {}
    for e in pending:
        if closed.get((e.get('unit'), e.get('sku')), 0) < e.get('price', 0):
            expiry = e.get('paid_at', 0)+warranty_hours(e.get('warranty_hours'))*3600
            bucket = int(expiry//1200)*1200
            schedule[bucket] = schedule.get(bucket, 0)+e.get('price', 0)
    return {'spendable': max(0, balance - floor - liability - storage), 'refund_liability': liability,
            'storage_to_end': storage, 'cash_floor': floor, 'remaining_days': days,
            'reserve_release_schedule': [{'at': k+1200, 'amount': v} for k,v in sorted(schedule.items())],
            'discharged_refund_reserve': released}


@decision
def restock_candidates(inv, catalog, demand, journal, config, now=None):
    now = time.time() if now is None else now
    velocity=config.get('shop_velocity_enabled',False)
    strategic=bool(not config.get('finalday_departments_enabled') and config.get('inventory_portfolio_v27_enabled') and velocity and config.get('shop_championship_enabled')
        and config.get('shop_inventory_caps_enabled') is False and config.get('shop_internal_capital_caps_enabled') is False)
    end = now + planning_hours(config, now)*3600
    if end - now < 3600 or not supply_open(inv, now):
        return []
    units = [u for u in inv.get("units", []) if remaining(u, now) > 0]
    count = {}
    for u in units:
        count[u["sku"]] = count.get(u["sku"], 0) + 1
    reserved_counts={}
    if config.get('book_growth_v23_enabled'):
        with journal.connect() as db:
            reserved={r[0] for r in db.execute('SELECT unit FROM reservations WHERE until>?',(now,))}
        from collections import Counter
        reserved_counts=Counter(u['sku'] for u in units if u.get('unit') in reserved)
    from .shop_growth import inventory_limit,stock_caps_enabled
    target = inventory_limit(config)
    if target is not None and len(units) >= target:
        return []
    capacity = cash_capacity(inv, journal, config, now)
    if config.get('market_control_enabled'):
        control=journal.get('market_control',{})
        if control.get('pause_restock'):
            return []
        fraction=max(0,min(1,control.get('capital_fraction',1)))
        capacity=dict(capacity,spendable=capacity['spendable']*fraction)
    spendable = capacity['spendable']
    from .market_memory import market_book
    book = market_book(journal, catalog, now)
    merchant = {}
    if config.get('shop_growth_v5'):
        from .merchant import context
        merchant = context(journal, catalog, now,config=config)
    signals = {}
    for e in journal.events("public_settle", 20000):
        if e.get('buyer') == 'seochanit' or not 0 <= now-e['_ts'] <= 21600:
            continue
        product = catalog.get(e.get("item") or e.get("sku"), {})
        cat = product.get("category")
        if cat:
            signals[cat] = signals.get(cat, 0) + math.exp(-(now - e["_ts"]) / 21600)
    for e in journal.events("stockout", 2000):
        product = catalog.get(e.get("sku"), {})
        if product and 0 <= now - e['_ts'] < 21600:
            signals[product["category"]] = signals.get(product["category"], 0) + .35 * math.exp(-(now-e['_ts']) / 3600)
    from .forum import signals as forum_signals
    forum = forum_signals(journal, now)
    for category, value in forum['categories'].items():
        signals[category] = signals.get(category, 0) + min(value, 3)
    own = {}
    for event in journal.events('shop_settled', 10000):
        if 0 <= now - event.get('paid_at', event['_ts']) < 3600:
            own[event.get('sku')] = own.get(event.get('sku'), 0) + 1
    costs = {}
    for e in journal.events('shop_settled', 10000) + units:
        product = catalog.get(e.get('sku'), {})
        if product.get('list_price') and type(e.get('cost')) in (int, float) and e['cost'] > 0:
            costs.setdefault(e['sku'], []).append(e['cost'] / product['list_price'])
    candidates = []
    # A deterministic rotating probe avoids presenting unobserved demand as fact.
    probe = int(now // 3600)
    for sku, p in catalog.items():
        fee = .01 * capacity['remaining_days'] if p.get('kind') == 'goods' else 0
        factor = surge_factor(inv, sku)
        per_unit = supply_bound(p, inv, sku)
        commitment = per_unit * (1 + fee)
        if (stock_caps_enabled(config) and count.get(sku, 0) >= config["restock_max_qty"]) or commitment > spendable:
            continue
        if p.get("kind") == "food" and (p.get("shelf_hours") or 0) < 6:
            continue
        cat_signal = signals.get(p["category"], 0)
        import hashlib
        draw = int(hashlib.sha256((str(probe) + sku).encode()).hexdigest()[:8], 16) / 2**32
        scarcity = 1 / (1 + count.get(sku, 0))
        weight = config.get('category_weights', {}).get(p['category'], 1)
        exact = min(forum['skus'].get(sku, 0), 2)
        # Cover observed own sales through the next supply window, in small lots.
        observed = book.get(sku, {})
        growth = merchant.get('skus', {}).get(sku, {})
        rate = (growth.get('arrival_rate_scenario', 0) if merchant else
                max(own.get(sku, 0), .35 * observed.get('per_seller_per_hour', 0)))
        trend=config.get('shop_sku_trend_factors',{}).get(sku,1) if config.get('objective_feedback_enabled') and not config.get('shop_adaptive_demand_enabled') else 1
        rate*=trend
        if velocity:
            from .shop_growth import target_stock
            paid=growth.get('positive_paid_while_available',0)
            if paid>=3:rate=max(.01,growth.get('paid_arrival_rate_scenario',rate/max(.01,trend))*trend)
            public_qualified=config.get('objective_feedback_enabled') and sku in config.get('shop_public_demand_skus',[])
            coverage=None
            if config.get('shop_adaptive_demand_enabled'):
                from .demand_policy import coverage_hours
                probability=(growth.get('demand_calibration') or {}).get('supply_success_scenario',2/3)
                coverage=coverage_hours(inv,probability,config,now)
                if p.get('kind')=='food':coverage=min(coverage,p.get('shelf_hours') or coverage)
            desired=target_stock(rate,paid,config,public_qualified=public_qualified,
                public_limit=config.get('shop_public_demand_caps',{}).get(sku,6),coverage_hours=coverage)
        else:
            desired = max(1, math.ceil(rate * config.get('supply_cycle_seconds', 1200) / 3600 * 1.5))
        buffer=reserved_counts.get(sku,0) if (growth.get('service_loss_adjustment') or {}).get('qualified') else 0
        needed=desired+buffer-count.get(sku,0)
        service_need=None
        if velocity and config.get('service_contract_v26_enabled'):
            from .service_policy import need_plan
            service_need=need_plan(journal,config,sku,p,units,rate,desired,coverage,paid,now)
            if service_need:needed=max(needed,service_need['needed'])
        baseline_needed=needed
        depth_supported=False
        if strategic:
            from .inventory_portfolio import supported
            depth_supported=supported(growth,observed,now)
            if p.get('kind')=='food' and .45*p.get('shelf_hours',0)<min(6,(end-now)/3600):depth_supported=False
            if depth_supported:needed=max(needed,20)
        if needed<=0:
            continue
        held_room=config['restock_max_qty']-count.get(sku,0) if stock_caps_enabled(config) else 20
        qty = min(20, held_room,
                  needed, max(0, math.floor(spendable / commitment)))
        if qty <= 0:
            continue
        # Affordability still reserves worst-case storage THROUGH THE END. The
        # objective uses expected holding time instead, so quick-turn goods do
        # not pay two weeks of hypothetical storage in every ranking decision.
        probe_sku = observed.get('external_samples', observed.get('samples', 0)) < 3 and own.get(sku, 0) < 2
        hours = min(6, max(0, (end - now) / 3600))
        if probe_sku:
            sale_probability = .25  # Bounded discovery scenario; never pretend unknown demand is certain.
        else:
            sale_probability = min(.98, 1 - math.exp(-max(.01, rate) * hours))
        holding_hours = min(hours, max(config.get('supply_cycle_seconds', 1200) / 3600,
                                        1 / max(.01, rate)))
        # Unknown supplier costs use the midpoint of the published weighted
        # expectation bounds as a planning scenario, NOT an assumed distribution.
        ratios = costs.get(sku, [])
        prior_cost = .50 if p.get('kind') == 'food' else .59
        cost_lower = .40 if p.get('kind') == 'food' else .49
        # Sold units are a selected sample. Shrink sparse own observations and
        # keep the published grade-mixture expectation bounds for a NEW draw.
        cost_ratio = max(cost_lower, min(.60 if p.get('kind') == 'food' else .69,
                          (sum(ratios[:20]) + 5 * prior_cost) / (len(ratios[:20]) + 5)))
        if config.get('shop_championship_enabled'):
            # Past sold units are selected by grade and historical surge.
            # Their cost ratio cannot be re-taxed by today's surge factor.
            cost_ratio=prior_cost
        cost_upper = (.60 if p.get('kind') == 'food' else .69)*factor
        cost_ratio *= factor
        price = min(p['list_price'], observed.get('median', .85 * p['list_price']))
        refund_stress = .15 if p.get('kind') == 'goods' else 0
        terms={}
        if velocity:
            from .shop_growth import resale_terms
            terms=resale_terms(p,observed,journal,config,now)
            price,refund_stress=terms['price'],terms['refund_stress']
            if config.get('reversal_v28_enabled') and p.get('kind')=='goods' and observed.get('external_samples',0)>=20:
                # A historical high-price segment is a smaller observed market,
                # never the same traffic at an arbitrarily raised asking price.
                alternatives=[(terms,rate)]
                for quantile in (.75,.9):
                    option=resale_terms(p,observed,journal,config,now,quantile=quantile)
                    alternatives.append((option,rate*option['observed_price_segment_fraction']))
                def option_value(row):
                    t,r=row;prob=1-math.exp(-max(0,r)*hours)
                    storage=cost_ratio*p['list_price']*.01*min(hours,max(1/3,1/max(.01,r)))/24
                    return prob*(t['price']*(1-t['refund_stress'])-cost_ratio*p['list_price'])-storage
                terms,rate=max(alternatives,key=option_value)
                price,refund_stress=terms['price'],terms['refund_stress']
                sale_probability=min(.98,1-math.exp(-max(.01,rate)*hours))
                holding_hours=min(hours,max(config.get('supply_cycle_seconds',1200)/3600,1/max(.01,rate)))
        expected_storage = cost_ratio * p['list_price'] * .01 * holding_hours / 24 if fee else 0
        # Small lots: only own observed repeat sales justify multiple new units.
        if probe_sku and stock_caps_enabled(config):
            qty = 1
        existing_exposure = sum(u.get('cost', 0) * (1 + fee) for u in units if u['sku'] == sku)
        max_exposure = min(spendable, max(0, inv.get('balance', 0) *
                           config.get('max_sku_capital_fraction', .15) - existing_exposure))
        from .limits import capital_caps
        if not capital_caps(config):max_exposure=spendable
        if velocity:
            working=sum(u.get('cost',0) for u in units)+spendable
            if capital_caps(config):max_exposure=min(max_exposure,max(0,working*config.get('max_sku_capital_fraction',.35)-existing_exposure))
        if probe_sku and capital_caps(config):
            max_exposure = min(max_exposure, capacity['spendable'] * config['exploration_fraction'])
        qty = min(qty, max(0, math.floor(max_exposure / commitment)))
        if qty <= 0:
            continue
        expected_revenue = qty * sale_probability * price * (1 - refund_stress)
        # Unsold stock retains acquisition value. Only sales remove its cost;
        # food additionally risks expiration within the rolling horizon.
        expiry_stress = (1-sale_probability)*.25 if p.get('kind') == 'food' else 0
        expected_profit = expected_revenue - qty * ((sale_probability+expiry_stress)*cost_ratio*p['list_price'] + expected_storage)
        if expected_profit <= 0:
            continue
        exploration_weight = config['exploration_fraction'] * draw if probe_sku else 1
        score = weight * scarcity * exploration_weight * expected_profit
        candidates.append({"id": sku, "sku": sku, "qty": qty, "score": score,
                           "max_cost": per_unit * qty, "capital_commitment": commitment * qty, "category": p["category"],
                           'expected_delta': {'book': expected_profit, 'profit': expected_profit, 'revenue': expected_revenue},
                           "evidence": {"observed_category_signal": cat_signal, "probe": cat_signal == 0,
                                        "forum_request_signal": exact, 'own_sales_last_hour': own.get(sku, 0),
                                        'cash_capacity': capacity, 'public_sku_trades': observed, 'supply_surge_factor': factor,
                                        'worst_case_unit_cost': per_unit,
                                        'sale_probability_scenario': sale_probability, 'expected_holding_hours': holding_hours,
                                        'cost_ratio_scenario': cost_ratio, 'cost_is_observed': bool(ratios),
                                        'own_cost_samples': len(ratios[:20]), 'cost_prior_pseudo_samples': 5,
                                        'expected_profit_scenario': expected_profit,
                                        'upper_cost_profit_stress': expected_revenue - qty * (cost_upper * p['list_price'] + expected_storage),
                                        'refund_stress': refund_stress, 'exploration': probe_sku,
                                        "adaptive_category_weight": weight}})
        candidates[-1]['evidence'].update(kind=p.get('kind'), list_price=p['list_price'],
                                           refund_upper_scenario=terms.get('refund_upper',refund_stress),
                                           uncapped_inventory=not stock_caps_enabled(config),
                                           adaptive_coverage_hours=coverage if velocity else None,
                                           growth=growth, arrival_rate_scenario=rate,
                                           reserved_pipeline_buffer=buffer,
                                           planning_hours=hours,scenario_unit_cost=cost_ratio*p['list_price'],
                                           resale_price_scenario=price,resale_terms_scenario=terms)
        if service_need:candidates[-1]['evidence']['service_eligibility']=service_need
        if config.get('inventory_portfolio_v27_enabled'):
            candidates[-1]['evidence']['v27_baseline_qty']=max(0,min(qty,baseline_needed))
            candidates[-1]['evidence']['v27_depth_supported']=depth_supported
        if config.get('objective_feedback_enabled'):candidates[-1]['evidence']['trend_factor_scenario']=trend
    from .meta import rank_progress
    board = journal.snapshot('board.json', {})
    if now - journal.get('board_checked_at', 0) > 300:
        board = {}
    for c in candidates:
        c['rank_progress_surrogate'] = rank_progress(board, 'shop', 'seochanit', c['expected_delta'])
    if config.get('finalday_departments_enabled'):
        from .departments import procurement_department
        return procurement_department(candidates,inv,capacity,config,journal,catalog,now)
    from .portfolio import allocate
    if strategic:
        from .inventory_portfolio import allocate as strategic_allocate
        packet={}
        selected=strategic_allocate(candidates,inv,capacity,dict(config,_v27_rules_hash=journal.get('market_rules',{}).get('sha256')),
            units,dict(merchant,_v27_board=board),now,audit=packet)
        if packet:
            journal.put('inventory-portfolio:v27',packet)
            journal.add('inventory_portfolio_prepared',packet,role='shop')
        return selected
    return allocate(candidates, inv, capacity, dict(config,inventory_target_units=target), units, merchant=merchant)
