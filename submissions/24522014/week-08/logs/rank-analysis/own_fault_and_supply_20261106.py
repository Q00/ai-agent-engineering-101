"""Read-only: why our shop's fault share looks high, whether it touches stars, and whether supply cost is climbing under the patch 3.4 surge rule. No network, no model. Only ratios and counts: no unit cost is written.

Populations: the 11-06 board for shops with 100+ deals; saved public rows (star audit, then market-intel) for shop mix and grades; our supplies from logs/market-kit.log joined by name to the catalog.
A shop's goods share comes from sampled rows matched by the URL on its board card, so shops whose URL changed are left out and counted. Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
KST = timezone(timedelta(hours=9))
BOARD = "logs/rank-analysis/board-20261106T0150Z.json"
SUPPLY = re.compile(r"^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.+) for (\d+), balance")
RATES = {"A": .027, "B": .098, "C": .331}  # no warranty, fault-rates-20261106.json


def load_rows():
    rows = {}
    for name in ("logs/star-audit/20261105/rows.jsonl", "logs/star-audit/20261105/rows-tail.jsonl"):
        with (ROOT / name).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                row = json.loads(line)
                rows[row["seq"]] = row
    for row in json.loads((ROOT / ".market/market-intel.json").read_text(encoding="utf-8"))["rows"]:
        rows[row["seq"]] = row
    return rows


def correlation(pairs):
    if len(pairs) < 3:
        return None
    x, y = zip(*pairs)
    mx, my = sum(x)/len(x), sum(y)/len(y)
    bottom = math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
    return round(sum((a-mx)*(b-my) for a, b in pairs)/bottom, 3) if bottom else None


def faults(board, rows, catalog, local):
    units = {r.get("unit") for r in local}
    urls = {r["data"]["shop"] for r in rows.values() if r["kind"] == "settle" and r["data"].get("unit") in units}
    kinds = defaultdict(Counter)
    for row in rows.values():
        if row["kind"] == "settle":
            kinds[row["data"].get("shop")][(catalog.get(row["data"].get("item")) or {}).get("kind")] += 1
    table, missing = [], 0
    for shop in board["shops"]:
        if (shop.get("deals") or 0) < 100:
            continue
        mix = kinds.get((shop.get("card") or {}).get("url"))
        own = shop.get("name") == "kcwarni"
        if own:
            mix = Counter()
            for url in urls:
                mix.update(kinds[url])
        total = sum(mix.values()) if mix else 0
        share = mix["goods"]/total if total >= 20 else None
        missing += share is None
        table.append({"name": "our shop" if own else "other", "deals": shop["deals"], "broken": shop.get("broken") or 0, "fault_per_deal": (shop.get("broken") or 0)/shop["deals"], "goods_share": share,
                      "stars": shop.get("stars"), "stars_rank": shop.get("stars_rank")})
    known = [r for r in table if r["goods_share"]]
    for row in known:
        row["fault_per_goods_deal"] = row["fault_per_deal"]/row["goods_share"]
    me = next(r for r in table if r["name"] == "our shop")
    settled = [r["data"] for r in rows.values() if r["kind"] == "settle" and r["data"].get("unit") in units and (catalog.get(r["data"].get("item")) or {}).get("kind") == "goods"]
    grades = Counter(d.get("grade") for d in settled)
    market = Counter(r["data"].get("grade") for r in rows.values() if r["kind"] == "settle" and (catalog.get(r["data"].get("item")) or {}).get("kind") == "goods")
    share_of = lambda c: {g: round(c[g]/sum(c.values()), 3) for g in "ABC"}
    expected = sum(RATES[g]*grades[g] for g in "ABC")/max(1, sum(grades[g] for g in "ABC"))
    per_goods = sorted(r["fault_per_goods_deal"] for r in known)
    results = [r["data"] for r in rows.values() if r["kind"] == "result" and r["data"].get("shop") in urls and r["data"].get("stars") is not None and r["data"].get("receipt")]
    broke = {r["data"].get("unit") for r in rows.values() if r["kind"] == "broken" and r["data"].get("shop") in urls}
    hit, rest = [x["stars"] for x in results if x["receipt"].get("unit") in broke], [x["stars"] for x in results if x["receipt"].get("unit") not in broke]
    low = [x for x in results if x["stars"] <= 2]
    return {"shops_100_plus": len(table), "shops_without_a_usable_mix": missing, "fault_per_deal_median": round(statistics.median(r["fault_per_deal"] for r in table), 4), "own_fault_per_deal": round(me["fault_per_deal"], 4), "own_rank_from_lowest": 1+sorted(r["fault_per_deal"] for r in table).index(me["fault_per_deal"]),
            "own_deals": me["deals"], "own_broken": me["broken"], "own_goods_share_in_sample": me["goods_share"], "goods_share_median_others": round(statistics.median(r["goods_share"] for r in known if r["name"] != "our shop"), 3),
            "fault_per_goods_deal_median": round(statistics.median(per_goods), 4), "own_fault_per_goods_deal": round(me.get("fault_per_goods_deal", 0), 4), "own_rank_per_goods_deal_from_lowest": 1+per_goods.index(me["fault_per_goods_deal"]), "shops_compared_per_goods_deal": len(per_goods),
            "own_grade_mix_goods": share_of(grades), "market_grade_mix_goods": share_of(market), "expected_fault_at_own_mix": round(expected, 4), "own_goods_sold_in_sample": sum(grades.values()),
            "correlation_fault_per_deal_with_stars_across_shops": correlation([(r["fault_per_deal"], r["stars"]) for r in table if type(r["stars"]) in (int, float)]),
            "correlation_fault_per_goods_deal_with_stars_across_shops": correlation([(r["fault_per_goods_deal"], r["stars"]) for r in known if type(r["stars"]) in (int, float)]),
            "own_reviews": {"sample": len(results), "stars_mean_units_that_broke": round(sum(hit)/len(hit), 3) if hit else None, "n_broke": len(hit), "stars_mean_other_units": round(sum(rest)/len(rest), 3) if rest else None, "low_reviews_1_or_2": len(low), "low_reviews_on_units_that_broke": sum(x["receipt"].get("unit") in broke for x in low)},
            "note": "Reviews are written at payment, before a unit can break, so a break cannot lower that deal's stars."}


def supply(catalog):
    """Each supply's price per list price, by day and hour, from the kit log. The log has no date, so a clock going backwards starts a new day."""
    by_name = {(p.get("name") or ""): p for p in catalog.values()}
    day, last, rows = 0, "", []
    with (ROOT / "logs/market-kit.log").open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            stamp = line[:8]
            if re.fullmatch(r"\d\d:\d\d:\d\d", stamp):
                day += stamp < last
                last = stamp
            found = SUPPLY.match(line)
            if found:
                product = by_name.get(found[3].strip())
                if product and product.get("list_price"):
                    rows.append((day, stamp[:2], int(found[4])/int(found[2])/product["list_price"], found[3].strip()))
    if not rows:
        return {"supplies": 0}
    days = sorted({r[0] for r in rows})
    above = [r for r in rows if r[2] > .75]
    return {"supplies_matched_to_the_catalog": len(rows), "log_days": len(days), "by_day_index": {str(d): {"supplies": sum(r[0] == d for r in rows), "median_share_of_list": round(statistics.median(r[2] for r in rows if r[0] == d), 3), "max_share_of_list": round(max(r[2] for r in rows if r[0] == d), 3),
                                                                                                         "above_the_contract_top_75pct": sum(r[0] == d and r[2] > .75 for r in rows)} for d in days[-4:]},
            "above_the_contract_top_75pct": len(above), "share_above": round(len(above)/len(rows), 4), "highest_share_of_list": round(max(r[2] for r in rows), 3), "at_or_above_list": sum(r[2] >= 1 for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    board = json.loads((ROOT / BOARD).read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    local = json.loads((ROOT / ".market/local-settlements.json").read_text(encoding="utf-8")).get("records") or {}
    local = list(local.values()) if isinstance(local, dict) else local
    report = {"faults": faults(board, load_rows(), catalog, local), "supply_cost": supply(catalog)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
