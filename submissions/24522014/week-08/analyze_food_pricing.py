"""Read-only evidence for food pricing: public food sales by shelf life left, and how long food orders need it to last.

Public settled rows show what buyers paid, with the unit's expiry at sale. They show neither the seller's cost nor how long
a unit waited, so sale speed here is only our own trial. Use periods come from food orders this buyer saw, a sample of all
orders. Every price here is what sold; asks that did not sell are not public.
"""

import argparse
from collections import Counter
import glob
import json
from pathlib import Path
from statistics import quantiles

from investigate_market import ledger_rows

ROOT = Path(__file__).resolve().parent
BANDS = ((0, .1), (.1, .25), (.25, .45), (.45, .8), (.8, 1.01))
TRIAL = ("식품>김치/반찬/즉석요리>김치", "식품>육류>가공/조리 육류")


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def spread(values):
    values = sorted(values)
    if not values:
        return None
    if len(values) < 4:
        return {"p25": values[0], "p50": values[len(values) // 2], "p75": values[-1]}
    low, mid, high = quantiles(values, n=4)
    return {"p25": round(low, 3), "p50": round(mid, 3), "p75": round(high, 3)}


def sales(root, catalog):
    """One row per public food sale that has a usable price, expiry and shelf life."""
    rows = []
    for row in ledger_rows(root):
        data = row.get("data") or {}
        if row.get("kind") != "settle":
            continue
        product = catalog.get(data.get("item")) or {}
        price, expires, shelf, listed = data.get("amount"), data.get("expires_at"), product.get("shelf_hours"), product.get("list_price")
        if product.get("kind") != "food" or not (type(price) in (int, float) and price > 0 and expires and shelf and listed):
            continue
        rows.append({"share": price / listed, "left": (expires - row["ts"]) / (shelf * 3600), "hours_left": (expires - row["ts"]) / 3600, "shelf": shelf,
                     "grade": data.get("grade"), "category": product.get("category"), "sku": data["item"], "ts": row["ts"], "listed": listed})
    return rows


def by_band(rows):
    total = len(rows)
    table = []
    for low, high in BANDS:
        inside = [r for r in rows if low <= r["left"] < high]
        table.append({"left": f"{low:.2f}-{min(high, 1):.2f}", "sales": len(inside), "sales_share": round(len(inside) / total, 4) if total else None,
                      "price_per_list": spread([r["share"] for r in inside]), "hours_left": spread([r["hours_left"] for r in inside]),
                      "grades": dict(Counter(r["grade"] for r in inside).most_common())})
    return table


def use_shares(root, catalog):
    """Each food order's use period as a share of the shelf life of the SKU the buyer tried."""
    seen = {}
    for path in glob.glob(str(Path(root) / "logs/market-buyer/*.json")):
        record = (read(path, {}) or {}).get("record") or {}
        hours = (record.get("accept_conditions") or {}).get("min_hours_left")
        shelf = (catalog.get(record.get("item")) or {}).get("shelf_hours")
        if record.get("order") and type(hours) in (int, float) and shelf:
            seen[record["order"]] = round((hours - 2) / shelf, 2)
    return dict(sorted(Counter(seen.values()).items()))


def replay_first_trial(root, catalog, policy_rule):
    """The first trial's units at the price the new rule would allow. Whether a buyer would have paid is unknown."""
    from r3_policy import food_terms
    state = read(Path(root) / ".market/food-trial.json", {}) or {}
    records = (read(Path(root) / ".market/local-settlements.json", {}) or {}).get("records") or {}
    records = list(records.values()) if isinstance(records, dict) else records
    rows = []
    for unit, facts in (state.get("units") or {}).items():
        sale = next((r for r in records if r.get("unit") == unit), None)
        product = catalog.get(facts.get("sku")) or {}
        if not sale or not product.get("shelf_hours"):
            continue
        left = (facts["expires_at"] - sale["ts"]) / (product["shelf_hours"] * 3600)
        terms = food_terms(facts["cost"], product["list_price"], left, policy_rule)
        rows.append({"sku": facts["sku"], "list_price": product["list_price"], "cost": facts["cost"], "left_at_sale": round(left, 3), "minutes_to_sale": round((sale["ts"] - facts["supplied_at"]) / 60, 1),
                     "sold_at": sale["amount"], "profit": sale["amount"] - facts["cost"], "new_floor": terms[0] if terms else None, "new_quote": terms[1] if terms else None,
                     "profit_at_new_floor": terms[0] - facts["cost"] if terms else None, "profit_at_new_quote": terms[1] - facts["cost"] if terms else None,
                     "pricing": terms[2] if terms else "unchanged_standard"})
    return rows


def public_reach(rows, rule):
    """How much of what really sold in each band was at or above the new floor and quote."""
    table = []
    bounds = [(b["left_from"], rule["standard_from_left"] if i == 0 else rule["market_bands"][i - 1]["left_from"], b) for i, b in enumerate(rule["market_bands"])]
    for low, high, band in bounds:
        inside = [r for r in rows if low <= r["left"] < high]
        table.append({"left": f"{low:.2f}-{high:.2f}", "sales": len(inside), "at_or_above_floor": round(sum(r["share"] >= band["floor_share"] for r in inside) / len(inside), 3) if inside else None,
                      "at_or_above_quote": round(sum(r["share"] >= band["quote_share"] for r in inside) / len(inside), 3) if inside else None})
    return table


def use_periods(root, catalog):
    """Each distinct food order's use period, from what the buyer recorded. min_hours_left adds 2 hours for delivery and safety."""
    seen = {}
    for path in glob.glob(str(Path(root) / "logs/market-buyer/*.json")):
        record = (read(path, {}) or {}).get("record") or {}
        hours = (record.get("accept_conditions") or {}).get("min_hours_left")
        if record.get("order") and type(hours) in (int, float):
            seen[record["order"]] = {"hours": hours - 2, "category": record.get("category")}
    return list(seen.values())


def demand_at(orders, hours):
    return round(sum(o["hours"] <= hours for o in orders) / len(orders), 4) if orders else None


def report(root=ROOT):
    root = Path(root)
    catalog = read(root / ".market/catalog.json", {}) or {}
    rows = sales(root, catalog)
    trial = [r for r in rows if r["category"] in TRIAL]
    orders = use_periods(root, catalog)
    trial_orders = [o for o in orders if o["category"] in TRIAL]
    from r3_policy import load_policy
    rule = load_policy()["selling"]["food_pricing_v2"]
    return {"replay_first_trial": replay_first_trial(root, catalog, rule), "public_reach_of_new_prices": public_reach(trial, rule), "use_period_share_of_shelf": use_shares(root, catalog),
            "public_food_sales": len(rows), "by_shelf_left_all_food": by_band(rows), "by_shelf_left_trial_categories": by_band(trial),
            "by_shelf_left_each_category": {name: by_band([r for r in rows if r["category"] == name]) for name in TRIAL},
            "use_period_hours_all_food": {"orders": len(orders), "spread": spread([o["hours"] for o in orders]), "max": max((o["hours"] for o in orders), default=None)},
            "use_period_hours_trial_categories": {"orders": len(trial_orders), "spread": spread([o["hours"] for o in trial_orders]),
                                                  "max": max((o["hours"] for o in trial_orders), default=None),
                                                  "orders_covered_at_hours": {str(h): demand_at(trial_orders, h) for h in (6, 12, 24, 48, 72, 120, 168)}}}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(report(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
