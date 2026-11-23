#!/usr/bin/env python3
"""Round 3 category-diversified, cash-protected inventory planning.

Kit v4 command: python restock.py --state <path-to-.market>
Required stdout: {"buy": [{"sku": "...", "qty": 1}, ...]}

This is a cautious cold-start policy. The public demand file describes
orderable categories, NOT observed sales frequency or popular SKUs.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

MIN_CASH = 1_500_000
MAX_STOCK_UNITS = 20
MAX_UNITS_PER_CATEGORY = 2
MAX_UNITS_PER_TOP_CATEGORY = 2
MAX_UNITS_PER_SKU = 1
MAX_LINES_PER_RUN = 8
MAX_ESTIMATED_SPEND = 300_000
MIN_FOOD_SHELF_HOURS = 48
MAX_LIST_PRICE = 200_000


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return default


def num(x, default=0.0):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return default


def root(category):
    return category.split(">", 1)[0].strip()


def stable_tiebreak(sku):
    # Avoid always selecting the alphabetically earliest G00... SKU.
    return int.from_bytes(hashlib.blake2s(sku.encode("utf-8"), digest_size=4).digest(), "big")


def make_plan(state):
    inventory = load_json(state / "inventory.json", {})
    catalog = load_json(state / "catalog.json", {})
    demand = load_json(state / "demand.json", [])
    if not (isinstance(inventory, dict) and isinstance(catalog, dict) and isinstance(demand, list)):
        return {"buy": []}
    if int(num(inventory.get("round"))) != 3:
        return {"buy": []}

    balance = num(inventory.get("balance"))
    stock = inventory.get("units", [])
    if balance <= MIN_CASH or not isinstance(stock, list) or len(stock) >= MAX_STOCK_UNITS:
        return {"buy": []}

    # `skus` in the public demand groups is an INTEGER COUNT, not a list.
    # It does not disclose popular SKU IDs or sales frequency.
    allowed_categories = {
        str(g["category"]).strip()
        for g in demand
        if isinstance(g, dict) and isinstance(g.get("category"), str) and g["category"].strip()
    }
    if not allowed_categories:
        return {"buy": []}

    per_sku = Counter()
    per_cat = Counter()
    per_root = Counter()
    for unit in stock:
        if not isinstance(unit, dict):
            continue
        sku = str(unit.get("sku") or "")
        meta = catalog.get(sku) or {}
        cat = str(unit.get("category") or (meta.get("category") if isinstance(meta, dict) else "") or "")
        per_sku[sku] += 1
        per_cat[cat] += 1
        per_root[root(cat)] += 1

    max_budget = min(MAX_ESTIMATED_SPEND, balance * 0.08, balance - MIN_CASH)
    remaining = max_budget
    slots = min(MAX_LINES_PER_RUN, MAX_STOCK_UNITS - len(stock))
    candidates = []
    for sku, meta in catalog.items():
        if not isinstance(sku, str) or not isinstance(meta, dict):
            continue
        cat = str(meta.get("category") or "").strip()
        if cat not in allowed_categories or per_sku[sku] >= MAX_UNITS_PER_SKU:
            continue
        price = num(meta.get("list_price"))
        if not 0 < price <= MAX_LIST_PRICE:
            continue
        kind = str(meta.get("kind") or "").lower()
        food = kind in {"food", "fresh", "grocery", "perishable", "식품"}
        shelf = num(meta.get("shelf_hours"))
        if food and shelf < MIN_FOOD_SHELF_HOURS:
            continue
        # Max source-cost fraction for grade A = 75% of list price.
        estimated_max_cost = 0.75 * price
        if estimated_max_cost > max_budget:
            continue
        # For an initial unknown-demand period, avoid both fragile food and
        # very costly items; this is not a demand/profit forecast.
        target = 35_000.0
        price_penalty = abs(price - target) / target
        base = -min(price_penalty, 5.0) - (3.0 if food else 0.0)
        candidates.append((sku, cat, root(cat), estimated_max_cost, base, stable_tiebreak(sku)))

    chosen = set()
    buys = []
    # Choose items one at a time, re-scoring the category concentrations.
    # This avoids fixed SKU ordering and concentrates less stock in bedding.
    for _ in range(slots):
        available = [c for c in candidates if c[0] not in chosen
                     and c[3] <= remaining
                     and per_cat[c[1]] < MAX_UNITS_PER_CATEGORY
                     and per_root[c[2]] < MAX_UNITS_PER_TOP_CATEGORY]
        if not available:
            break
        sku, category, top, cost, _, _ = max(
            available,
            key=lambda c: (
                -per_root[c[2]],
                -per_cat[c[1]],
                c[4],
                c[5],
            )
        )
        chosen.add(sku)
        per_sku[sku] += 1
        per_cat[category] += 1
        per_root[top] += 1
        remaining -= cost
        buys.append({"sku": sku, "qty": 1})

    print(f"[restock] candidates={len(candidates)} proposed={len(buys)} "
          f"max_estimated_cost={round(max_budget - remaining)} "
          f"top_categories={dict(per_root)}", file=sys.stderr)
    return {"buy": buys}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = make_plan(args.state)
    except Exception as error:
        print(f"[restock] error: {type(error).__name__}: {error}", file=sys.stderr)
        result = {"buy": []}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
