"""Read-only, the 11-06 two-hour check beside saved_projection_20261106.py: market width, the price-reading expressions turn by turn, supplies above the .75 contract top against surge, the food trial, and Saved/score with rivals moving. No network, no model.

Times KST. Rows come from market_buyer run files and logs/market-kit.log. Surge is only the current inventory.json list: past lists were not saved and the ledger has no supply rows, so a past factor is a lower bound implied by price, not an observation.
Rivals moving = each student's pace between two saved boards, kept up to the round end. Only ratios and counts are written: no unit cost, no per-SKU cost share. Run from week-08; prints the same JSON.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from analyze_food_trial import report as food_report  # noqa: E402
import buyer_pace_detail_20261105 as detail  # noqa: E402
import buyer_pace_market_20261105 as market  # noqa: E402
import rank_analysis as ra  # noqa: E402

KST = timezone(timedelta(hours=9))
END = datetime(2026, 11, 17, 23, 59, tzinfo=KST)
SUPPLY = re.compile(r"^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.+) for (\d+), balance")
SURGE_START, SURGE_RATE, SURGE_TOP = 50, .002, 1.5


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def stop(row):
    trail = row.get("decision_trail") or []
    return trail[-1].get("reason_code") if trail else (row.get("failure_reason") or row.get("outcome"))


def width(start, end):
    """Each shop-list reading in the window, and each stretch under 40 shops."""
    readings = []
    for moment, raw in market.kit_lines():
        if start <= moment.replace(tzinfo=KST) < end:
            found = market.SHOPS.search(raw)
            if found:
                readings.append((moment, int(found[1])))
    under = [(m, n) for m, n in readings if n < 40]
    return {"readings": len(readings), "min": min((n for _, n in readings), default=None), "max": max((n for _, n in readings), default=None), "median": median(n for _, n in readings) if readings else None,
            "readings_under_40": len(under), "first_under_40": under[0][0].strftime("%H:%M:%S") if under else None, "last_under_40": under[-1][0].strftime("%H:%M:%S") if under else None}


def expressions(start, end):
    payments = detail.load_payments()
    table, outcomes = {}, Counter()
    for moment, row in detail.load_records():
        if not start <= moment < end:
            continue
        outcomes[row.get("outcome")] += 1
        trail = row.get("decision_trail") or []
        for how in {t["price_read"] for t in trail if t.get("price_read")}:
            entry = table.setdefault(how, {"deals": 0, "paid": 0, "stops": Counter(), "ask_per_list": []})
            entry["deals"] += 1
            entry["paid"] += bool((payments.get(row.get("order")) or {}).get("paid"))
            entry["stops"][stop(row)] += 1
            for turn in trail:
                check = turn.get("counter_check") or {}
                if turn.get("price_read") == how and check.get("ask") and check.get("list_price"):
                    entry["ask_per_list"].append(check["ask"]/check["list_price"])
    for entry in table.values():
        share = entry.pop("ask_per_list")
        entry["stops"] = dict(entry["stops"])
        entry["ask_per_list"] = {"n": len(share), "min": round(min(share), 3), "median": round(median(share), 3), "max": round(max(share), 3)} if share else None
    return {"by_expression": table, "all_outcomes": dict(outcomes.most_common())}


def supplies(catalog, inventory):
    by_name = {(p.get("name") or ""): (sku, p) for sku, p in catalog.items()}
    surge = {r["sku"]: r for r in inventory.get("surge") or []}
    rows, day, last = [], 0, ""
    with (ROOT / "logs/market-kit.log").open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            stamp = line[:8]
            if re.fullmatch(r"\d\d:\d\d:\d\d", stamp):
                day += stamp < last
                last = stamp
            found = SUPPLY.match(line)
            if found and found[3].strip() in by_name:
                sku, product = by_name[found[3].strip()]
                if product.get("list_price"):
                    rows.append({"day": day, "sku": sku, "share": int(found[4])/int(found[2])/product["list_price"], "kind": product.get("kind")})
    above = [r for r in rows if r["share"] > .75]
    implied = [r["share"]/.75 for r in above]
    listed = [r["factor"] for r in surge.values()]
    for row in surge.values():
        assert abs(row["factor"]-min(SURGE_TOP, 1+SURGE_RATE*(row["units"]-SURGE_START))) < .002 or row["units"] <= SURGE_START, row
    return {"supplies": len(rows), "above_the_contract_top": len(above), "of_them_goods": sum(r["kind"] == "goods" for r in above), "distinct_skus_above": len({r["sku"] for r in above}),
            "implied_lowest_factor": {"min": round(min(implied), 3), "median": round(median(implied), 3), "max": round(max(implied), 3)} if implied else None,
            "implied_lowest_market_units_in_the_hour": {"median": round(SURGE_START+(median(implied)-1)/SURGE_RATE), "max": round(SURGE_START+(max(implied)-1)/SURGE_RATE)} if implied else None, "any_above_the_rule_top_1_5": sum(i > SURGE_TOP for i in implied),
            "current_surge_list": {"skus": len(surge), "factor_min": min(listed, default=None), "factor_median": round(median(listed), 3) if listed else None, "factor_max": max(listed, default=None), "formula_1_plus_002_per_unit_over_50_fits_every_row": True},
            "above_supplies_whose_sku_is_on_the_current_list": sum(r["sku"] in surge for r in above), "held_units_whose_sku_is_on_the_current_list": sum(u.get("sku") in surge for u in inventory.get("units") or [])}


def food(now, inventory, catalog):
    out = {}
    for name, trial in (("first", None), ("v2", "v2")):
        data = food_report(ROOT, now, trial)
        planned = data["planned"]
        arrived = {u["sku"] for u in data["units"]}
        times = [u["hours_to_sale"] for u in data["units"] if u["hours_to_sale"] is not None]
        ratio = data["profit_per_deal"]/data["goods_profit_median"] if data["profit_per_deal"] is not None and data["goods_profit_median"] else None
        out[name] = {"units_planned": len(planned), "units_arrived": data["units_arrived"], "units_sold": data["units_sold"], "still_held": sum(u["still_held"] for u in data["units"]), "discards": data["discards"], "mature_24h": data["mature_units"], "sold_within_24h": data["sold_within_24h"],
                     "longest_hours_to_sale": max(times, default=None), "profit_per_deal_vs_goods_median": round(ratio, 3) if ratio is not None else None, "stop_rules": data["stop_rules"], "stop_signals": data["stop_signals"], "planned_never_arrived": sorted(set(planned)-arrived)}
    out["stock_now"] = dict(Counter(u.get("kind") for u in inventory.get("units") or []))
    return out


def agreed_not_paid(start, end):
    """Deals our buyer agreed to that the kit then refused to pay. One shop's share is shown without its name."""
    payments = detail.load_payments()
    agreed = refused = 0
    reasons, shops, how_late = Counter(), Counter(), []
    for moment, row in detail.load_records():
        if not start <= moment < end or row.get("outcome") != "purchased":
            continue
        agreed += 1
        payment = payments.get(row.get("order")) or {}
        if not payment.get("paid"):
            refused += 1
            text = payment.get("reason") or "no payment record"
            reasons["grade" if "grade" in text else "shelf_life" if "shelf life" in text else "cert" if "cert" in text else "warranty" if "warranty" in text else "other"] += 1
            shops[payment.get("shop")] += 1
    return {"agreed": agreed, "not_paid": refused, "share": round(refused/agreed, 3) if agreed else None, "reasons": dict(reasons), "shops": len(shops), "from_the_top_shop": shops.most_common(1)[0][1] if shops else 0}


def board_movement(earlier, later, hours):
    def row(board):
        return next(r for r in board["buyers"] if r.get("name") == "kcwarni")
    old, new = row(earlier), row(later)
    players = {p["player"]: p for p in later["players"] if p.get("student")}
    before = {p["player"]: p for p in earlier["players"] if p.get("student")}
    rank = 1+sum((p.get("student_total") or 0) > players["kcwarni"]["student_total"] for p in players.values())
    rank_before = 1+sum((p.get("student_total") or 0) > before["kcwarni"]["student_total"] for p in before.values())
    pace = sorted(((student_rows(later)[n].get("deals") or 0)-(student_rows(earlier).get(n) or {}).get("deals", 0))/hours for n in student_rows(later) if n in student_rows(earlier))
    return {"hours": round(hours, 2), "own": {k: [old.get(k), new.get(k)] for k in ("saved", "forum", "posts", "deals", "orders")}, "deals_added_on_the_board": new["deals"]-old["deals"],
            "mean_worth_of_the_added_deals_from_the_board": round((new["saved"]*(new["deals"]+100)-old["saved"]*(old["deals"]+100))/max(1, new["deals"]-old["deals"]), 4),
            "student": {k: [before["kcwarni"].get(k), players["kcwarni"].get(k)] for k in ("student_shop", "student_buyer", "student_total", "student_pct")}, "student_rank": [rank_before, rank], "students": [len(before), len(players)], "students_deals_per_hour_median": round(median(pace), 1)}


def student_rows(board):
    names = {p["buyer"]["name"] for p in board["players"] if p.get("student") and p.get("buyer")}
    return {r["name"]: r for r in board["buyers"] if r.get("name") in names}


def score(rows, me):
    return round(sum(w*ra.percentile(ra.number(rows[me].get(k)), [ra.number(r.get(k)) for r in rows.values()]) for k, w in ra.SAVED_WEIGHTS.items()), 4)


def moved(earlier, later, hours_between, hours_left):
    """Each student's deals, posts and Saved total at the pace between two boards. Forum held: its pace is noise over two hours."""
    old, new = student_rows(earlier), student_rows(later)
    out = {}
    for name, row in new.items():
        was = old.get(name) or row
        deals = row.get("deals") or 0
        total = (row.get("saved") or 0)*(deals+100)
        before = (was.get("saved") or 0)*((was.get("deals") or 0)+100)
        grown = deals+max(0, deals-(was.get("deals") or 0))/hours_between*hours_left
        final = total+(total-before)/hours_between*hours_left
        out[name] = dict(row, deals=grown, saved=final/(grown+100), posts=(row.get("posts") or 0)+max(0, (row.get("posts") or 0)-(was.get("posts") or 0))/hours_between*hours_left)
    return out


def projected(rows, me, deals, total, posts):
    table = dict(rows)
    table[me] = dict(rows[me], deals=deals, saved=total/(deals+100), posts=posts)
    return score(table, me)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("--since", "--until", "--earlier-board", "--board", "--projection", "--before-since", "--before-until"):
        parser.add_argument(name, required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    start, end = at(args.since), at(args.until)
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    inventory = json.loads((ROOT / ".market/inventory.json").read_text(encoding="utf-8"))
    earlier, board = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.earlier_board, args.board))
    plan = json.loads(Path(args.projection).read_text(encoding="utf-8"))
    me = "kcwarni"
    now_row = student_rows(board)[me]
    left = plan["projection_assumptions"]["hours_left_to_the_round_end"]
    hours = plan["window"]["hours"]
    between = (Path(args.board).stat().st_mtime-Path(args.earlier_board).stat().st_mtime)/3600
    total = now_row["saved"]*(now_row["deals"]+100)
    base = plan["window"]["paid_per_hour"]
    worth = plan["window"]["mean_worth_after_faults"]
    rescued = plan["retry_inputs"]["rescued_per_hour_estimate"]
    rescued_worth = plan["retry_inputs"]["mean_worth_of_rescued_after_faults_unknown_grade"]
    posts = now_row.get("posts", 0)+6*left
    still = student_rows(board)
    going = moved(earlier, board, between, left)
    scenarios = {}
    for name, share in (("without_retry", 0), ("retry_half_the_estimate", .5), ("retry_full_estimate", 1)):
        deals = now_row["deals"]+(base+rescued*share)*left
        sum_saved = total+(base*worth+rescued*share*rescued_worth)*left
        scenarios[name] = {"deals_at_the_end": round(deals), "saved_at_the_end": round(sum_saved/(deals+100), 4), "student_buyer_rivals_held_still": projected(still, me, deals, sum_saved, posts), "student_buyer_rivals_at_their_pace": projected(going, me, deals, sum_saved, posts)}
    keep_deals = now_row["deals"]+base*left
    keep_sum = total+base*worth*left
    posts_step2 = {"posts_at_6_an_hour": projected(going, me, keep_deals, keep_sum, posts), "posts_at_12_an_hour": projected(going, me, keep_deals, keep_sum, now_row.get("posts", 0)+12*left)}
    table = dict(going)
    table[me] = dict(going[me], deals=keep_deals, saved=keep_sum/(keep_deals+100), posts=posts)
    def parts(rows):
        return {k: round(ra.percentile(ra.number(rows[me].get(k)), [ra.number(r.get(k)) for r in rows.values()]), 3) for k in ra.SAVED_WEIGHTS}
    rivals = [r for n, r in going.items() if n != me]
    report = {"window_kst": [args.since, args.until], "board_movement": board_movement(earlier, board, between), "percentiles_now_among_students": parts(still), "percentiles_at_the_end_rivals_at_their_pace_without_retry": parts(table), "posts_step2_rivals_at_their_pace": posts_step2,
              "agreed_but_not_paid": agreed_not_paid(start, end), "agreed_but_not_paid_before": agreed_not_paid(at(args.before_since), at(args.before_until)), "market_width": width(start, end), "market_width_before": width(at(args.before_since), at(args.before_until)), "expressions": expressions(start, end),
              "supply_vs_surge": supplies(catalog, inventory), "food_trial": food(end.timestamp(), inventory, catalog), "scenarios": scenarios,
              "rivals_at_their_pace": {"hours_between_boards": round(between, 2), "students": len(rivals), "median_deals_at_the_end": round(median(r["deals"] for r in rivals)), "median_saved_at_the_end": round(median(r["saved"] for r in rivals), 4)},
              "note": f"{hours} h window stretched over {left} h: a straight line through a small sample, not a forecast."}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
