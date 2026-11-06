"""Read-only, for the two-hour check: what retries or a lower rank for one-unit listings would do to Saved and the buyer score. No network, no model. Everything past the window is an estimate.

Population: market_buyer runs in --since/--until (KST), our payments, the saved public board in --board. Rivals are held still, so score differences say how far we move, not where the board ends.
Worth of a deal = what it adds to Saved on average: food d, kit-enforced warranty (1-p)d, other goods (1-p)d - p, with the rates in buyer_support.json.
Retry deals = lost orders with another candidate x paid share of tries in the window; their discount is that candidate's estimate, its known price or the ceiling. Run from week-08; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import discount_floor as floor  # noqa: E402
import rank_analysis as ra  # noqa: E402
from quote_loss_20261106 import FILTERS, GRADES, runs, stop_of  # noqa: E402
import quote_loss_20261106 as loss  # noqa: E402
from settlement_memory import payment_outcomes  # noqa: E402

KST = timezone(timedelta(hours=9))
END = datetime(2026, 11, 17, 23, 59, tzinfo=KST)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def worth(discount, kind, grade, covered, settings):
    return float(floor.expected_discount(discount, kind, grade, covered, settings, GRADES)) if hasattr(floor, "expected_discount") else discount


def band(qty):
    return "unknown" if type(qty) not in (int, float) else "1" if qty < 2 else "2-3" if qty < 4 else "4+"


def window(start, end, settings, catalog):
    payments = payment_outcomes(ROOT / ".market/payments.jsonl")
    loss.SETTINGS = settings
    tried, paid, worths, discounts = 0, 0, [], []
    by_how = defaultdict(lambda: {"deals_read_this_way": 0, "paid": 0, "discounts": [], "mismatch_receipts": 0})
    retry = {"lost_orders": 0, "with_other": 0, "discounts": [], "worths": []}
    listing = Counter()
    one = {"selected_with_one_unit": 0, "had_alternative_with_more_stock": 0, "refused_at_open": 0, "gave_up": [], "alternative_bands": Counter()}
    mismatches = Counter()
    for moment, data in runs(start, end):
        record = data["record"]
        if not record.get("shop_name"):
            continue
        tried += 1
        trail = record.get("decision_trail") or []
        payment = payments.get(record.get("order")) or {}
        was_paid = bool(payment.get("paid"))
        paid += was_paid
        kind = "food" if (record.get("accept_conditions") or {}).get("min_hours_left") is not None else "goods"
        covered = bool((record.get("accept_conditions") or {}).get("warranty_hours"))
        reads = {t["price_read"] for t in trail if t.get("price_read")}
        how = next(iter(reads)) if len(reads) == 1 else "existing_parser" if not reads else "several"
        by_how[how]["deals_read_this_way"] += 1
        if record.get("outcome") == "receipt_mismatch":
            mismatches[how] += 1
            by_how[how]["mismatch_receipts"] += 1
        if was_paid:
            product = catalog.get(payment.get("item")) or {}
            value = product.get("list_price")
            if value:
                deal_path = ROOT / ".market/buyer-deals" / f"{payment.get('ledger')}.json"
                grade = json.loads(deal_path.read_text(encoding="utf-8")).get("grade") if deal_path.exists() else None
                discount = 1-payment["price"]/value
                discounts.append(discount)
                worths.append(worth(discount, product.get("kind"), grade, covered, settings))
                by_how[how]["paid"] += 1
                by_how[how]["discounts"].append(discount)
        stop = stop_of(record)
        key = bool(record.get("refused_at_open")) or stop in FILTERS
        if key:
            retry["lost_orders"] += 1
            others = loss.alternatives(data, record, kind, settings)
            if others:
                retry["with_other"] += 1
                best = max(others, key=lambda o: o[0])[0]
                retry["discounts"].append(best)
                retry["worths"].append(worth(best, kind, None, False, settings))
        qty = record.get("selected_offer_qty")
        listing[(band(qty), "tried")] += 1
        listing[(band(qty), "refused")] += bool(record.get("refused_at_open"))
        if band(qty) == "1":
            one["selected_with_one_unit"] += 1
            one["refused_at_open"] += bool(record.get("refused_at_open"))
            chosen = next((e for e in data.get("selection_evidence") or [] if e["shop"] == record["shop_name"] and e["sku"] == record["item"]), None)
            more = [e for e in data.get("selection_evidence") or [] if e.get("support_eligible") and type(e.get("listed_qty")) in (int, float) and e["listed_qty"] >= 2 and "expected_discount" in e]
            if chosen and "expected_discount" in chosen and more:
                best = max(more, key=lambda e: e["expected_discount"])
                one["had_alternative_with_more_stock"] += 1
                one["gave_up"].append(chosen["expected_discount"]-best["expected_discount"])
                one["alternative_bands"][band(best["listed_qty"])] += 1
    return {"tried": tried, "paid": paid, "discounts": discounts, "worths": worths, "by_how": by_how, "retry": retry, "listing": listing, "one": one}


def scores(board, me, saved, deals, posts, forum):
    """Student percentile buyer score with one row changed. Students only, as the Class view counts."""
    students = {p["buyer"]["name"]: p for p in board["players"] if p.get("student") and p.get("buyer")}
    rows = [r for r in board["buyers"] if r.get("name") in students]
    mine = next(r for r in rows if r["name"] == me)
    changed = dict(mine, saved=saved, deals=deals, posts=posts, forum=forum)
    others = [r for r in rows if r["name"] != me]
    table = others+[changed]
    return round(sum(w*ra.percentile(ra.number(changed.get(k)), [ra.number(r.get(k)) for r in table]) for k, w in ra.SAVED_WEIGHTS.items()), 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    start, end = at(args.since), at(args.until)
    settings = floor.load()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    board = json.loads(Path(args.board).read_text(encoding="utf-8"))
    me = next(r for r in board["buyers"] if r.get("name") == "kcwarni")
    seen = window(start, end, settings, catalog)
    hours = (end-start).total_seconds()/3600
    left = (END-end).total_seconds()/3600
    deals, saved = me["deals"], me["saved"]
    total = saved*(deals+100)
    pay_share = seen["paid"]/seen["tried"] if seen["tried"] else 0
    mean = lambda v: sum(v)/len(v) if v else None
    base_per_hour = seen["paid"]/hours
    base_worth = mean(seen["worths"]) or 0
    retry_per_hour = seen["retry"]["with_other"]*pay_share/hours
    retry_worth = mean(seen["retry"]["worths"]) or 0
    keep = (deals+base_per_hour*left, total+base_per_hour*left*base_worth)
    with_retry = (keep[0]+retry_per_hour*left, keep[1]+retry_per_hour*left*retry_worth)
    posts = me.get("posts", 0)+6*left
    def outcome(pair):
        final = pair[1]/(pair[0]+100)
        return {"deals_at_the_end": round(pair[0]), "saved_at_the_end": round(final, 4), "student_buyer_score": scores(board, "kcwarni", final, pair[0], posts, me.get("forum"))}
    one = seen["one"]
    listing = {b: {"tried": seen["listing"][(b, "tried")], "refused_at_open": seen["listing"][(b, "refused")], "share": round(seen["listing"][(b, "refused")]/seen["listing"][(b, "tried")], 3) if seen["listing"][(b, "tried")] else None} for b in ("1", "2-3", "4+")}
    share_one = listing["1"]["share"] or 0
    alt = one["alternative_bands"]
    alt_share = sum(alt[b]*(listing[b]["share"] or 0) for b in alt)/max(1, sum(alt.values()))
    report = {"window": {"kst": [args.since, args.until], "hours": round(hours, 3), "tries": seen["tried"], "paid": seen["paid"], "paid_per_hour": round(base_per_hour, 2), "mean_discount": round(mean(seen["discounts"]) or 0, 4), "mean_worth_after_faults": round(base_worth, 4)},
              "by_expression": {how: {"deals": row["deals_read_this_way"], "paid": row["paid"], "paid_per_hour": round(row["paid"]/hours, 2), "mean_discount": round(mean(row["discounts"]), 4) if row["discounts"] else None, "receipt_mismatches": row["mismatch_receipts"]} for how, row in sorted(seen["by_how"].items())},
              "board_now": {"saved": saved, "deals": deals, "posts": me.get("posts"), "forum": me.get("forum"), "student_buyer_score": scores(board, "kcwarni", saved, deals, me.get("posts", 0), me.get("forum"))},
              "projection_assumptions": {"hours_left_to_the_round_end": round(left, 1), "rivals": "held still", "pace": "this window's", "posts": "6 an hour all the way", "forum": "unchanged"},
              "retry_inputs": {"lost_orders": seen["retry"]["lost_orders"], "with_another_candidate": seen["retry"]["with_other"], "rescued_per_hour_estimate": round(retry_per_hour, 2), "mean_discount_of_rescued": round(mean(seen["retry"]["discounts"]) or 0, 4), "mean_worth_of_rescued_after_faults_unknown_grade": round(retry_worth, 4)},
              "without_retry": outcome(keep), "with_retry": outcome(with_retry),
              "one_unit_listings": {"by_listed_qty": listing, "selected_with_one_unit": one["selected_with_one_unit"], "of_them_with_an_eligible_candidate_with_two_or_more": one["had_alternative_with_more_stock"], "median_expected_discount_given_up": round(median(one["gave_up"]), 4) if one["gave_up"] else None,
                                    "mean_expected_discount_given_up": round(mean(one["gave_up"]) or 0, 4), "refusals_now_on_those_listings": one["refused_at_open"], "refusals_avoided_estimate": round(one["had_alternative_with_more_stock"]*(share_one-alt_share), 1)}}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
