"""Conservative Round-3 restock v4: v3-first, small cold-start fallback.

Companion to the user's existing restock_v3.py, imported without modification.
Reads .market snapshots only; the market kit (not this script) makes any buys.

CLI:
  python restock_v4.py --state .market --preview --diagnose
  python restock_v4.py --state .market
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import restock_v3 as previous

# Keep worst-case exposure low when there is NO historic sales or cost evidence.
MAX_COLD_START_UNITS = 3          # Current stock + new units combined
MAX_ESTIMATED_UNIT_COST = 15_000 # 150% of list-price estimate, not guaranteed real cost
MAX_COLD_START_UNSOLD_COST = 55_000
MIN_CASH_FLOOR = 4_700_000
MIN_LIST_PRICE = 4_000
MAX_LIST_PRICE = 10_000
COST_BUFFER_FACTOR = 1.50

# Exclude common perishable categories from the bootstrap fallback.
# These are conservative heuristics, not a guarantee that every other item is nonperishable.
FOOD_KEYWORDS = (
    "김치", "식품", "즉석", "주스", "음료", "생수", "코코아", "과일", "채소", "농산",
    "수산", "축산", "육류", "고기", "계란", "유제품", "과자", "빵", "면류", "라면",
    "쌀", "곡물", "반찬", "커피", "차류", "건강식", "냉장", "냉동", "식재", "조미",
)


def _finite_money(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) and num > 0 else None


def evaluate(state: Path, *, ignore_window: bool = False) -> tuple[list[dict], dict]:
    """Return buy recommendations, never make API calls or write files."""
    existing, info = previous.evaluate(state, ignore_window=ignore_window)
    if existing:
        return existing, dict(info, strategy="historical_v3")
    if info.get("reason") != "no profitable, sufficiently evidenced SKU under risk caps":
        return [], dict(info, strategy="original_v3_guards")

    inv = previous.read_json(state / "inventory.json", {})
    catalog = previous.read_json(state / "catalog.json", {})
    demand = previous.read_json(state / "demand.json", [])
    if not isinstance(inv, dict) or not isinstance(catalog, dict):
        return [], dict(info, strategy="bootstrap", detail="missing/invalid snapshot")
    if not ignore_window and (inv.get("restock") or {}).get("open") is not True:
        return [], dict(info, strategy="bootstrap", detail="supply window closed")

    units = [u for u in inv.get("units", []) if isinstance(u, dict)]
    # Count all units, including expired ones, in bootstrap exposure cap.
    if len(units) >= MAX_COLD_START_UNITS:
        return [], dict(info, strategy="bootstrap", detail="bootstrap unit cap reached")
    unsold_cost = sum(_finite_money(u.get("cost")) or 0.0 for u in units)
    if unsold_cost >= MAX_COLD_START_UNSOLD_COST:
        return [], dict(info, strategy="bootstrap", detail="bootstrap unsold-cost cap reached")
    cash = float(inv.get("balance") or 0)
    floor = max(MIN_CASH_FLOOR, float(getattr(previous, "MIN_BALANCE", MIN_CASH_FLOOR)))
    if cash - floor < MAX_ESTIMATED_UNIT_COST:
        return [], dict(info, strategy="bootstrap", detail="cash guard: need buffered free cash")
    demand_categories = {str(p.get("category")) for p in demand if isinstance(p, dict)} if isinstance(demand, list) else set()
    stocked = Counter(str(u.get("sku")) for u in units)
    stocked_categories = Counter(
        str(catalog.get(sku, {}).get("category", ""))
        for sku in stocked for _ in range(stocked[sku])
        if isinstance(catalog.get(sku), dict)
    )

    choices = []
    for sku, item in catalog.items():
        if not isinstance(item, dict) or item.get("kind") != "goods":
            continue
        sku = str(sku)
        if stocked[sku]:
            continue
        cat = str(item.get("category") or "")
        if not cat or (demand_categories and cat not in demand_categories):
            continue
        if any(word in cat for word in FOOD_KEYWORDS):
            continue
        # Prefer items without an explicit expiry field; no true shelf-life guarantee.
        if item.get("perishable") is True:
            continue
        list_price = _finite_money(item.get("list_price"))
        if list_price is None or not MIN_LIST_PRICE <= list_price <= MAX_LIST_PRICE:
            continue
        estimated = list_price * COST_BUFFER_FACTOR
        if (estimated > MAX_ESTIMATED_UNIT_COST or
            unsold_cost + estimated > MAX_COLD_START_UNSOLD_COST or
            cash - estimated < floor):
            continue
        # Diversify category, prefer modest list price, deterministically break ties.
        score = (stocked_categories[cat], abs(list_price - 7_000), sku)
        choices.append((score, sku, cat, list_price, estimated))

    if not choices:
        return [], dict(info, strategy="bootstrap", detail="no conservative nonfood, low-cost catalog candidate")
    choices.sort()
    _, sku, category, list_price, estimated = choices[0]
    # Exactly ONE unit. Kit repeats about every minute, and total exposure caps at 3 units.
    return [{"sku": sku, "qty": 1}], dict(
        info, strategy="bootstrap", detail="one cautious unit, historical evidence unavailable",
        choice={"sku": sku, "category": category, "list_price": int(list_price),
                "buffered_estimated_cost": round(estimated)},
        warning="list-price estimate is NOT an actual supply quotation, and profit is not guaranteed",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    picks, info = evaluate(args.state, ignore_window=args.preview)
    if args.diagnose:
        print(json.dumps(info, ensure_ascii=False), file=sys.stderr)
    if args.preview:
        print(json.dumps({"buy": [], "preview_would_buy": picks}, ensure_ascii=False))
    else:
        print(json.dumps({"buy": picks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
