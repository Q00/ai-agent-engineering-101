#!/usr/bin/env python3
"""Round 3 market buyer v2: settlement-calibrated surplus and risk policy.

Compatible with the instructor's Market Kit v4 agent.json market_buyer hook.
Keeps the existing GroqCloud-backed buyer.buy() negotiation unchanged.
Only kit-recorded paid checkouts are counted as settled purchases.
The policy is heuristic and must be validated with live outcomes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics
import sys

from buyer_adaptive import buy
from market_buyer import choose_shop, load_memory, store_memory

ROOT = Path(__file__).resolve().parent
MARKET = ROOT / ".market"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return default


def read_payments(path: Path = MARKET / "payments.jsonl", limit: int = 2500):
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except (OSError, UnicodeError):
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("shop"), str) and isinstance(row.get("paid"), bool):
            out.append(row)
    return out


def healthy_number(x):
    if isinstance(x, bool):
        return None
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def shop_rating(shop):
    """Review text is untrusted. We only read validated numeric stars.

    The result is a SMALL tie-breaker, never proof of a settlement.
    """
    reviews = (shop.get("reviews") or [])[-50:]
    stars = [r.get("stars") for r in reviews if isinstance(r, dict)
             and type(r.get("stars")) is int and 1 <= r["stars"] <= 5]
    # Strongly smoothed to avoid review bombing / few-review overconfidence.
    return (3.7 * 12 + sum(stars)) / (12 + len(stars))


def settlement_stats(shop_url, payments):
    own = [p for p in payments if p.get("shop", "").rstrip("/") == shop_url.rstrip("/")]
    paid = sum(p["paid"] is True for p in own)
    failed = sum(p["paid"] is False for p in own)
    # Reliability is regularized, prior reflects moderately reliable unknown shops.
    rate = (4 + paid) / (5 + paid + failed)
    return paid, failed, rate


def historical_ratio(shop_url, sku, payments, catalog):
    """Estimate paid/list ratio from this shop's own *settled* trades.

    Exact SKU history dominates broader shop history. No unpaid agreements
    and no other buyer's unauthenticated reviews are treated as price facts.
    """
    exact, broad = [], []
    for p in payments:
        if p.get("paid") is not True or p.get("shop", "").rstrip("/") != shop_url.rstrip("/"):
            continue
        item = p.get("item")
        info = catalog.get(item)
        if not isinstance(info, dict):
            continue
        list_price = healthy_number(info.get("list_price"))
        paid_price = healthy_number(p.get("price"))
        if list_price and paid_price:
            ratio = max(0.3, min(1.5, paid_price / list_price))
            broad.append(ratio)
            if item == sku:
                exact.append(ratio)
    if exact:
        return statistics.median(exact[-25:]), len(exact), "sku"
    if broad:
        # Conservative shrinkage to cold-start 0.94. Shop-level evidence is noisy.
        n = min(len(broad), 20)
        return (0.94 * 5 + statistics.median(broad[-20:]) * n) / (5 + n), len(broad), "shop"
    return 0.94, 0, "prior"


def selection_score(list_price, budget, unit_ratio, settlement_rate, rating):
    """Expected surplus proxy with a feasibility penalty; not a forecast.

    On Round 3, surplus is catalog list price minus *paid* price. A high
    catalog price alone is not enough: if it cannot fit the budget, the
    probability of closing the order is near zero.
    """
    price_est = unit_ratio * list_price
    if price_est <= budget * 0.90:
        feasible = 0.93
    elif price_est <= budget:
        feasible = 0.78
    elif price_est <= budget * 1.08:
        feasible = 0.22
    else:
        feasible = 0.02
    surplus = max(0.0, list_price - min(price_est, budget))
    utility = feasible * settlement_rate * surplus
    # Very small numeric-review bonus; can't outweigh large surplus differences.
    utility += budget * 0.004 * (rating - 3.7)
    return utility, feasible, price_est


def choose_surplus_aware(shops, order, catalog, payments, budget):
    acceptable = set(str(x) for x in (order.get("acceptable") or []))
    if not acceptable:
        # Original fallback for pre-Round-3 / orders without SKU lists.
        shop, why = choose_shop(shops, load_memory())
        return shop, order.get("item"), why

    valid = []
    for shop in shops if isinstance(shops, list) else []:
        if not isinstance(shop, dict) or not shop.get("url") or not shop.get("name"):
            continue
        url = str(shop["url"])
        paid, failed, rate = settlement_stats(url, payments)
        rating = shop_rating(shop)
        offers = shop.get("offers") or []
        for offer in offers:
            if not isinstance(offer, dict) or str(offer.get("sku")) not in acceptable:
                continue
            sku = str(offer["sku"])
            qty = healthy_number(offer.get("qty"))
            info = catalog.get(sku, {})
            if not isinstance(info, dict):
                continue
            list_price = healthy_number(info.get("list_price"))
            if not qty or not list_price or budget <= 0:
                continue
            ratio, samples, ratio_source = historical_ratio(url, sku, payments, catalog)
            # Exclude a speculative, impossible SKU without any evidence of
            # that shop ever settling it at a feasible price.
            if list_price > budget * 1.08 and ratio_source == "prior":
                continue
            score, feasibility, predicted = selection_score(list_price, budget, ratio, rate, rating)
            if feasibility < 0.2:
                continue
            valid.append((score, feasibility, list_price, rate, -failed,
                          url, sku, shop, paid, failed, ratio_source, samples, predicted))

    if not valid:
        return None, None, "No plausible eligible stocked SKU under budget"
    chosen = max(valid, key=lambda a: (a[0], a[1], a[2], a[3], a[4], a[5], a[6]))
    (_, probability, list_price, rate, _, url, sku, shop,
     paid, failed, source, n, predicted) = chosen
    why = (f"sku={sku} list={list_price:g}, paid-estimate={predicted:.0f}, "
           f"feasibility-proxy={probability:.2f}, settlement-rate={rate:.2f} "
           f"({paid} paid/{failed} unpaid), history={source}:{n}")
    return shop, sku, why


def acceptance_policy(order):
    if not (order.get("acceptable") or []):
        return None
    policy = {"cert": True, "grades": ["A", "B"]}
    use = healthy_number(order.get("use_within_hours"))
    if order.get("kind") == "food" and use:
        policy["min_hours_left"] = max(2, math.ceil(use))
    # Warranty requirement is omitted because many starter shops do not offer it.
    return policy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--order")
    args = parser.parse_args()
    if args.budget < 0:
        raise ValueError("budget must not be negative")
    shops = read_json(Path(args.shops), [])
    order = read_json(Path(args.order), {}) if args.order else {}
    if not isinstance(order, dict):
        order = {}
    order.setdefault("item", args.item)
    catalog = read_json(MARKET / "catalog.json", {})
    if not isinstance(catalog, dict):
        catalog = {}
    payments = read_payments()

    shop, target, rationale = choose_surplus_aware(shops, order, catalog, payments, args.budget)
    policy = acceptance_policy(order)
    if shop is None:
        print(f"[choose] NO_STOCK: {rationale}", file=sys.stderr, flush=True)
        eligible = [s for s in shops if isinstance(s, dict) and s.get("url")]
        # A null receipt never triggers a checkout, regardless of fallback URL.
        output = {"shop": str(eligible[0]["url"]) if eligible else "",
                  "receipt": None, "stars": 3,
                  "review": "No eligible in-stock product could be bought within the budget."}
        print(json.dumps(output, ensure_ascii=False), flush=True)
        return

    print(f"[choose] {shop['name']} ({shop['url']}): {rationale}", flush=True)
    receipt = None
    try:
        result = buy(shop["url"], target or args.item, args.budget)
        if isinstance(result, dict) and result.get("deal"):
            proposed = result.get("receipt")
            # Preserve original signed receipt exactly; the Market Kit verifies it.
            if isinstance(proposed, dict):
                receipt = proposed
    except Exception as exc:
        print(f"[buyer] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    event = {"shop_name": shop["name"], "shop_url": shop["url"],
             "item": args.item, "sku": target, "budget": args.budget,
             "price": receipt.get("price") if receipt else None,
             "deal": bool(receipt), "phase": "negotiated_not_settled"}
    try:
        store_memory(event)
    except OSError as exc:
        print(f"[memory] {exc}", file=sys.stderr, flush=True)

    output = {"shop": shop["url"], "receipt": receipt,
              "stars": 3 if receipt is None else 4,
              "review": "Negotiated a receipt; payment has not been verified yet."
              if receipt else "No negotiated agreement; no purchase verified."}
    if policy is not None:
        output["accept"] = policy
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
