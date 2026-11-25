"""Model proposes a scenario, reviews calculations, and approves a bounded plan.

The market draws grade and actual cost only when the lot arrives. The cost used
here is the documented grade-weighted midpoint, and the forecast is labeled as
an estimate. Small caps protect the initial period with little evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import time

import httpx

from harness import decide_json, trace
from market_memory import events, record, sync_settlements, recent_trade_context
from context_compaction import RESTOCK_HISTORY_BYTES, text
from newsvendor_tool import poisson_pmf, recommend_quantity
import inventory_observation
import inventory_exploration
import inventory_portfolio
import replenishment_plan
from stock_policy import policy_view, NEW_STOCK
from capital_policy import MIN_CASH, operating_policy
from procurement_cost import ceiling as acquisition_ceiling
from market_scoring import continuation as book_continuation,BOOK_CONTRACT


COOLDOWN = Path("state/restock-cooldown.json")
PLAN = Path('state/restock-plan.json')
MIN_BALANCE = MIN_CASH
STARTUP_MAX_BUY = 2


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def fetch_market_sales() -> Counter:
    """Recent public Round 3 settlements, not a complete demand observation."""
    response = httpx.get("https://market.ourolabs.ai/api/ledger?after=-500", timeout=5.0)
    response.raise_for_status()
    page = response.json()
    if not isinstance(page, list):
        raise ValueError("unexpected public ledger response")
    return Counter(entry.get("data", {}).get("item") for entry in page
                   if isinstance(entry, dict) and entry.get("round") == 3
                   and entry.get("kind") == "settle"
                   and isinstance(entry.get("data"), dict)
                   and isinstance(entry["data"].get("item"), str))


def choose_candidates(catalog: dict, inventory: dict, history: list[dict],
                      market_sales: Counter | None = None) -> list[dict]:
    """Expose a bounded exploratory set plus SKUs with first-hand evidence."""
    market_sales = market_sales or Counter()
    observed_tasks = {}
    for row in history:
        if row.get('event') not in ('opening','request_seen','no_stock'):
            continue
        skus = [row['sku']] if row.get('event')=='opening' and row.get('sku') else row.get('requested_skus',[])
        if row.get('task_id'):
            for sku in skus:
                observed_tasks.setdefault(sku,set()).add(row['task_id'])
    counts = Counter({sku:len(tasks) for sku,tasks in observed_tasks.items()})
    settled = Counter(e.get("sku") for e in history if e.get("event") == "settled" and e.get("sku"))
    stocked = Counter(u.get("sku") for u in inventory.get("units", []) if u.get("grade") != "expired")
    categories = Counter(u.get('category') for u in inventory.get('units', []) if u.get('grade') != 'expired')
    signals = {sku:policy_view(sku,catalog[sku],inventory,history,time.time()) for sku in catalog if settled[sku]}
    products = {sku: data for sku, data in catalog.items()
                if isinstance(data, dict) and data.get('kind') in ('food', 'goods')
                and type(data.get('list_price')) is int and data['list_price'] > 0
                and (data['kind'] == 'goods' or (data.get('shelf_hours') or 0) >= 24)
                and stocked[sku] < signals.get(sku, {}).get('stock_limit', NEW_STOCK)
                and (not data.get('category') or categories[data['category']] < replenishment_plan.MAX_CATEGORY_STOCK)}
    if not operating_policy(time.time())['allow_new_skus']:
        products = {sku:p for sku,p in products.items() if signals.get(sku,{}).get('proven')}
    if not products:
        return []
    public = [sku for sku, _ in market_sales.most_common() if sku in products][:8]
    explored = [sku for sku, _ in counts.most_common() if sku in products][:8]
    successful = [sku for sku, _ in settled.most_common() if sku in products][:8]
    seed = int(time.time() // 3600)
    rng = random.Random(seed)
    sampled = []
    for kind in ('food', 'goods'):
        pool = sorted(s for s, p in products.items() if p['kind'] == kind)
        sampled.extend(rng.sample(pool, min(8, len(pool))))
    selected = list(dict.fromkeys(public + explored + successful + sampled))
    return [{"sku": sku, "name": products[sku].get("name", sku),
             "category": products[sku].get("category"), "kind": products[sku]['kind'],
             "list_price": products[sku]["list_price"],
             "shelf_hours": products[sku].get("shelf_hours"),
             'sales_evidence':signals.get(sku, {}), 'stock_limit':signals.get(sku, {}).get('stock_limit', NEW_STOCK),
             "currently_stocked": stocked[sku], "observed_shop_requests": counts[sku],
             "confirmed_settlements": settled[sku],
             "recent_public_settlements": market_sales[sku]} for sku in selected]


def decision_schema() -> dict:
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["buy", "hold"]},
        "sku": {"type": ["string", "null"]},
        "expected_sale_price": {"type": ["integer", "null"]},
        "demand_mean": {"type": ["number", "null"]},
        "continuation_value": {"type": ["number", "null"]},
        "reason": {"type": "string"},
    }, "required": ["action", "sku", "expected_sale_price", "demand_mean", "continuation_value", "reason"],
        "additionalProperties": False}


def approval_schema(table: list[dict]) -> dict:
    return {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['approve', 'hold', 'replan']},
        'candidate_id': {'type': ['string','null'], 'enum': [None]+[c['id'] for c in table]},
        'reason': {'type': 'string'}},
        'required': ['action','candidate_id','reason'],
        'additionalProperties': False}


def inventory_fresh(path: Path, now: float) -> bool:
    try:
        return 0 <= now-path.stat().st_mtime <= inventory_observation.MAX_SNAPSHOT_AGE
    except OSError:
        return False


def observation_view(observed: dict) -> dict:
    """Numeric evidence only; the shared observation caveat is stated once."""
    return {k:observed[k] for k in ('window_hours','observed_visits','observed_no_stock_visits',
                                   'confirmed_settlements','snapshot_coverage_hours',
                                   'in_stock_exposure_hours','observed_visit_rate_while_stocked')}


def quantity_candidates(mean: float, price: int, cost: int, stocked: int, cap: int,
                        continuation_value: float = 0., holding_per_leftover: float = 0., stock_limit: int = NEW_STOCK) -> list[dict]:
    table = [{'id':'hold','qty':0,'target_stock':stocked,'expected_incremental_book_change':0,
              'assumption':'wait until next review; not a promise of zero future sales'}]
    seen = {0}
    for label, factor in [('low',.65),('base',1.),('high',1.5)]:
        pmf = poisson_pmf(mean*factor)
        calc = recommend_quantity(pmf,price,cost,salvage=continuation_value,
                                  holding_per_leftover=holding_per_leftover,max_qty=stock_limit)
        qty = min(cap,max(0,calc['quantity']-stocked))
        if qty in seen:
            continue
        seen.add(qty)
        incremental = sum(prob*(price*(min(d,stocked+qty)-min(d,stocked))
                               + (continuation_value-holding_per_leftover)*(max(stocked+qty-d,0)-max(stocked-d,0))-cost*qty)
                          for d,prob in enumerate(pmf))
        table.append({'id':label,'qty':qty,'target_stock':stocked+qty,
                      'forecast_mean':round(mean*factor,3),'calculation':calc,
                      'expected_incremental_book_change':round(incremental,2),
                      'assumption':'scenario estimate; fixed sale price, existing stock assumed saleable; '
                                   'continuation is the supplied Book-retention/future-value scenario, not a cash buyback; '
                                   'actual grade, expiry and cost can change realized outcome'})
    return table


def plan_candidates(table: list[dict], observation: dict, horizon_hours: float, mean: float) -> list[dict]:
    """Compute lease/budget alternatives before asking the model to approve.

    Extra budget is permission to replace SOLD stock, not an advance purchase.
    The selected-shop visit/conversion estimate is a labeled planning hypothesis.
    """
    rate = observation['observed_visit_rate_while_stocked']
    if rate is not None and observation['observed_visits']:
        rate *= min(1.,observation['confirmed_settlements']/observation['observed_visits'])
    else:
        rate = mean/horizon_hours
    total_cap = 2 if observation['observed_visits']<5 and not observation.get('proven') else 20
    options=[]
    for row in table:
        if not row['qty']:
            options.append({**row,'valid_for_seconds':180,'max_total_qty':0})
            continue
        for ttl in (180,600):
            expected=rate*ttl/3600
            repeat=max(1,math.ceil(expected+1.3*math.sqrt(expected)))
            for mode,budget in [('tight',row['qty']),('repeat',min(total_cap,row['qty']+repeat))]:
                if mode=='repeat' and budget==row['qty']:
                    continue
                options.append({**row,'id':f"{row['id']}-{ttl}-{mode}",'quantity_candidate_id':row['id'],
                                'valid_for_seconds':ttl,'max_total_qty':budget,
                                'expected_replacement_sales_during_validity':round(expected,3),
                                'model_calls_per_10min_if_same_plan_is_renewed':2*math.ceil(600/ttl),
                                'authorization_note':'repeat budget is not purchased up front; target stock and initial per-order limit stay fixed'})
    return options


def run(state: Path) -> dict:
    inventory_path = state / 'inventory.json'
    inventory = load_json(inventory_path, {})
    if inventory.get("round", 0) != 3:
        return {"buy": []}
    now = time.time()
    operating = operating_policy(now)
    if not operating['allow_replenishment']:
        record('restock_hold',reason='market_closing',operating_policy=operating)
        return {'buy':[]}
    if not inventory_fresh(inventory_path,now):
        record('restock_hold',reason='stale_inventory')
        return {'buy':[]}
    history = events()
    if inventory_observation.snapshot(inventory,inventory_path,history,now):
        history = events()
    live_units = [u for u in inventory.get("units", []) if u.get("grade") != "expired"]
    try:
        imported = sync_settlements()
        if imported:
            trace("settlements_imported", count=imported)
    except OSError as error:
        trace("settlement_sync_error", error=str(error))
    history = events()
    catalog = load_json(state / "catalog.json", {})
    # The finite startup experiment is separate from profit-based replenishment.
    # Old/offline fixtures without window metadata retain the legacy single-SKU path.
    if isinstance(inventory.get('restock'), dict):
        exploration_state = load_json(inventory_exploration.STATE, {})
        public = Counter()
        if not exploration_state.get('completed') and exploration_state.get('basket') is None:
            try:
                public = fetch_market_sales()
            except (httpx.HTTPError, ValueError):
                pass
        exploration = inventory_exploration.run(inventory, catalog, history, public, now,
                                                model=decide_json,
                                                refresh=lambda: (load_json(inventory_path, {}), time.time(),
                                                                 inventory_fresh(inventory_path, time.time())))
        if exploration is not None:
            return exploration
        def refresh_portfolio():
            current = load_json(inventory_path, {})
            at = time.time()
            return current, at, (inventory_fresh(inventory_path, at)
                                 and load_json(state/'catalog.json', {}) == catalog)
        portfolio = inventory_portfolio.run(inventory, catalog, history, time.time(),
                                             model=decide_json, refresh=refresh_portfolio)
        if portfolio is not None:
            return portfolio
    cached = replenishment_plan.load(PLAN)
    invalidated_plan = False
    if cached and cached.get('sku') in catalog:
        sku = cached['sku']
        observation = {**inventory_observation.evidence(sku,history,now), **policy_view(sku,catalog[sku],inventory,history,now)}
        result, reason = replenishment_plan.execute(cached,inventory=inventory,product=catalog[sku],
                                                    observation=observation,now=now,snapshot_time=inventory_path.stat().st_mtime)
        if result is not None:
            replenishment_plan.save(PLAN,cached)  # reserve budget before emitting intent
            record('restock_plan_execution',plan_id=cached['id'],sku=sku,result=result,
                   emitted_qty=cached['emitted_qty'],reason=reason)
            return result
        invalidated_plan = reason in {'new_stockout_evidence','observed_visit_rate_changed',
                                     'grade_changed','expiry_requires_reassessment','catalog_changed'}
        record('restock_plan_invalidated',plan_id=cached.get('id'),reason=reason)
        PLAN.unlink(missing_ok=True)
    cooldown = load_json(COOLDOWN, {})
    if not invalidated_plan and time.time() - cooldown.get("at", 0) < 180:
        return {"buy": []}
    try:
        public_sales = fetch_market_sales()
    except (httpx.HTTPError, ValueError) as error:
        trace("market_signal_unavailable", error=type(error).__name__)
        public_sales = Counter()
    candidates = choose_candidates(catalog, inventory, history, public_sales)
    if not candidates:
        return {"buy": []}
    recent = recent_trade_context(history,{c['sku'] for c in candidates},max_bytes=RESTOCK_HISTORY_BYTES)
    prompt = {
        "phase": "restock", "task_id": f"restock-{int(time.time())}",
        'operating_policy':operating,
        "balance": inventory.get("balance"), "candidate_skus": candidates,
        'portfolio_limits': {'maximum_stock_per_sku': replenishment_plan.MAX_SKU_STOCK,
                             'maximum_stock_per_category': replenishment_plan.MAX_CATEGORY_STOCK,
                             'maximum_total_stock': None},
        'risk_capacity': replenishment_plan.inventory_risk(inventory, now),
        'new_lot_economics': {'food_grade_probabilities': {'A': .25, 'B': .5, 'C': .25},
                              'goods_grade_probabilities': {'A': .7, 'B': .2, 'C': .1},
                              'cost_midpoints_of_list': {'A': .65, 'B': .5, 'C': .35},
                              'unvalidated_sale_anchors_of_list': {'A': 1., 'B': .8, 'C': .6},
                              'expected_cost_of_list': {'food': .5, 'goods': .59},
                              'goods_storage_per_day_of_cost': .01,
                              'anchor_weighted_sale_of_list': {'food': .8, 'goods': .92}},
        "recent_first_hand_evidence": recent,
        'observation_contract':'Selected shop visits, not total demand. Missing exposure is unknown, not zero. '
                               'Snapshot coverage does not prove listing visibility. Rates are conditioned on observed stock.',
        'observation_by_sku': {c['sku']:{'recent_3h':observation_view(inventory_observation.evidence(c['sku'],history,now,3.)),
                                        'recent_12h':observation_view(inventory_observation.evidence(c['sku'],history,now,12.))}
                               for c in candidates},
        "objective": "Choose a food or goods SKU to replenish, or hold. Estimate a sale price and "
                     "demand mean over the conservative horizon of one quarter of its "
                     "listed shelf life, capped at 24 hours; goods use a 24-hour review horizon. "
                     "For goods, consider 1% daily acquisition-cost storage and unknown post-sale breakage. "
                     "The tool charges a full horizon of storage on leftover goods; sold-unit holding and "
                     "unknown warranty losses are not modeled by that approximation. The market has not published "
                     "demand weights. Public settlements are sales, not all demand; "
                     "a shop request is only an observed visit. With sparse evidence "
                     "choose a low estimate and a "
                     "small exploratory lot. You will review quantity calculations in a second step, "
                     "then approve a bounded replenishment plan. expected_sale_price must estimate "
                     "the NEW lot across possible grades, not copy a current C-grade unit quote. "
                     "Use grade-specific evidence and the explicit mixture priors; anchors are unvalidated. "
                     "Estimate continuation_value per unsold new unit after this horizon: uncertain net "
                     "future sale revenue before expiry, after grade decay and future costs. It is NOT "
                     "a market buyback and is not guaranteed. Set zero if unsupported; it is capped "
                     "at expected purchase cost. Do not assume a still-saleable unit is physically "
                     "discarded merely because this short planning horizon ends. Missing observations are UNKNOWN, "
                     "not zero demand. demand.json lists categories without demand weights.",
    }
    prompt['objective'] += (' '+BOOK_CONTRACT+' Limit the forecast horizon to hours_to_end if shorter. '
                            'Goods unsold at round end retain acquisition-cost Book credit, not sale proceeds. '
                            'Food credit requires it to survive without spoilage; do not invent survival odds. '
                            'The operator prefers active capital turnover with positive acquisition spread, '
                            'revenue and honest buyer satisfaction over idle cash or extreme per-sale markups. '
                            'Ranking weights apply to percentiles, not raw units.')
    reusable_scenario = (not invalidated_plan and isinstance(cooldown.get('scenario'),dict)
                         and 0 <= now-cooldown.get('scenario_at',0) < 1800
                         and cooldown.get('policy_version') == replenishment_plan.POLICY_VERSION
                         and cooldown.get('sku') in catalog
                         and cooldown.get('product_signature') == replenishment_plan.product_signature(catalog[cooldown['sku']])
                         and any(c['sku']==cooldown['sku'] for c in candidates))
    try:
        if reusable_scenario:
            decision = cooldown['scenario']
            trace('restock_scenario_reused',sku=decision.get('sku'),age_seconds=round(now-cooldown['scenario_at']))
        else:
            decision = decide_json("restocking agent", prompt, decision_schema())
        fallback = False
    except Exception as error:
        trace("restock_model_fallback", error=type(error).__name__, reason=str(error)[-400:])
        decision = {"action": "hold", "sku": None, "expected_sale_price": None,
                    "demand_mean": None, "reason": "model unavailable; avoid speculative purchase"}
        fallback = True
    COOLDOWN.parent.mkdir(parents=True, exist_ok=True)
    scenario_sku = decision.get('sku')
    cooldown_data = {'at':time.time(),'sku':scenario_sku,'policy_version':replenishment_plan.POLICY_VERSION}
    if decision.get('action')=='buy' and scenario_sku in catalog:
        cooldown_data.update({'scenario':decision,
                              'scenario_at':cooldown['scenario_at'] if reusable_scenario else time.time(),
                              'product_signature':replenishment_plan.product_signature(catalog[scenario_sku])})
    COOLDOWN.write_text(json.dumps(cooldown_data),
                        encoding="utf-8")
    selected = next((c for c in candidates if c["sku"] == decision.get("sku")), None)
    if decision["action"] != "buy" or selected is None:
        record("restock_decision", action="hold", model_choice=decision, fallback=fallback)
        return {"buy": []}
    list_price = selected["list_price"]
    proposed_price = decision.get("expected_sale_price")
    price = (max(1, min(2 * list_price, proposed_price))
             if type(proposed_price) is int else round(list_price * 0.8))
    proposed_mean = decision.get("demand_mean")
    mean = float(proposed_mean) if type(proposed_mean) in (int, float) and math.isfinite(proposed_mean) else 0.0
    observation = {**inventory_observation.evidence(selected['sku'],history,now), **policy_view(selected['sku'],catalog[selected['sku']],inventory,history,now)}
    sparse = observation['observed_visits'] < 5 and not observation['proven']
    forecast_cap = 20. if observation['proven'] else 1.5 if sparse else 10.
    mean = max(0.0, min(mean, forecast_cap))
    # Food grade probabilities: A .25, B .50, C .25. Midpoint cost shares:
    # A .65, B .50, C .35, giving an expected purchase cost of .50 list price.
    expected_cost = round(list_price * (.59 if selected['kind'] == 'goods' else .50))
    residual = decision.get('continuation_value')
    continuation = (max(0., min(expected_cost, float(residual)))
                    if type(residual) in (int, float) and math.isfinite(residual) else 0.)
    horizon_hours = 24.0 if selected['kind'] == 'goods' else min(24.0, selected['shelf_hours'] * .25)
    horizon_hours = min(horizon_hours,max(0.,operating['hours_to_end']))
    retained = book_continuation(catalog[selected['sku']],expected_cost,continuation,horizon_hours,operating['hours_to_end'])
    continuation = retained['value']
    holding = expected_cost * .01 * horizon_hours / 24 if selected['kind'] == 'goods' else 0.
    calculation = recommend_quantity(poisson_pmf(mean), price, expected_cost,
                                     salvage=continuation, holding_per_leftover=holding,
                                     max_qty=observation['stock_limit'])
    balance = replenishment_plan.inventory_risk(inventory, now)['available_to_invest']
    affordable = int(balance // acquisition_ceiling(catalog[selected['sku']]))
    category_stock = sum(u.get('category') == selected.get('category') for u in live_units) if selected.get('category') else 0
    cap = max(0, min(affordable,
                     observation['stock_limit']-selected['currently_stocked'],
                     replenishment_plan.MAX_CATEGORY_STOCK-category_stock,
                     STARTUP_MAX_BUY if sparse else 20))
    quantities = quantity_candidates(mean,price,expected_cost,selected['currently_stocked'],cap,continuation,holding,observation['stock_limit'])
    table = plan_candidates(quantities,observation,horizon_hours,mean)
    proposal={**decision,'reason':text(decision.get('reason'),160)}
    approval_prompt = {'phase':'approve_replenishment_plan','scenario_proposal':proposal,
                       'sku':selected['sku'],'horizon_hours':horizon_hours,'inventory_balance':inventory.get('balance'),
                       'market_evidence':{'recent_public_settlements':selected['recent_public_settlements'],
                                          'source':'latest 500 public Round 3 ledger entries; sales, not all demand',
                                          'currently_stocked':selected['currently_stocked']},
                       'effective_forecast_mean':mean,
                       'continuation_value':continuation,
                       'holding_per_leftover':holding,
                       'portfolio_limits':prompt['portfolio_limits'],
                       'continuation_value_contract':retained['contract'],'book_continuation':retained,
                       'forecast_policy_cap':forecast_cap,
                       'observation':observation,'quantity_candidates':table,
                       'scenario_age_seconds':round(now-cooldown.get('scenario_at',now)) if reusable_scenario else 0,
                       'scenario_status':'temporary model hypothesis, not an observed fact',
                       'sparse_observation_cap_applies':sparse,
                       'maximum_authorized_total_qty':2 if sparse else 20,
                       'model_call_tradeoff':{'new_plan':2,'stable_scenario_renewal':1,'valid_plan_reuse':0,
                                              'goal':'minimize repeated calls subject to the same bounded inventory risk'},
                       'existing_units':[{'grade':u.get('grade'),'cost':u.get('cost'),
                                          'hours_left':round(max(0,u['expires_at']-now)/3600,2) if u.get('expires_at') else None}
                                         for u in live_units if u.get('sku')==selected['sku']],
                       'objective':'Approve one calculated candidate or hold. You own the final quantity decision. '
                                   'Zero own visits with zero stocked exposure means UNKNOWN demand, not observed zero demand. '
                                   'At cold start, a small authorized exploratory lot may be justified by the explicitly '
                                   'provided public-sale evidence; a shop does not need its own first sale before buying its first unit. '
                                   'Public sales are still a selected sample and do not guarantee this shop will sell. '
                                   'Select a calculated plan including its validity and total unit budget. '
                                   'During validity, code may replenish ONLY to this target, within this total budget '
                                   'and initial per-order quantity; uncertain supply intents consume the budget. '
                                   'New stockout evidence, catalog/expiry/rate changes invalidate it. '
                                   'The total budget covers the initial order PLUS repeat replenishment during validity; '
                                   'it is distinct from per-order qty. Size it from the evidence and validity, not '
                                   'automatically equal to the first order. Stable evidence can justify longer validity. '
                                   'A longer lease never raises target inventory by itself; repeat units are bought only '
                                   'after observed depletion and every cash/stock guard is checked again. '
                                   'For similarly bounded inventory risk, prefer fewer repeated model calls. '
                                   'If the demand/price scenario is obsolete or the candidates are inadequate, '
                                   'choose action=replan; this emits no purchase and asks for a fresh scenario next hook. '
                                   'A two-unit total cap applies ONLY when sparse_observation_cap_applies is true. '
                                   'Grade and cost are drawn at arrival; estimates are not realized profits.'}
    try:
        approval = decide_json('replenishment plan approver',approval_prompt,approval_schema(table))
        if approval.get('action')=='replan':
            COOLDOWN.write_text(json.dumps({'at':0,'sku':selected['sku']}),encoding='utf-8')
            record('restock_decision',action='hold',reason='model requested fresh scenario',approval=approval)
            return {'buy':[]}
        chosen = next((c for c in table if c['id']==approval.get('candidate_id')),None)
        if approval.get('action')=='hold':
            chosen = table[0]
        if chosen is None or approval.get('action') not in ('approve','hold'):
            raise ValueError('invalid plan approval')
        approval = {**approval,'candidate_id':chosen['id'],'valid_for_seconds':chosen['valid_for_seconds'],
                    'max_total_qty':chosen['max_total_qty']}
        plan = replenishment_plan.create(sku=selected['sku'],product=catalog[selected['sku']],
                                          candidate=chosen,approval=approval,observation=observation,now=time.time())
    except Exception as error:
        record('restock_decision',action='hold',reason='plan approval failed',error=type(error).__name__)
        return {'buy':[]}
    current = load_json(inventory_path,{})
    if not inventory_fresh(inventory_path,time.time()):
        record('restock_decision',action='hold',reason='inventory stale after approval')
        return {'buy':[]}
    if isinstance(current.get('restock'), dict) and not inventory_exploration.window_open(current, time.time()):
        record('restock_hold',reason='supply_window_closed_after_approval')
        return {'buy': []}
    current_catalog = load_json(state/'catalog.json',{})
    current_product = current_catalog.get(selected['sku'])
    if not isinstance(current_product,dict):
        return {'buy':[]}
    plan['unit_grades_at_approval'] = {u['unit']:u.get('grade') for u in live_units
                                      if u.get('sku')==selected['sku'] and u.get('unit')}
    result, reason = replenishment_plan.execute(plan,inventory=current,product=current_product,
                                                observation=observation,now=time.time(),snapshot_time=inventory_path.stat().st_mtime)
    if result is None:
        record('restock_decision',action='hold',reason=reason)
        return {'buy':[]}
    replenishment_plan.save(PLAN,plan)
    qty = result['buy'][0]['qty'] if result['buy'] else 0
    record("restock_decision", action="buy" if qty else "hold", sku=selected["sku"],
           expected_sale_price=price, expected_cost=expected_cost,
           horizon_hours=round(horizon_hours, 2), forecast_mean=mean,
           calculation=calculation, qty=qty, model_choice=decision, fallback=fallback,
           plan_id=plan['id'],approval=approval,chosen_calculation=chosen,observation=observation)
    trace("newsvendor_calculation", sku=selected["sku"], quantity=calculation["quantity"],
          buy_now=qty, critical_ratio=calculation["critical_ratio"],
          forecast_mean=mean, horizon_hours=round(horizon_hours, 2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.state)
    print(json.dumps(result, ensure_ascii=False))   # kit reads the last JSON line


if __name__ == "__main__":
    main()
