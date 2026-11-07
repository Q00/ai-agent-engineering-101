"""First-hand sales evidence and explicit inventory-depth alternatives."""
from collections import Counter
import math
from procurement_cost import ceiling as acquisition_ceiling

from inventory_observation import timestamp, evidence, demand_outcomes, conversion_estimate
from newsvendor_tool import poisson_pmf, recommend_quantity
from capital_policy import MAX_UNIT_COST, operating_policy, target_inventory_cost
from market_scoring import continuation
from seller_warranty import economics, hours

PROVEN_STOCK = 20  # safety ceiling, never a desired inventory target
NEW_STOCK = 2
MAINTENANCE_CATEGORY_LIMIT = 24
MIN_POSITIVE_SALES = 2
MIDPRICE_MIN = 30_000
MIDPRICE_MAX = 300_000
SUPPLY_CYCLE_SECONDS = 20*60
SUPPLY_JITTER_BUFFER_SECONDS = 120
PURCHASE_LEAD_SECONDS = 60
GROWTH_COVERAGE_HOURS={'goods':(2.,6.),'food':(1.,2.)}


def sales_evidence(sku, history, now, *, kind=None):
    receipts = {e['unit']: e for e in history if e.get('event') == 'receipt_signed'
                and e.get('unit') and type(e.get('cost')) in (int, float) and e['cost'] > 0}
    settled = {e['ledger']: e for e in history if e.get('event') == 'settled' and e.get('sku') == sku
               and e.get('ledger') is not None and (timestamp(e.get('at')) or 0) >= now-12*3600
               and (timestamp(e.get('at')) or math.inf) <= now}
    matched = [e for e in settled.values() if e.get('unit') in receipts and type(e.get('price')) in (int, float)]
    positive = [e for e in matched if e['price'] > receipts[e['unit']]['cost']]
    midprice = [e for e in positive if MIDPRICE_MIN <= e['price'] <= MIDPRICE_MAX]
    recent = [e for e in positive if timestamp(e['at']) >= now-3600]
    spread = sum(e['price']-receipts[e['unit']]['cost'] for e in matched)
    contributions = []
    for sale in matched:
        receipt = receipts[sale['unit']]
        unit = {'kind': kind or ('goods' if receipt.get('warranty_hours') else None),
                'cost': receipt['cost'], 'supplied_at': timestamp(receipt.get('supplied_at'))}
        charges = economics(unit, sale['price'], now=timestamp(sale['at']), warranty_hours=hours(unit))
        contribution = sale['price']-receipt['cost']-charges['estimated_accrued_holding']-charges['refund_allowance']
        contributions.append((sale['price'], contribution))
    net_scenario = sum(value for _, value in contributions)
    positive_contributions = sum(value>0 for _,value in contributions)
    proven = positive_contributions >= MIN_POSITIVE_SALES and net_scenario > 0
    return {'confirmed_positive_spread_sales_12h': len(positive), 'confirmed_positive_spread_sales_1h': len(recent),
            'midprice_positive_sales_12h': len(midprice),
            'positive_contribution_sales_12h': positive_contributions,
            'midprice_contribution_sales_12h': sum(MIDPRICE_MIN <= price <= MIDPRICE_MAX and value>0
                                                  for price,value in contributions),
            'contribution_after_holding_refund_scenario_12h': round(net_scenario, 2),
            'spread_before_holding_refunds_12h': spread, 'proven': proven,
            'settled_sales_12h':len(settled), 'maintenance_eligible':proven, 'positive_sales_threshold_12h':MIN_POSITIVE_SALES,
            'stock_limit': PROVEN_STOCK if proven else NEW_STOCK,
            'caution': 'Contribution deducts estimated accrued goods storage and a goods refund stress allowance. '
                       'It does not identify actual refunds or spoiled unsold inventory.'}


def protection_hours(inventory, now):
    window = inventory.get('restock') or {}
    opens = window.get('opens_at')
    if type(opens) not in (int,float) or not math.isfinite(opens):
        seconds = SUPPLY_CYCLE_SECONDS
        source = 'nominal cycle; no window timestamp'
    elif window.get('open'):
        seconds = max(0., opens+SUPPLY_CYCLE_SECONDS-now)
        source = 'nominal next opening after the currently open window'
    else:
        seconds = max(0., opens-now)
        source = 'published next opening'
    hours = min(max(60.,seconds+SUPPLY_JITTER_BUFFER_SECONDS+PURCHASE_LEAD_SECONDS)/3600,
                operating_policy(now)['hours_to_end'])
    return {'hours':hours,
            'source':source, 'timing_buffer_seconds':SUPPLY_JITTER_BUFFER_SECONDS+PURCHASE_LEAD_SECONDS,
            'caution':'Future open-window timing and procurement delay are planning assumptions, not guarantees.'}


def _quantile(mean, level):
    cumulative = 0.
    for n, probability in enumerate(poisson_pmf(mean,cap=PROVEN_STOCK)):
        cumulative += probability
        if cumulative >= level:
            return n
    return PROVEN_STOCK


def demand_forecast(sku, product, inventory, history, now, *, outcomes=None):
    outcomes=demand_outcomes(history,now) if outcomes is None else outcomes
    conversion=conversion_estimate(sku,outcomes)
    windows = {}
    for hours in (1,3,12):
        observed = evidence(sku,history,now,float(hours),outcomes=outcomes)
        raw_rate = observed['confirmed_settlements']/hours
        lost_count=observed['eligible_lost_stockout_visits']
        expected_lost=lost_count*conversion['probability']
        observed_demand_rate=(observed['confirmed_settlements']+expected_lost)/hours
        exposure = observed['in_stock_exposure_hours']
        matched = observed['settlements_during_observed_stock']
        # Pair numerator and denominator. All sales / a short exposed interval
        # would exaggerate demand. Gaps and out-of-stock time are not zero demand.
        conditional = (matched+raw_rate*.5)/(exposure+.5) if exposure >= .25 else None
        corrected=max(raw_rate,conditional or 0.)
        windows[str(hours)] = {'settlements':observed['confirmed_settlements'],
                              'in_stock_exposure_hours':exposure, 'covered_settlements':matched,
                              'observed_stockouts':observed['observed_no_stock_visits'],
                              'eligible_lost_stockouts':lost_count,
                              'ambiguous_stockouts_excluded':observed['ambiguous_stockout_visits'],
                              'already_served_stockouts_excluded':observed['already_served_stockout_visits'],
                              'expected_lost_sales':round(expected_lost,4),
                              'sales_plus_expected_lost_rate':round(observed_demand_rate,4),
                              'exposure_adjusted_sales_rate':corrected,
                              'snapshot_coverage_hours':observed['snapshot_coverage_hours'],
                              # Exposure correction already estimates unobserved demand.
                              # Add lost requests only to raw sales, then take the max.
                              'rate_per_hour':max(observed_demand_rate,corrected),
                              'rate_source':('sales plus conversion-weighted stockouts' if observed_demand_rate>corrected else
                                  'paired exposure with shrinkage' if corrected>raw_rate else 'full-window observed sales lower bound')}
    if windows['1']['settlements']+windows['1']['expected_lost_sales'] >= 2:
        rate = .65*windows['1']['rate_per_hour']+.35*windows['3']['rate_per_hour']
        source = '65% recent 1h + 35% recent 3h'
    elif windows['3']['settlements']+windows['3']['expected_lost_sales']:
        rate = windows['3']['rate_per_hour']
        source = 'recent 3h; sparse 1h evidence'
    else:
        rate = windows['12']['rate_per_hour']
        source = 'recent 12h fallback; short-window absence is not proof of zero demand'
    if windows['1']['settlements']>=2:
        paid_rate=.65*windows['1']['exposure_adjusted_sales_rate']+.35*windows['3']['exposure_adjusted_sales_rate']
    elif windows['3']['settlements']:
        paid_rate=windows['3']['exposure_adjusted_sales_rate']
    else:
        paid_rate=windows['12']['exposure_adjusted_sales_rate']
    # Inferred losses can activate a recent window. That branch switch must
    # never erase the existing paid-sales signal from a different window.
    if paid_rate>rate:
        rate=paid_rate;source='paid-sales baseline retained; inferred losses do not lower demand'
    protection = protection_hours(inventory,now)
    mean = rate*protection['hours']
    operating = operating_policy(now)
    terminal_capacity = (max(0,math.floor(rate*operating['hours_to_end']))
                         if operating['phase'] != 'growth' else None)
    scenarios = []
    for name,factor,level in (('low',.8,.90),('base',1.,.95),('high',1.25,.975)):
        target = _quantile(mean*factor,level)
        scenarios.append({'scenario':name,'forecast_mean':round(mean*factor,4),'service_level':level,
                          'target_stock':target,'safety_ceiling_binding':target==PROVEN_STOCK})
    price = product.get('list_price') or 1
    cost = max(1,round(price*(.50 if product.get('kind')=='food' else .59)))
    sale = max(1,round(price*(.80 if product.get('kind')=='food' else .92)))
    holding = cost*.01*protection['hours']/24 if product.get('kind')=='goods' else 0.
    diagnostic = recommend_quantity(poisson_pmf(mean),sale,cost,holding_per_leftover=holding,max_qty=PROVEN_STOCK)
    retained = continuation(product,cost,0,protection['hours'],operating['hours_to_end'])
    book_diagnostic = recommend_quantity(poisson_pmf(mean),sale,cost,salvage=retained['value'],holding_per_leftover=holding,max_qty=PROVEN_STOCK)
    # The near-window forecast remains a diagnostic. Recovery also offers
    # explicit demand-derived buffers instead of assuming perfect replenishment
    # every twenty minutes. These are choices, never automatic buy quantities.
    coverage=[]
    growing=(operating['phase']=='growth' and
             sum(max(0,int(u.get('cost') or 0)) for u in inventory.get('units',[])
                 if u.get('grade') in ('A','B','C') and (u.get('expires_at') is None or u['expires_at']>now))
             <target_inventory_cost(inventory,now))
    if growing:
        for requested in GROWTH_COVERAGE_HOURS.get(product.get('kind'),()):
            hours=min(requested,operating['hours_to_end'])
            if product.get('kind')=='food':hours=min(hours,max(0,float(product.get('shelf_hours') or 0))/4)
            if hours<=protection['hours']:continue
            coverage.append({'scenario':f'cover_{hours:g}h','coverage_hours':hours,
                'forecast_mean':round(rate*hours,4),'service_level':.975,
                'target_stock':_quantile(rate*hours,.975),
                'holding_per_unit_at_assumed_cost':round(cost*.01*hours/24,2) if product.get('kind')=='goods' else 0.,
                'caution':'Catalog shelf life is not a guarantee of an incoming food unit remaining lifetime; '
                          'grade, expiry, breakage and holding risk still require model judgment.'})
    return {'sales_rate_per_hour':round(rate,4),'rate_source':source,'windows':windows,
            'paid_sales_only_rate_per_hour':round(paid_rate,4),
            'lost_demand_increase_per_hour':round(max(0,rate-paid_rate),4),
            'stockout_conversion':conversion,
            'demand_rate_policy':'max(exposure-adjusted sales rate, (sales + eligible stockouts * conversion) / hours); '
                                 'never add stockouts on top of exposure correction',
            'operating_phase':operating['phase'],'terminal_sale_capacity_estimate':terminal_capacity,
            'protection_period':protection,'demand_mean_until_next_replenishment':round(mean,4),
            'scenarios':scenarios,'maximum_target':max(s['target_stock'] for s in scenarios),
            'growth_coverage_options':coverage,
            'maximum_approval_target':max(s['target_stock'] for s in scenarios+coverage),
            'newsvendor_zero_continuation_diagnostic':diagnostic,
            'newsvendor_book_diagnostic':book_diagnostic,'book_continuation':retained,
            'assumption':'Poisson and service levels are unvalidated planning hypotheses. '
            'Near-window scenarios cover the next procurement opportunity; growth coverage options span multiple opportunities. '
            'Zero-continuation diagnostic is a pessimistic reference, not the Book objective. '
            'Terminal sale capacity is a diagnostic, not an unsold-stock liquidation constraint. '
            'remaining inventory is not actually discarded at this short horizon. '
            'Exposure correction describes this shop only, not total market demand.'}


def replenishment_candidates(catalog, inventory, history, now, blocked=(), trialed=(), pending=None):
    operating = operating_policy(now)
    if not operating['allow_replenishment']: return []
    units = [u for u in inventory.get('units', []) if u.get('grade') in ('A', 'B', 'C')
             and (u.get('expires_at') is None or u['expires_at'] > now)]
    stocks = Counter(u['sku'] for u in units)
    pending = pending or {}
    categories = Counter(u.get('category') for u in units)
    sold_skus = {e.get('sku') for e in history if e.get('event') == 'settled' and e.get('sku')}
    outcomes=demand_outcomes(history,now)
    rows = []
    for sku in sold_skus:
        p = catalog.get(sku)
        if sku in blocked or not isinstance(p, dict) or p.get('kind') not in ('food', 'goods'):
            continue
        signal = sales_evidence(sku, history, now, kind=p['kind'])
        price = p.get('list_price')
        reserved = pending.get(sku, 0)
        projected = stocks[sku] + reserved
        second_trial = (operating['allow_new_skus'] and not projected and sku not in trialed
                        and signal['settled_sales_12h'] == 1
                        and signal['positive_contribution_sales_12h'] == 1
                        and len({e.get('ledger') for e in history if e.get('event') == 'settled'
                                 and e.get('sku') == sku and e.get('ledger') is not None}) == 1)
        if ((not signal['proven'] and not second_trial) or projected >= PROVEN_STOCK or type(price) is not int
                or price <= 0 or acquisition_ceiling(p) > MAX_UNIT_COST):
            continue
        if p['kind'] == 'food' and (p.get('shelf_hours') or 0) < 48:
            continue
        reserved_category = sum(qty for key,qty in pending.items()
                                if catalog.get(key,{}).get('category') == p.get('category'))
        room = max(0, MAINTENANCE_CATEGORY_LIMIT-categories[p.get('category')]-reserved_category)
        forecast = demand_forecast(sku,p,inventory,history,now,outcomes=outcomes)
        options = []
        if second_trial and room:
            options = [{'target_stock':1, 'qty':1, 'forecast_target':None,
                        'scenario':'second_trial', 'service_level':None,
                        'cost_ceiling':acquisition_ceiling(p)}]
        for scenario in forecast['scenarios']+forecast['growth_coverage_options']:
            if second_trial: break
            target = scenario['target_stock']
            qty = min(max(0, target-projected),room)
            if qty > 0 and not any(o['qty']==qty for o in options):
                options.append({'target_stock': projected+qty, 'qty': qty,
                                'forecast_target':target,'scenario':scenario['scenario'],
                                'coverage_hours':scenario.get('coverage_hours',forecast['protection_period']['hours']),
                                'forecast_mean':scenario['forecast_mean'],
                                'service_level':scenario['service_level'],
                                'cost_ceiling': acquisition_ceiling(p)*qty})
        if not options:
            continue
        options.sort(key=lambda option:option['qty'])
        rows.append({'sku': sku, 'name': p.get('name', sku), 'kind': p['kind'], 'category': p.get('category'),
                     'midprice_priority': (operating['phase'] == 'growth' and
                                           signal['midprice_contribution_sales_12h'] >= MIN_POSITIVE_SALES),
                     'currently_stocked': stocks[sku], 'pending_stock':reserved,
                     'second_trial':second_trial, 'evidence': signal, 'target_options': options,
                     'cost_ceiling_per_unit': acquisition_ceiling(p), 'forecast':forecast,
                     'budget_scope':'replenishment: shared window and cash/risk limits; NOT rolling novel-exploration budget',
                     'assumption':('One additional unit after exactly one positive-contribution settlement; not proven maintenance.'
                                   if second_trial else 'Each target/quantity is forecast-derived; the model approves a candidate or holds.')})
    rows.sort(key=lambda r: (not r['midprice_priority'],
                             -(r['target_options'][-1]['qty']/max(1,r['currently_stocked'])),
                             -r['evidence']['confirmed_positive_spread_sales_1h'],
                             -r['evidence']['contribution_after_holding_refund_scenario_12h'], r['sku']))
    proven = [r for r in rows if not r['second_trial']]
    trials = [r for r in rows if r['second_trial']]
    trial_rows = trials[:2]
    return proven[:12-len(trial_rows)] + trial_rows


def policy_view(sku, product, inventory, history, now):
    signal = sales_evidence(sku,history,now,kind=product.get('kind') if product else None)
    if signal['maintenance_eligible']:
        forecast = demand_forecast(sku,product,inventory,history,now)
        return {**signal,'stock_limit':forecast['maximum_approval_target'],'forecast':forecast}
    return signal
