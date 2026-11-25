"""Read-only: how often settled goods broke, by grade and warranty, from public data already saved on disk. No network, no model.

Population: Round 3 `settle` rows of goods (catalog kind) in the star-audit ledger sample (2026-11-03 21:04 to 11-05 10:42 KST), all shops and buyers. Sales newer than the
last saved row minus the break window are left out. That sample holds under a quarter of the ledger in separate pages, so a sale's `broken` row is often missing and the matched rate
comes out too low. The board counts every break, so its total scales the matched rates. The scale assumes the missing rows do not depend on grade; our own deals are the check on it.
Wilson 95% intervals, scaled the same way. Run from week-08 with --out; prints the same JSON. Holds no costs or floors of ours.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
WINDOW_HOURS = 6
SOURCES = ("logs/star-audit/20261105/rows.jsonl", "logs/star-audit/20261105/rows-tail.jsonl")
BOARD = "logs/star-audit/20261105/board.json"


def wilson(hits, total):
    if not total:
        return None
    z = 1.96
    share = hits/total
    middle = share+z*z/(2*total)
    spread = z*math.sqrt(share*(1-share)/total+z*z/(4*total*total))
    return [round((middle-spread)/(1+z*z/total), 4), round((middle+spread)/(1+z*z/total), 4)]


def saved_rows():
    """(rows by seq, what each file held). A line that is not a ledger row is skipped and counted."""
    rows, sources = {}, {}
    batches = []
    for name in SOURCES:
        path = ROOT / name
        if path.exists():
            found = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    found.append(json.loads(line))
                except ValueError:
                    pass
            batches.append((name, found))
    for name, found in batches:
        usable = [r for r in found if isinstance(r, dict) and type(r.get("seq")) is int and r.get("round") == 3]
        stamps = [r["ts"] for r in usable if type(r.get("ts")) in (int, float)]
        sources[name] = {"rows": len(usable), "kinds": dict(Counter(r.get("kind") for r in usable)), "from_kst": stamp(min(stamps)) if stamps else None, "to_kst": stamp(max(stamps)) if stamps else None}
        rows.update({r["seq"]: r for r in usable})
    return rows, sources


def stamp(ts):
    return datetime.fromtimestamp(ts, KST).isoformat(timespec="seconds")


def cover(hours):
    if hours is None or hours == 0:
        return "no_warranty"
    return "warranty_6h_or_more" if hours >= WINDOW_HOURS else "warranty_under_6h"


def table(rows, catalog):
    settled = [r for r in rows.values() if r.get("kind") == "settle" and (catalog.get((r.get("data") or {}).get("item")) or {}).get("kind") == "goods"]
    broken = {(r.get("data") or {}).get("unit"): r for r in rows.values() if r.get("kind") == "broken"}
    last = max(r["ts"] for r in rows.values())
    done = [r for r in settled if r["ts"] <= last-WINDOW_HOURS*3600]
    cells = defaultdict(Counter)
    hours_after = []
    for row in done:
        data = row["data"]
        key = (data.get("grade"), cover(data.get("warranty_hours")))
        cells[key]["sold"] += 1
        event = broken.get(data.get("unit"))
        if event:
            cells[key]["broke"] += 1
            refund = (event.get("data") or {}).get("refunded")
            cells[key]["refunded"] += bool(refund)
            hours_after.append((event.get("data") or {}).get("hours_after_sale"))
    units = {r["data"].get("unit") for r in settled}
    report = {"goods_settled_all": len(settled), "goods_settled_past_the_window": len(done), "left_out_could_still_break": len(settled)-len(done),
              "broken_rows": len(broken), "broken_rows_without_a_saved_sale": sum(u not in units for u in broken),
              "longest_hours_after_sale": max((h for h in hours_after if h is not None), default=None), "matched_overall": sum(c["broke"] for c in cells.values())/max(1, sum(c["sold"] for c in cells.values())), "cells": {}}
    for (grade, kind), count in sorted(cells.items(), key=lambda p: (str(p[0][0]), p[0][1])):
        report["cells"][f"{grade}/{kind}"] = {"sold": count["sold"], "broke": count["broke"], "refunded": count["refunded"], "rate": round(count["broke"]/count["sold"], 4), "rate_95": wilson(count["broke"], count["sold"])}
    return report


def board_rate(rows, catalog):
    board = json.loads((ROOT / BOARD).read_text(encoding="utf-8"))
    shops = board["shops"]
    settled = [(catalog.get((r.get("data") or {}).get("item")) or {}).get("kind") for r in rows.values() if r.get("kind") == "settle"]
    share = settled.count("goods")/len(settled)
    deals, broken = sum(s.get("deals") or 0 for s in shops), sum(s.get("broken") or 0 for s in shops)
    return {"board_file": BOARD, "shops": len(shops), "deals_all_kinds": deals, "broken": broken, "refunds": sum(s.get("refunds") or 0 for s in shops), "goods_share_in_the_sample": round(share, 4),
            "goods_deals_estimated": round(deals*share), "true_rate_goods": round(broken/(deals*share), 4)}


def own_check(catalog, rates):
    """Our own purchases are completely recorded: broken.jsonl has every break, and buyer-deals every goods deal. Grade of a break is not always known, so this checks the total."""
    count = Counter()
    for path in (ROOT / ".market/buyer-deals").glob("*.json"):
        deal = json.loads(path.read_text(encoding="utf-8"))
        if (catalog.get(deal.get("sku")) or {}).get("kind") == "goods":
            count[(deal.get("grade"), "warranty" if (deal.get("warranty_hours") or 0) >= WINDOW_HOURS else "no_warranty")] += 1
    lines = (ROOT / ".market/broken.jsonl").read_text(encoding="utf-8").splitlines()
    def expected(table_):
        return round(sum(n*table_[kind][grade] for (grade, kind), n in count.items() if grade in table_[kind]), 1)
    total = sum(count.values())
    return {"goods_deals": total, "by_grade_and_warranty": {f"{g}/{k}": n for (g, k), n in sorted(count.items())}, "own_breaks_recorded": len(lines), "own_rate": round(len(lines)/total, 4),
            "own_rate_95": wilson(len(lines), total), "expected_breaks_at_scaled_rates": expected(rates["scaled"]), "expected_breaks_at_matched_rates": expected(rates["matched"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    rows, sources = saved_rows()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    report = {"sources": sources, "distinct_rows": len(rows), **table(rows, catalog)}
    overall = report.pop("matched_overall")
    truth = board_rate(rows, catalog)
    scale = truth["true_rate_goods"]/overall
    report.update(matched_overall=overall, board=truth, scale=round(scale, 3), scale_assumption="missing broken rows do not depend on grade or warranty")
    names = {"no_warranty": "no_warranty", "warranty": "warranty_6h_or_more"}
    rates = {"matched": {}, "scaled": {}}
    for kind, name in names.items():
        rates["matched"][kind] = {g: report["cells"][f"{g}/{name}"]["rate"] for g in "ABC"}
        rates["scaled"][kind] = {g: round(report["cells"][f"{g}/{name}"]["rate"]*scale, 3) for g in "ABC"}
        for g in "ABC":
            report["cells"][f"{g}/{name}"]["scaled_95"] = [round(x*scale, 3) for x in report["cells"][f"{g}/{name}"]["rate_95"]]
    report["rates"] = rates
    report["own_purchases_check"] = own_check(catalog, rates)
    intel = json.loads((ROOT / ".market/market-intel.json").read_text(encoding="utf-8"))
    recent = [r for r in intel.get("rows") or [] if r.get("round") == 3]
    goods = sum(r.get("kind") == "settle" and (catalog.get((r.get("data") or {}).get("item")) or {}).get("kind") == "goods" for r in recent)
    breaks = sum(r.get("kind") == "broken" for r in recent)
    report["later_sample_broken_per_goods_sale"] = {"file": ".market/market-intel.json", "from_kst": sources_stamp(recent, min), "to_kst": sources_stamp(recent, max), "goods_sales": goods, "broken": breaks, "rate": round(breaks/goods, 4) if goods else None}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


def sources_stamp(rows, pick):
    return stamp(pick(r["ts"] for r in rows))


if __name__ == "__main__":
    main()
