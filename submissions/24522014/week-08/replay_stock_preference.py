"""Read-only replay of the listed-stock preference over saved hook runs. Nothing is sent, bought or written.

Older runs saved no stock figure for candidates that were not chosen, so a candidate's stock is taken from the closest run
that did choose that shop and SKU, within --window seconds; with no such run it stays unknown, the lowest band. Newer runs
save listed_qty directly. Only the first pick is replayed: what a different choice would have changed later in the run
(cooldowns, shop stock, what the shop then answered) cannot be known, so refusal figures are estimates from each band's rate.
"""

import argparse
from bisect import bisect_left
from collections import Counter
import json
from pathlib import Path

from buyer_funnel import stamp
from buyer_support import prefer_listed_stock, stock_band

ROOT = Path(__file__).resolve().parent
BANDS = {0: "unknown", 1: "1", 2: "2-3", 3: "4+"}


def epoch(name):
    from datetime import datetime, timezone
    return datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()


def load(root, since, until=None):
    runs = []
    for path in sorted((Path(root) / "logs/market-buyer").glob("*.json")):
        name = path.name.split("-")[0]
        if name < since or (until and name >= until):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        record = data["record"]
        if record.get("outcome") == "research_skipped" or type(record.get("selected_offer_qty")) not in (int, float):
            continue
        eligible = [e for e in data.get("selection_evidence") or () if e.get("support_eligible")]
        chosen = next((e for e in eligible if e["shop"] == record.get("shop_name") and e["sku"] == record.get("item")), None)
        if chosen:
            runs.append({"at": epoch(name), "record": record, "eligible": eligible, "chosen": chosen})
    return runs


def stock_lookup(runs, window):
    seen = {}
    for run in runs:
        seen.setdefault((run["chosen"]["shop"], run["chosen"]["sku"]), []).append((run["at"], run["record"]["selected_offer_qty"]))

    def closest(shop, sku, at):
        rows = seen.get((shop, sku))
        if not rows:
            return None
        spot = bisect_left(rows, (at, -1))
        nearby = rows[max(0, spot - 1):spot + 1]
        moment, qty = min(nearby, key=lambda row: abs(row[0] - at))
        return qty if abs(moment - at) <= window else None
    return closest


def replay(runs, tolerance, window):
    closest = stock_lookup(runs, window)
    attempts, refused = Counter(), Counter()
    for run in runs:
        band = stock_band(run["record"]["selected_offer_qty"])
        attempts[band] += 1
        refused[band] += bool(run["record"].get("refused_at_open"))
    rates = {band: refused[band] / attempts[band] for band in attempts}
    rule = {"enabled": True, "surplus_tolerance_ratio": tolerance}
    changed, known, given_up, surplus_before, expected_avoided, actual_refused = 0, 0, 0, 0, 0.0, 0
    before, after, band_moves = Counter(), Counter(), Counter()
    for run in runs:
        rows = []
        for entry in run["eligible"]:
            qty = entry.get("listed_qty")
            rows.append(dict(entry, listed_qty=qty if qty is not None else closest(entry["shop"], entry["sku"], run["at"])))
        first = next(row for row in rows if row["shop"] == run["chosen"]["shop"] and row["sku"] == run["chosen"]["sku"])
        first["listed_qty"] = run["record"]["selected_offer_qty"]
        known += sum(row["listed_qty"] is not None for row in rows) / len(rows)
        pick = prefer_listed_stock(rows, first, rule)
        before[first["shop"]] += 1
        after[pick["shop"]] += 1
        surplus_before += first["estimated_surplus"]
        if pick is first:
            continue
        changed += 1
        given_up += first["estimated_surplus"] - pick["estimated_surplus"]
        old_band, new_band = stock_band(first["listed_qty"]), stock_band(pick["listed_qty"])
        band_moves[f"{BANDS[old_band]}->{BANDS[new_band]}"] += 1
        expected_avoided += rates.get(old_band, 0) - rates.get(new_band, 0)
        actual_refused += bool(run["record"].get("refused_at_open"))

    def top(counter):
        total = sum(counter.values()) or 1
        return [{"shop": name, "share": round(count / total, 4)} for name, count in counter.most_common(5)]
    return {"tolerance": tolerance, "runs": len(runs), "changed": changed, "changed_share": round(changed / len(runs), 4) if runs else None,
            "candidates_with_a_stock_figure": round(known / len(runs), 4) if runs else None, "band_moves": dict(band_moves.most_common()),
            "refused_at_open_among_changed": actual_refused, "refusals_avoided_estimate": round(expected_avoided, 1),
            "estimated_surplus_given_up": round(given_up), "estimated_surplus_share_given_up": round(given_up / surplus_before, 5) if surplus_before else None,
            "top_shops_before": top(before), "top_shops_after": top(after)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    parser.add_argument("--window", type=int, default=1800)
    parser.add_argument("--tolerance", type=float, action="append")
    args = parser.parse_args()
    runs = load(ROOT, stamp(args.since), stamp(args.until) if args.until else None)
    refusal = {}
    for run in runs:
        row = refusal.setdefault(BANDS[stock_band(run["record"]["selected_offer_qty"])], {"attempts": 0, "refused_at_open": 0})
        row["attempts"] += 1
        row["refused_at_open"] += bool(run["record"].get("refused_at_open"))
    print(json.dumps({"window_seconds": args.window, "refusals_by_listed_qty": refusal,
                      "replays": [replay(runs, t, args.window) for t in args.tolerance or (.03, .05, .1)]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
