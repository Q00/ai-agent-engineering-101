"""Read-only: track the stock expansion against numeric stop rules, and size a possible food trial from public data.

Before and after figures are shown side by side and are not causal: the market, rivals and our other settings all moved.
Food numbers are estimates from public settlements and contract cost ranges, since only the shops know their real costs.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import json
from pathlib import Path
from statistics import median
import time

from investigate_market import ledger_rows, read
from rank_analysis import snapshot_points

ROOT = Path(__file__).resolve().parent
EXPANSION_AT = datetime(2026, 11, 4, 13, 20, 55, tzinfo=timezone.utc).timestamp()
STOP = {"min_profit_rate_vs_before": .5, "max_storage_per_profit": .3, "max_share_older_than_24h": .3, "max_broken_rate_vs_before": 1.5, "min_hours_for_verdict": 12}
GRADE_COST = {"A": .65, "B": .5, "C": .35}  # Midpoints of the contract's cost share ranges.


def rates(points, start, end):
    window = [p for p in points if start <= p["observed_at"] <= end]
    if len(window) < 2:
        return None
    first, last = window[0], window[-1]
    hours = (last["observed_at"] - first["observed_at"]) / 3600
    if hours <= 0:
        return None
    result = {"from": first["observed_at"], "to": last["observed_at"], "hours": round(hours, 2)}
    for key in ("visits", "deals", "profit", "revenue", "broken"):
        a, b = first["shop"].get(key), last["shop"].get(key)
        result[f"{key}_per_hour"] = round((b - a) / hours, 2) if type(a) in (int, float) and type(b) in (int, float) else None
    return result


def shop_series(root):
    rows = []
    for path in sorted(glob.glob(str(Path(root) / "logs/market-intel/*.json"))):
        data = read(path, {})
        shop = ((data.get("competition") or {}).get("own") or {}).get("shops")
        if data.get("round") == 3 and shop and data.get("observed_at"):
            rows.append({"observed_at": data["observed_at"], "shop": {k: shop.get(k) for k in ("visits", "deals", "profit", "revenue", "broken", "stars")}})
    return rows


def stock_now(root, now):
    inventory = read(Path(root) / ".market/inventory.json", {})
    units = inventory.get("units") or []
    rate = inventory.get("storage_per_day", .01)
    ages = [(now - u["supplied_at"]) / 3600 for u in units if type(u.get("supplied_at")) in (int, float)]
    catalog = read(Path(root) / ".market/catalog.json", {})
    return {"units": len(units), "skus": len({u.get("sku") for u in units}), "categories": len({u.get("category") or (catalog.get(u.get("sku")) or {}).get("category") for u in units}),
            "cost": round(sum(u.get("cost", 0) for u in units)), "storage_per_hour": round(sum(u.get("cost", 0) for u in units) * rate / 24, 1),
            "age_hours_median": round(median(ages), 1) if ages else None, "share_older_than_24h": round(sum(a >= 24 for a in ages) / len(ages), 3) if ages else None,
            "balance": inventory.get("balance")}


def restock_runs(root, since):
    bought, runs, reasons = Counter(), 0, Counter()
    stamp = datetime.fromtimestamp(since, timezone.utc).strftime("%Y%m%dT%H%M%S")
    for path in sorted(glob.glob(str(Path(root) / "logs/restock/*.json"))):
        if Path(path).name < stamp:
            continue
        row = read(path, {})
        reasons[(row.get("explanation") or {}).get("reason")] += 1
        for buy in row.get("buy") or []:
            bought[buy["sku"]] += buy["qty"]
        runs += bool(row.get("buy"))
    return {"runs_with_purchases": runs, "units_ordered": sum(bought.values()), "skus_ordered": len(bought), "plan_reasons": dict(reasons)}


def stop_signals(before, after, stock):
    signals = []
    if not before or not after or after["hours"] < STOP["min_hours_for_verdict"]:
        return {"verdict_possible": False, "signals": signals, "note": f"needs at least {STOP['min_hours_for_verdict']} hours after the change"}
    if before.get("profit_per_hour") and (after.get("profit_per_hour") or 0) < before["profit_per_hour"] * STOP["min_profit_rate_vs_before"]:
        signals.append("profit_rate_below_half_of_before")
    if after.get("profit_per_hour") and stock["storage_per_hour"] > after["profit_per_hour"] * STOP["max_storage_per_profit"]:
        signals.append("storage_above_30_percent_of_profit")
    if (stock.get("share_older_than_24h") or 0) >= STOP["max_share_older_than_24h"]:
        signals.append("over_30_percent_of_stock_older_than_24h")
    rate_before = (before.get("broken_per_hour") or 0) / before["deals_per_hour"] if before.get("deals_per_hour") else None
    rate_after = (after.get("broken_per_hour") or 0) / after["deals_per_hour"] if after.get("deals_per_hour") else None
    if rate_before and rate_after and rate_after > rate_before * STOP["max_broken_rate_vs_before"]:
        signals.append("broken_rate_up_50_percent")
    return {"verdict_possible": True, "signals": signals, "action_if_any": "set max_total_units back to 16; stock then sells down without new buys"}


def expansion(root, now=None):
    now = time.time() if now is None else now
    series = shop_series(root)
    before = rates(series, EXPANSION_AT - 24 * 3600, EXPANSION_AT)
    after = rates(series, EXPANSION_AT, now)
    stock = stock_now(root, now)
    return {"changed_at": EXPANSION_AT, "stop_rules": STOP, "before_24h": before, "after": after, "stock_now": stock,
            "restock_since_change": restock_runs(root, EXPANSION_AT), "stop": stop_signals(before, after, stock)}


def own_use_requirements(root):
    """use_within_hours our own buyer saw per food category: a sample of what orders ask for."""
    needs = defaultdict(list)
    for path in sorted(glob.glob(str(Path(root) / "logs/market-buyer/2026110[34]T*.json"))):
        record = read(path, {}).get("record") or {}
        hours = (record.get("accept_conditions") or {}).get("min_hours_left")
        if hours is not None and record.get("category"):
            needs[record["category"]].append(hours - 2)
    return needs


def food(root, rows, shops_path):
    catalog = read(Path(root) / ".market/catalog.json", {})
    registry = read(shops_path, {})
    registry = registry.get("shops", registry) if isinstance(registry, dict) else registry
    stock_by_sku = Counter()
    url_to_name = {s.get("url"): s.get("name") for s in registry}
    for shop in registry:
        for sku, qty in (shop.get("stock") or {}).items():
            stock_by_sku[sku] += qty
    now = max((r["ts"] for r in rows if r.get("ts")), default=time.time())
    settle = [r for r in rows if r.get("kind") == "settle" and r.get("data")]
    day = [r for r in settle if now - r["ts"] <= 86400]
    by_cat = defaultdict(list)
    for row in day:
        product = catalog.get(row["data"].get("item")) or {}
        if product.get("kind") == "food":
            by_cat[product.get("category")].append((row, product))
    uses = own_use_requirements(root)
    table = []
    for category, entries in by_cat.items():
        skus = {r["data"]["item"] for r, _ in entries}
        ratios, margins, grades, left_hours, shelf_hours = [], [], Counter(), [], []
        for row, product in entries:
            data = row["data"]
            amount = data.get("amount")
            if type(amount) not in (int, float) or not product.get("list_price"):
                continue
            ratio = amount / product["list_price"]
            ratios.append(ratio)
            grades[data.get("grade")] += 1
            if data.get("grade") in GRADE_COST:
                margins.append(ratio - GRADE_COST[data["grade"]])
            if type(data.get("expires_at")) in (int, float):
                left_hours.append((data["expires_at"] - row["ts"]) / 3600)
            if product.get("shelf_hours"):
                shelf_hours.append(product["shelf_hours"])
        stock = sum(stock_by_sku[s] for s in skus)
        per_hour = len(entries) / 24
        need = uses.get(category, [])
        needed = median(need) if need else None
        typical_shelf = median(shelf_hours) if shelf_hours else None
        # A grade B unit starts near 45-80% of its shelf life; use the low end to stay cautious.
        cautious_left = typical_shelf * .45 if typical_shelf else None
        table.append({"category": category, "settlements_24h": len(entries), "skus_sold": len(skus), "shops": len({r["data"].get("shop") for r, _ in entries}),
                      "buyers": len({r["data"].get("buyer") for r, _ in entries}), "market_stock_of_those_skus": stock,
                      "hours_of_market_stock_at_this_rate": round(stock / per_hour, 1) if per_hour else None,
                      "price_per_list_median": round(median(ratios), 3) if ratios else None, "margin_per_list_estimate_median": round(median(margins), 3) if margins else None,
                      "shelf_hours_median": typical_shelf, "hours_left_at_sale_median": round(median(left_hours), 1) if left_hours else None,
                      "our_orders_use_within_median": needed, "our_order_samples": len(need),
                      "grade_share_c": round(grades["C"] / max(sum(grades.values()), 1), 3),
                      "cautious_hours_left_grade_b": round(cautious_left, 1) if cautious_left else None,
                      "room_after_use_within": round(cautious_left - needed, 1) if cautious_left is not None and needed is not None else None,
                      "list_price_median": median(p["list_price"] for _, p in entries if p.get("list_price"))})
    table.sort(key=lambda r: -r["settlements_24h"])
    per_shop = defaultdict(Counter)
    for row in day:
        per_shop[url_to_name.get(row["data"].get("shop"))][(catalog.get(row["data"].get("item")) or {}).get("kind")] += 1
    top = sorted(per_shop.items(), key=lambda x: -sum(x[1].values()))[:8]
    return {"window": "latest 24 hours of saved public rows", "food_settlements_24h": sum(len(v) for v in by_cat.values()), "all_settlements_24h": len(day),
            "categories": table[:14], "top_shops_kind_mix": {str(k): dict(v) for k, v in top},
            "assumptions": "Cost is the contract's midpoint by grade. Stock is one moment. Grade B is assumed to arrive near its 45% lower bound."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    shops = sorted(glob.glob(str(ROOT / "logs/rank-analysis/shops-*.json")))[-1]
    print(json.dumps({"expansion": expansion(ROOT), "food": food(ROOT, ledger_rows(ROOT), shops)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
