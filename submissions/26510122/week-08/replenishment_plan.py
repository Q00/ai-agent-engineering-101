"""Bounded model-approved replenishment plans; execution never invents a target.

Quantity/spend budgets are reserved BEFORE returning a buy intent. If supply is
refused, that reservation is not silently restored. A new model approval is
needed once the plan expires or its authorized budget is exhausted.
"""
from __future__ import annotations

import hashlib
import json
import math
from procurement_cost import ceiling as acquisition_ceiling
from pathlib import Path
import uuid
import time
from market_memory import RECEIPT_EVENTS, events
import seller_warranty
from market_scoring import book_metrics
from supply_reconciliation import RECONCILIATION_EVENTS, reconcile, pending_quantity
from stock_policy import PROVEN_STOCK, NEW_STOCK, MAINTENANCE_CATEGORY_LIMIT
from capital_policy import MIN_CASH, INVALID_STATE_EXPOSURE, operating_policy

MAX_VALID_SECONDS = 600
MIN_BALANCE = MIN_CASH
MAX_SKU_STOCK = PROVEN_STOCK
MAX_CATEGORY_STOCK = MAINTENANCE_CATEGORY_LIMIT
MAX_INVENTORY_COST = INVALID_STATE_EXPOSURE  # fail-closed invalid-state reservation only
POLICY_VERSION = 10


def pending_exploration_cost(inventory: dict) -> int:
    """Share unresolved exploration reservations with ordinary/cached plans."""
    path = Path('state/inventory-portfolio.json')
    if not path.exists():
        return 0
    state = load(path)
    if not state or not isinstance(state.get('intents'), list):
        return MAX_INVENTORY_COST
    if any(not isinstance(i, dict) or not isinstance(i.get('sku'), str)
           or type(i.get('at')) not in (int, float) or not math.isfinite(i['at'])
           or type(i.get('qty', 1)) is not int or not 1 <= i.get('qty', 1) <= PROVEN_STOCK
           or not isinstance(i.get('prior_units'), list)
           or i.get('status') not in ('issued_unknown', 'observed_inventory', 'settled')
           for i in state['intents']):
        return MAX_INVENTORY_COST
    reconcile(state['intents'], inventory, events(kinds=RECONCILIATION_EVENTS), time.time())
    total = 0
    catalog=load(Path('.market/catalog.json')) or {}
    for intent in state['intents']:
        if not isinstance(intent, dict) or intent.get('status') not in ('issued_unknown', 'observed_inventory', 'settled'):
            return MAX_INVENTORY_COST
        if intent['status'] != 'issued_unknown':
            continue
        if type(intent.get('cost_ceiling')) is not int or intent['cost_ceiling'] <= 0 or not isinstance(intent.get('prior_units'), list):
            return MAX_INVENTORY_COST
        product=catalog.get(intent['sku'])
        ceiling=max(intent['cost_ceiling'],acquisition_ceiling(product) if product else math.ceil(intent['cost_ceiling']*1.5))
        if intent.get('surge_reserved'):ceiling=intent['cost_ceiling']
        total += ceiling * pending_quantity(intent)
    return total


def inventory_risk(inventory: dict, now: float, pending_cost: int | None = None) -> dict:
    """Acquisition-cost exposure, not an assertion of resale or liquidation value."""
    pending_cost = pending_exploration_cost(inventory) if pending_cost is None else pending_cost
    balance = inventory.get('balance')
    balance = max(0, int(balance)) if type(balance) in (int, float) and math.isfinite(balance) else 0
    stock_cost = sum(max(0, int(u.get('cost') or 0)) for u in inventory.get('units', [])
                     if u.get('grade') in ('A', 'B', 'C')
                     and (u.get('expires_at') is None or u['expires_at'] > now))
    policy = operating_policy(now)
    goods_cost = sum(max(0,int(u.get('cost') or 0)) for u in inventory.get('units',[])
                     if u.get('kind')=='goods' and u.get('grade') in ('A','B','C'))
    storage_reserve = math.ceil(goods_cost*.01*min(14.,policy['hours_to_end']/24))
    warranty = seller_warranty.exposure(events(kinds=RECEIPT_EVENTS), now)
    reserve = max(MIN_BALANCE,int(inventory.get('bankrupt_line') or 500_000)+100_000)+storage_reserve+warranty['cash_reserve']
    limit = int((balance + stock_cost) * policy['inventory_capital_share'])
    cash_room = max(0, balance - reserve)
    exposure_room = max(0, limit - stock_cost - max(0, pending_cost))
    return {'cash_balance': balance, 'cash_reserve': reserve, 'storage_cash_buffer':storage_reserve,
            'warranty_exposure':warranty,
            'book':book_metrics(inventory,now),
            'stock_acquisition_cost': stock_cost,
            'pending_cost_ceiling': pending_cost, 'inventory_cost_limit': limit,
            'capital_share_limit': policy['inventory_capital_share'], 'operating_policy':policy,
            'available_to_invest': min(cash_room, exposure_room),
            'caution': 'Acquisition cost is risk exposure, not guaranteed resale value.'}


def product_signature(product: dict) -> str:
    fields = {k: product.get(k) for k in ('kind', 'list_price', 'shelf_hours')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def save(path: Path, plan: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def create(*, sku: str, product: dict, candidate: dict, approval: dict,
           observation: dict, now: float) -> dict:
    ttl, budget = approval.get('valid_for_seconds'), approval.get('max_total_qty')
    if type(ttl) is not int or not 60 <= ttl <= MAX_VALID_SECONDS:
        raise ValueError('approval must give a validity between 60 and 600 seconds')
    if type(budget) is not int or not candidate['qty'] <= budget <= 20:
        raise ValueError('approval must cover initial quantity with total budget <=20')
    stock_limit = min(PROVEN_STOCK,observation.get('stock_limit',PROVEN_STOCK)) if observation.get('proven') else NEW_STOCK
    if not candidate['qty'] <= candidate['target_stock'] <= stock_limit:
        raise ValueError('target must fit the per-SKU portfolio limit')
    if observation['observed_visits'] < 5 and not observation.get('proven'):
        budget = min(budget, 2)
    return {'version': 1, 'policy_version': POLICY_VERSION, 'id': uuid.uuid4().hex, 'sku': sku, 'approved_at': now,
            'expires_at': now + ttl, 'target_stock': candidate['target_stock'],
            'stock_limit_at_approval': stock_limit,
            'max_initial_qty': candidate['qty'], 'max_total_qty': budget, 'emitted_qty': 0,
            'product_signature': product_signature(product),
            'cost_ceiling_per_unit': acquisition_ceiling(product),
            'observed_no_stock_at_approval': observation['observed_no_stock_visits'],
            'observed_visit_rate_at_approval': observation['observed_visit_rate_while_stocked'],
            'approval': approval, 'candidate': candidate}


def execute(plan: dict, *, inventory: dict, product: dict, observation: dict,
            now: float, snapshot_time: float | None = None) -> tuple[dict | None, str]:
    """Return a bounded intent, or None when another model approval is required."""
    integer_fields = ('target_stock','max_initial_qty','max_total_qty','emitted_qty','cost_ceiling_per_unit')
    if any(type(plan.get(k)) not in (int,float) or not math.isfinite(plan[k]) for k in ('approved_at','expires_at')):
        return None, 'invalid_plan_time'
    if any(not isinstance(plan.get(k),str) or not plan[k] for k in ('id','sku','product_signature')):
        return None, 'invalid_plan_identity'
    if any(type(plan.get(k)) is not int or plan[k] < 0 for k in integer_fields):
        return None, 'invalid_plan_fields'
    if not (plan['target_stock'] <= MAX_SKU_STOCK and plan['max_initial_qty'] <= plan['max_total_qty'] <= 20
            and plan['emitted_qty'] <= plan['max_total_qty'] and plan['cost_ceiling_per_unit'] > 0):
        return None, 'invalid_plan_budget'
    if plan.get('policy_version') != POLICY_VERSION:
        return None, 'portfolio_policy_changed'
    operating = operating_policy(now)
    if not operating['allow_replenishment']: return {'buy':[]}, 'market_closing'
    if not operating['allow_new_skus'] and not observation.get('proven'):
        return None, 'unproven_restock_closed'
    current_limit = min(PROVEN_STOCK,observation.get('stock_limit',PROVEN_STOCK)) if observation.get('proven') else NEW_STOCK
    if plan['target_stock'] > current_limit:
        return None, 'sales_evidence_changed'
    if plan.get('version') != 1 or not plan.get('approved_at', now+1) <= now < plan.get('expires_at', 0):
        return None, 'expired_or_invalid_plan'
    if not 60 <= plan['expires_at']-plan['approved_at'] <= MAX_VALID_SECONDS:
        return None, 'invalid_plan_validity'
    if plan.get('product_signature') != product_signature(product):
        return None, 'catalog_changed'
    stockout_times = observation.get('recent_stockout_times')
    new_stockouts = (sum(t >= plan['approved_at'] for t in stockout_times) if stockout_times is not None
                     else observation['observed_no_stock_visits']-plan['observed_no_stock_at_approval'])
    if new_stockouts >= 3:
        return None, 'new_stockout_evidence'
    baseline = plan.get('observed_visit_rate_at_approval')
    current = observation['observed_visit_rate_while_stocked']
    if baseline is not None and current is not None and abs(current-baseline) > max(1., baseline*.5):
        return None, 'observed_visit_rate_changed'
    if inventory.get('round') != 3:
        return None, 'wrong_round'
    if snapshot_time is not None and snapshot_time <= plan.get('last_intent_snapshot', -1):
        return {'buy':[]}, 'awaiting_inventory_refresh'
    live = [u for u in inventory.get('units', []) if u.get('grade') in ('A','B','C')
            and (u.get('expires_at') is None or u['expires_at'] > now)]
    own = [u for u in live if u.get('sku') == plan['sku']]
    known_grades = plan.get('unit_grades_at_approval',{})
    if any(u.get('unit') in known_grades and known_grades[u['unit']] != u.get('grade') for u in own):
        return None, 'grade_changed'
    if any(u.get('expires_at') is not None and u['expires_at']-now < 3600 for u in own):
        return None, 'expiry_requires_reassessment'
    plan['unit_grades_at_approval'] = {u['unit']:u.get('grade') for u in own if u.get('unit')}
    missing = max(0, plan['target_stock']-len(own))
    category = product.get('category')
    category_room = max(0, MAX_CATEGORY_STOCK - sum(u.get('category') == category for u in live)) if category else 20
    budget = max(0, plan['max_total_qty']-plan['emitted_qty'])
    if missing and not budget:
        return None, 'authorized_budget_exhausted'
    available = inventory_risk(inventory, now)['available_to_invest']
    qty = min(missing, budget, plan['max_initial_qty'], category_room, available//plan['cost_ceiling_per_unit'])
    qty = max(0, qty)
    plan['emitted_qty'] += qty
    if qty and snapshot_time is not None:
        plan['last_intent_snapshot'] = snapshot_time
    return ({'buy': [{'sku': plan['sku'], 'qty': qty}]} if qty else {'buy': []}), 'approved_plan'
