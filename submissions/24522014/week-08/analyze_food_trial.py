"""Read-only verdict on the food trial from local records: arrival, cost, expiry, sale, profit and discards."""

import argparse
import glob
import json
from pathlib import Path
from statistics import median
import time

ROOT = Path(__file__).resolve().parent
STOP = {"discards": 1, "min_sold_within_24h": .5, "min_profit_vs_goods_median": .25}


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def report(root=ROOT, now=None):
    root = Path(root)
    now = time.time() if now is None else now
    md = root / ".market"
    state = read(md / "food-trial.json", {"planned": {}, "units": {}})
    settlements = (read(md / "local-settlements.json", {}) or {}).get("records") or {}
    records = list(settlements.values()) if isinstance(settlements, dict) else settlements
    by_unit = {r.get("unit"): r for r in records if r.get("unit")}
    offers = {}
    for path in glob.glob(str(md / "signed-offers/*.json")):
        offer = read(path, {})
        if offer.get("unit"):
            offers[offer["unit"]] = offer
    held = {u.get("unit") for u in (read(md / "inventory.json", {}) or {}).get("units") or []}
    catalog = read(md / "catalog.json", {}) or {}
    rows = []
    for unit, facts in (state.get("units") or {}).items():
        sale = by_unit.get(unit)
        price = sale.get("amount") if sale else None
        arrived = facts.get("supplied_at") or facts.get("first_seen_at")
        sold_at = sale.get("ts") if sale else None
        expired = bool(facts.get("expires_at") and now >= facts["expires_at"])
        rows.append({"unit": unit, "sku": facts.get("sku"), "cost": facts.get("cost"), "grade_on_arrival": facts.get("grade_on_arrival"), "arrived_at": arrived,
                     "expires_at": facts.get("expires_at"), "sold_at": sold_at, "price": price,
                     "profit": price - facts["cost"] if price is not None and facts.get("cost") is not None else None,
                     "hours_to_sale": round((sold_at - arrived) / 3600, 2) if sold_at and arrived else None,
                     "discarded": not sale and unit not in held and expired, "still_held": unit in held})
    goods_profit = [o["price"] - o["cost"] for o in offers.values() if (catalog.get(o.get("sku")) or {}).get("kind") == "goods"
                    and type(o.get("cost")) in (int, float) and type(o.get("price")) in (int, float)]
    goods_median = median(goods_profit) if goods_profit else None
    profits = [r["profit"] for r in rows if r["profit"] is not None]
    mature = [r for r in rows if r["arrived_at"] and now - r["arrived_at"] >= 86400]
    sold_in_time = [r for r in mature if r["hours_to_sale"] is not None and r["hours_to_sale"] <= 24]
    signals = []
    discards = sum(r["discarded"] for r in rows)
    if discards >= STOP["discards"]:
        signals.append("discarded_unsold")
    if mature and len(sold_in_time) / len(mature) < STOP["min_sold_within_24h"]:
        signals.append("sold_within_24h_below_half")
    if profits and goods_median and sum(profits) / len(profits) < goods_median * STOP["min_profit_vs_goods_median"]:
        signals.append("profit_below_quarter_of_goods_median")
    return {"stop_rules": STOP, "planned": state.get("planned") or {}, "units": rows, "units_arrived": len(rows), "units_sold": len(profits), "discards": discards,
            "mature_units": len(mature), "sold_within_24h": len(sold_in_time), "profit_per_deal": round(sum(profits) / len(profits), 1) if profits else None,
            "goods_profit_median": goods_median, "estimated_cost_total": round(sum(p.get("estimated_cost", 0) for p in (state.get("planned") or {}).values())),
            "actual_cost_total": round(sum(r["cost"] or 0 for r in rows)), "stop_signals": signals, "stop": bool(signals)}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(report(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
