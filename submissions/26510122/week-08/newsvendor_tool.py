"""Small, inspectable calculations for the Round 3 shop.

This module supplies price *candidates* and a replenishment quantity. The
seller harness chooses an action; the tool does not silently set its price.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
from statistics import median_low
import sys
import time
from typing import Any, Iterable
from context_compaction import epoch
import seller_warranty
from market_scoring import terminal_credit
from capital_policy import ROUND_END


# Starting hypotheses, not values supplied by the lecture or the market.
GRADE_ANCHORS = {"A": 1.0, "B": 0.80, "C": 0.60}


def quote_candidates(unit: dict[str, Any], now: float | None = None, *, warranty_hours: int = 0) -> dict[str, Any]:
    """Return three grade-aware anchors and the unit economics behind them."""
    now = time.time() if now is None else now
    grade = unit.get("grade")
    if grade not in GRADE_ANCHORS:
        raise ValueError(f"unsupported grade: {grade!r}")
    cost, list_price = int(unit["cost"]), int(unit["list_price"])
    if cost <= 0 or list_price <= 0:
        raise ValueError("cost and list_price must be positive")
    anchor = list_price * GRADE_ANCHORS[grade]
    prices = sorted({max(1, round(anchor * factor)) for factor in (0.90, 1.0, 1.10)})
    expires = unit.get("expires_at")
    hours_left = max(0.0, (float(expires) - now) / 3600) if expires else None
    storage_day = round(cost * 0.01, 2) if unit.get("kind") == "goods" else 0.0
    warranty = seller_warranty.economics(unit, now=now, warranty_hours=warranty_hours)
    credit = terminal_credit(unit,ROUND_END)
    future_holding = storage_day*max(0.,ROUND_END-now)/86400
    prices = sorted({max(price,warranty['price_floor']) for price in prices})
    return {
        "sku": unit["sku"], "unit": unit["unit"], "grade": grade,
        "kind": unit.get("kind"), "cost": cost, "list_price": list_price,
        "hours_left": round(hours_left, 2) if hours_left is not None else None,
        "storage_per_day": storage_day,
        "warranty": warranty,
        "book": {'current_stock_credit':cost,'credit_if_unsold_at_round_end':credit,
                 'estimated_future_holding_if_unsold':round(future_holding,2),
                 'book_change_if_held_to_end':round(credit-cost-future_holding,2),
                 'contract':'Compare sale spread after warranty risk with hold-to-end Book change; inventory credit is not cash.'},
        "anchor_source": "unvalidated grade priors A=1.0, B=0.8, C=0.6 of list price",
        "candidates": [
            {"price": price, "margin_over_purchase_cost": price - cost,
             "below_purchase_cost": price < cost,
             "warranty_risk": seller_warranty.economics(unit,price,now=now,warranty_hours=warranty_hours)}
            for price in prices
        ],
    }


def expiry_band(hours):
    if hours is None or hours <= 0:
        return None
    return next((limit for limit in (3, 24, 72) if hours <= limit), 'over_72h')


def price_guidance(unit: dict, *, now: float | None = None, warranty_hours: int = 0,
                   attempts: Iterable[dict] = (), offered_price: int | None = None,
                   target_profit: int = 0) -> dict:
    """Separate the physical unit's economics from observed, comparable paid prices."""
    now = time.time() if now is None else now
    if type(target_profit) is not int or target_profit < 0:
        raise ValueError('target_profit must be a nonnegative integer')
    if offered_price is not None and (type(offered_price) is not int or offered_price < 0):
        raise ValueError('offered_price must be a nonnegative integer')
    cost = unit['cost']
    if type(cost) is not int or cost <= 0:
        raise ValueError('unit cost must be a positive integer')
    charges = seller_warranty.economics(unit, now=now, warranty_hours=warranty_hours)
    storage = Decimal(str(charges['estimated_accrued_holding']))
    refund = Decimal(str(charges['refund_share_scenario']))
    net_fraction = 1 - refund
    total_cost = cost + storage
    break_even = math.ceil(total_cost / net_fraction)
    positive_price = math.floor(total_cost / net_fraction) + 1
    goal_price = max(positive_price, math.ceil((total_cost + target_profit) / net_fraction))
    def profit(price):
        return float(round(price * net_fraction - total_cost, 2))
    food = unit.get('kind') == 'food'
    expires = unit.get('expires_at')
    remaining = (expires - now) / 3600 if type(expires) in (int, float) else None
    bucket = expiry_band(remaining) if food else None
    comparable = {}
    openings = {}
    for a in attempts:
        if a.get('outcome') != 'SETTLED' or a.get('sku') != unit['sku'] or a.get('ledger') is None:
            continue
        paid = a.get('settled_price')
        grade = a.get('settled_grade') or a.get('grade_at_receipt') or a.get('grade')
        when = a.get('settled_at')
        when = when if type(when) in (int, float) else epoch(when)
        if (grade != unit['grade'] or type(paid) is not int or paid <= 0
                or when is None or not 0 <= now - when <= 72 * 3600):
            continue
        if food:
            if bucket is None or expiry_band(a.get('hours_left_at_receipt')) != bucket:
                continue
        elif a.get('warranty_hours_at_receipt') != charges['warranty_hours']:
            continue
        comparable[a['ledger']] = paid
        opening = a.get('opening_ask')
        if type(opening) is int and opening >= paid and a.get('grade') == unit['grade']:
            openings[a['ledger']] = opening
    prices = list(comparable.values())
    observed = median_low(prices) if prices else None
    if food and bucket is None:
        status = 'expiry_unavailable_or_passed'
    elif len(prices) < 3:
        status = 'insufficient_comparable_sales'
    elif observed < goal_price:
        status = 'typical_paid_price_below_target'
    else:
        status = 'supported'
    opening_median = median_low(list(openings.values())) if len(openings) >= 3 else None
    start = max(observed, opening_median) if status == 'supported' and opening_median is not None else None
    return {
        'break_even_price': break_even,
        'minimum_profitable_price': positive_price,
        'target_profit': target_profit,
        'minimum_price_for_target': goal_price,
        'accrued_storage': float(storage),
        'refund_share_scenario': float(refund),
        'comparable_paid_72h': {'count': len(prices), 'median': observed,
                                'min': min(prices, default=None), 'max': max(prices, default=None)},
        'comparable_openings_72h': {'count': len(openings), 'median': opening_median},
        'status': status,
        'suggested_opening_price': start,
        'opening_reference_note': 'Paid prices describe completed negotiations. Opening guidance uses only '
                                  'opening quotes that later settled under comparable conditions. '
                                  'It is a reference, not a floor or evidence that higher asks will convert.',
        'estimated_profit_at_suggestion': profit(start) if start is not None else None,
        'offered_price': offered_price,
        'estimated_profit_at_offer': profit(offered_price) if offered_price is not None else None,
        'offer_headroom_to_profit_floor': offered_price - positive_price if offered_price is not None else None,
        'expiry_hours_band': bucket,
    }


def inventory_prices(runtime, sku, grade, offered_price, target_profit):
    from market_memory import trade_attempts

    path = runtime / '.market/inventory.json'
    now = time.time()
    inventory = json.loads(path.read_text())
    if inventory.get('round') != 3 or not 0 <= now - path.stat().st_mtime < 30:
        raise ValueError('live Round 3 inventory is not fresh')
    db_uri = (runtime/'state/seller.sqlite3').as_uri() + '?mode=ro'
    with closing(sqlite3.connect(db_uri, uri=True)) as db:
        unavailable = {row[0] for row in db.execute(
            'SELECT unit FROM settled_units UNION SELECT unit FROM leases WHERE until>?', (now,))}
    attempts = trade_attempts({sku}, path=runtime/'logs/shop-events.jsonl')
    return {'sku': sku, 'units': [
        {'unit': u['unit'], 'grade': u['grade'], 'cost': u['cost'],
         **price_guidance(u, now=now, warranty_hours=seller_warranty.hours(u), attempts=attempts,
                          offered_price=offered_price, target_profit=target_profit)}
        for u in inventory['units'] if u['sku'] == sku and u['unit'] not in unavailable
        and u['grade'] in GRADE_ANCHORS and (grade is None or u['grade'] == grade)
        and (u.get('expires_at') is None or u['expires_at'] > now)
    ]}


def poisson_pmf(mean: float, cap: int = 20) -> list[float]:
    """Demand 0..cap, with the final bucket including the upper tail."""
    if not math.isfinite(mean) or mean < 0 or cap < 1:
        raise ValueError("mean must be finite and nonnegative; cap must be positive")
    probs = [math.exp(-mean)]
    for n in range(1, cap):
        probs.append(probs[-1] * mean / n)
    probs.append(max(0.0, 1.0 - sum(probs)))
    return probs


def recommend_quantity(
    demand_pmf: list[float], price: int, cost: float, *,
    salvage: float = 0.0, holding_per_leftover: float = 0.0, max_qty: int = 20,
) -> dict[str, Any]:
    """Apply the discrete newsvendor rule, then report expected profit.

    The caller owns the demand forecast. A sale price does not come from this
    formula; demand_pmf must be estimated for that particular price and horizon.
    """
    if not demand_pmf or max_qty < 0 or type(price) is not int or price <= 0:
        raise ValueError("provide a demand PMF, positive integer price and max_qty >= 0")
    if any(not math.isfinite(x) or x < 0 for x in demand_pmf):
        raise ValueError("demand probabilities must be finite and nonnegative")
    if not math.isclose(sum(demand_pmf), 1.0, rel_tol=0, abs_tol=1e-6):
        raise ValueError("demand probabilities must sum to one")
    if not (0 <= salvage <= cost and holding_per_leftover >= 0):
        raise ValueError("require 0 <= salvage <= cost and nonnegative holding")
    underage = max(0.0, price - cost)
    overage = cost - salvage + holding_per_leftover
    ratio = underage / (underage + overage) if underage + overage else 0.0
    limit = min(max_qty, len(demand_pmf) - 1)
    cumulative = 0.0
    quantity = limit
    for q in range(limit + 1):
        cumulative += demand_pmf[q]
        if cumulative + 1e-12 >= ratio:
            quantity = q
            break

    def expected_profit(q: int) -> float:
        return sum(prob * (price * min(d, q) + salvage * max(q - d, 0)
                           - cost * q - holding_per_leftover * max(q - d, 0))
                   for d, prob in enumerate(demand_pmf))

    return {
        "quantity": quantity, "critical_ratio": round(ratio, 6),
        "underage_cost": round(underage, 2), "overage_cost": round(overage, 2),
        "expected_profit": round(expected_profit(quantity), 2),
        "horizon": "the horizon used to build demand_pmf",
        "price": price, "cost": cost, "salvage": salvage,
        "holding_per_leftover": holding_per_leftover,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Round 3 pricing and replenishment")
    parser.add_argument("action", choices=("quote", "replenish", "price"))
    parser.add_argument("--runtime", type=Path, default=Path.cwd())
    parser.add_argument("--sku")
    parser.add_argument("--grade", choices=tuple(GRADE_ANCHORS))
    parser.add_argument("--offer", type=int)
    parser.add_argument("--target-profit", type=int, default=0)
    args = parser.parse_args()
    if args.action == "price":
        if not args.sku:
            parser.error("price requires --sku")
        result = inventory_prices(args.runtime.resolve(), args.sku, args.grade, args.offer, args.target_profit)
    else:
        payload = json.load(sys.stdin)
        result = quote_candidates(payload["unit"]) if args.action == "quote" else recommend_quantity(**payload)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
