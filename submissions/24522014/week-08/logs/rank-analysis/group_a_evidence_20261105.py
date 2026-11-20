"""Read-only: evidence for the price-reading switches. Replays saved seller replies through buyer_fast with each switch on. No network, no model, no settings read or changed.

Part 1 (--since): replies the group A reading would have read that today's parser did not, split by what actually happened then.
A deal counts once, at its first such reply, because with the switch on the rule would have acted there.
Part 2 (--since-deals): paid deals per expression, against the settled price. "We offered it" is checked against the requests really sent,
because older records have an empty decision trail. That gap is why an earlier count called these deals unexplained.
Run from week-08 with --out. Prints the same JSON, which holds shop asks and paid prices but no costs, floors or our own sale prices.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import buyer_fast as fast  # noqa: E402
from buyer import seller_message, seller_metadata  # noqa: E402
from buyer_counter import evaluate as evaluate_counter, load_settings  # noqa: E402

KST = timezone(timedelta(hours=9))
OFF = dict(fast.OFF)
A = {**OFF, "group_a": True}
ALL = {"group_a": True, "lowest_price": True, "colon_krw": True}
ALONE = {"my_price": A, "final_price": A, "metadata_ask": A, "lowest_price": {**OFF, "lowest_price": True}, "colon_krw": {**OFF, "colon_krw": True}}
GROUP_A = ("my_price", "final_price", "metadata_ask")
OLD_END = "20261105T060000"  # Where the earlier count stopped, by record name in UTC.


def load_payments():
    rows = {}
    for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows[row.get("order")] = row
    return rows


def deal_files(record):
    """Seller replies still open for input, each with the offer we sent after it, and every number we sent."""
    folder = ROOT / "logs/buyer" / Path(str(record["buyer_log_dir"]).replace("\\", "/")).name
    if not folder.exists():
        return None, None
    replies, sent = [], []
    for path in sorted(folder.glob("*.request.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("body")
        except ValueError:
            continue
        message = ((body or {}).get("params") or {}).get("message") if isinstance(body, dict) else None
        if message and message.get("taskId"):
            text = " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict)).strip()
            if text.isdigit():
                sent.append(int(text))
    for path in sorted(folder.glob("*.response.txt")):
        try:
            result = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("result") or {}
        except ValueError:
            continue
        task = result.get("task") if isinstance(result.get("task"), dict) else result
        status = (task or {}).get("status") if isinstance(task, dict) else None
        if status and status.get("state") == "TASK_STATE_INPUT_REQUIRED":
            replies.append((seller_message(task), seller_metadata(task)))
    return replies, sent


def records(begin, end=None):
    folder = ROOT / "logs/market-buyer"
    unreadable = 0
    for name in sorted(n for n in os.listdir(folder) if n[:15] >= begin and (end is None or n[:15] < end)):
        try:
            yield name, json.loads((folder / name).read_text(encoding="utf-8"))["record"]
        except (OSError, ValueError, KeyError):
            unreadable += 1
            print("unreadable record", name, file=sys.stderr)


def gate_or_model(step):
    if step is None:
        return "no_decision_at_this_turn"
    return {"model_gate": "stopped_at_gate", "model": "model_handled", "quote_filter": "quote_filter_refused", "deterministic": "rule_handled", "parser": "parser_stop", "time_limit": "time_limit", "turn_limit": "turn_limit"}.get(step.get("source"), str(step.get("source")))


def part_one(begin, end, pay, catalog, reading=A):
    population = Counter()
    replies_table = defaultdict(Counter)
    deals = []
    refused = Counter()
    consistency = Counter()
    rule_offer = Counter()
    for name, record in records(begin, end):
        population["orders"] += 1
        if not record.get("buyer_log_dir"):
            population["no_trade_log"] += 1
            continue
        replies, sent = deal_files(record)
        if replies is None:
            population["trade_log_missing"] += 1
            continue
        population["trades_read"] += 1
        population["replies_open_for_input"] += len(replies)
        trail = {s.get("turn"): s for s in record.get("decision_trail") or [] if isinstance(s, dict)}
        payment = pay.get(record.get("order")) or {}
        first = trail.get(1)
        if first and first.get("reason_code") == "rule_offer":
            rule_offer["first_turn_rule_offers"] += 1
            rule_offer["paid"] += bool(payment.get("paid"))
        chosen = None
        for turn, (text, metadata) in enumerate(replies, 1):
            step = trail.get(turn)
            today = fast.read_ask(text, metadata, OFF)
            if step and step.get("parser_reason") in ("no_labeled_price", "conflicting_prices", "malformed_price", "non_current_price", "rule_offer", "over_budget", "rule_repeated_offer"):
                expected = today[1] if today[0] is None else ("rule_repeated_offer" if step.get("parser_reason") == "rule_repeated_offer" else None)
                consistency["checked"] += 1
                if expected and expected != step["parser_reason"]:
                    consistency["parser_disagrees_with_record"] += 1
            if today[1] != "no_labeled_price":
                continue
            price, code, how = fast.read_ask(text, metadata, reading)
            if code == "no_labeled_price":
                continue
            if code != "single_ask":
                refused[code] += 1
                continue
            what = gate_or_model(step)
            replies_table[how][what] += 1
            if chosen is None:
                last_offer = sent[turn-2] if turn > 1 and len(sent) >= turn-1 else None
                earlier = [trail[t] for t in range(1, turn) if t in trail]
                chosen = {"turn": turn, "how": how, "ask": price, "what": what, "reason": (step or {}).get("reason_code"), "last_offer": last_offer,
                          "rule_code": fast.classify(text, record.get("budget"), last_offer, metadata, reading)[1], "counter_already_used": any(s.get("reason_code") == "rule_counter_offer" for s in earlier),
                          "earlier_steps": [s.get("reason_code") for s in earlier]}
        if chosen is None:
            continue
        chosen.update(when=name[:15], shop=record.get("shop_name"), item=record.get("item"), cap=record.get("budget"), list_price=(catalog.get(record.get("item")) or {}).get("list_price"), turns=len(replies),
                      paid=bool(payment.get("paid")), paid_price=payment.get("price") if payment.get("paid") else None, payment_refused=bool(payment) and not payment.get("paid"), outcome=record.get("failure_reason") or record.get("outcome"), sent=sent)
        deals.append(chosen)
    return population, replies_table, deals, refused, consistency, rule_offer


def window_scale(begin, end, pay):
    scale = Counter()
    gate_reasons = Counter()
    counter_by_shop = defaultdict(Counter)
    for name, record in records(begin, end):
        trail = [s for s in record.get("decision_trail") or [] if isinstance(s, dict)]
        payment = pay.get(record.get("order")) or {}
        stops = [s for s in trail if s.get("source") == "model_gate"]
        if stops:
            scale["deals_stopped_at_gate"] += 1
            gate_reasons[str(stops[-1].get("reason_code"))] += 1
        if any(s.get("source") == "model" for s in trail):
            scale["deals_model_called"] += 1
            scale["deals_model_called_paid"] += bool(payment.get("paid"))
        if any(s.get("reason_code") == "rule_counter_offer" for s in trail):
            scale["deals_rule_counter"] += 1
            scale["deals_rule_counter_paid"] += bool(payment.get("paid"))
            counter_by_shop[record.get("shop_name")]["counters"] += 1
            counter_by_shop[record.get("shop_name")]["paid"] += bool(payment.get("paid"))
    return {**dict(scale), "gate_reasons": dict(gate_reasons), "rule_counter_by_shop_top8": {k: dict(v) for k, v in sorted(counter_by_shop.items(), key=lambda kv: -kv[1]["counters"])[:8]}}


def in_cap(deal):
    return 0 < deal["ask"] <= deal["cap"]


def surplus(deal, price):
    return deal["list_price"] - price if deal["list_price"] else 0


def counter_reply(deal, settings):
    counter, check = evaluate_counter(deal["ask"], deal["cap"], deal["list_price"], used=deal["counter_already_used"], last_offer=deal["last_offer"], config=settings)
    return counter is not None, (check or {}).get("reason_code")


def summarize_part_one(deals, hours, settings):
    report = {}
    by_how = lambda group: dict(Counter(d["how"] for d in group))
    # Stopped at the model gate.
    stopped = [d for d in deals if d["what"] == "stopped_at_gate"]
    within = [d for d in stopped if in_cap(d)]
    pairs = {}
    for deal in within:
        key = (deal["shop"], deal["item"])
        pairs[key] = max(pairs.get(key, 0), surplus(deal, deal["ask"]))
    shop_surplus = Counter()
    for deal in within:
        shop_surplus[deal["shop"]] += surplus(deal, deal["ask"])
    amounts = sorted((surplus(d, d["ask"]) for d in within), reverse=True)
    over = [d for d in stopped if not in_cap(d)]
    counters = Counter(counter_reply(d, settings)[1] for d in over)
    eligible = [d for d in over if counter_reply(d, settings)[0]]
    report["stopped_at_gate"] = {"deals": len(stopped), "gate_reasons": dict(Counter(d["reason"] for d in stopped)), "by_how": by_how(stopped), "first_read_turn": dict(Counter(d["turn"] for d in stopped)),
                                 "within_cap": {"deals": len(within), "deals_per_hour": round(len(within)/hours, 2), "by_how": by_how(within), "surplus_before_breakage_if_all_paid": sum(amounts),
                                                "surplus_by_how": {how: sum(surplus(d, d["ask"]) for d in within if d["how"] == how) for how in {d["how"] for d in within}},
                                                "top3_share": round(sum(amounts[:3])/sum(amounts), 3) if sum(amounts) else None, "distinct_shop_sku_pairs": len(pairs), "surplus_one_deal_per_pair": sum(pairs.values()),
                                                "shops": len(shop_surplus), "shops_by_surplus_top5": shop_surplus.most_common(5), "missing_list_price": sum(not d["list_price"] for d in within)},
                                 "above_cap": {"deals": len(over), "by_how": by_how(over), "counter_rule_results": dict(counters), "counter_rule_would_offer_the_cap": {"deals": len(eligible), "by_how": by_how(eligible), "shops": dict(Counter(d["shop"] for d in eligible)), "surplus_at_cap_if_all_accepted": sum(surplus(d, d["cap"]) for d in eligible), "distinct_shop_sku_pairs": len({(d["shop"], d["item"]) for d in eligible}), "ask_per_cap": sorted(round(d["ask"]/d["cap"], 3) for d in eligible)}}}
    # Handled by the model.
    handled = [d for d in deals if d["what"] == "model_handled"]
    paid = [d for d in handled if d["paid"]]
    table = {}
    for how in sorted({d["how"] for d in paid}):
        group = [d for d in paid if d["how"] == how]
        cap_ok = [d for d in group if in_cap(d)]
        gaps = [d["ask"]-d["paid_price"] for d in cap_ok]
        table[how] = {"paid": len(group), "ask_within_cap": len(cap_ok), "ask_above_cap": len(group)-len(cap_ok), "paid_equal_to_ask": sum(g == 0 for g in gaps), "paid_below_ask": sum(g > 0 for g in gaps), "paid_above_ask": sum(g < 0 for g in gaps),
                      "extra_cost_if_rule_took_the_ask": sum(gaps), "extra_cost_per_hour": round(sum(gaps)/hours), "extra_cost_share_of_paid": round(sum(gaps)/sum(d["paid_price"] for d in cap_ok), 4) if cap_ok else None,
                      "paid_price_sum": sum(d["paid_price"] for d in cap_ok), "shops": dict(Counter(d["shop"] for d in group))}
    unpaid = [d for d in handled if not d["paid"]]
    unpaid_within = [d for d in unpaid if in_cap(d)]
    report["model_handled"] = {"deals": len(handled), "by_how": by_how(handled), "first_read_turn": dict(Counter(d["turn"] for d in handled)), "paid_by_expression": table,
                               "not_paid": {"deals": len(unpaid), "within_cap": len(unpaid_within), "by_how": by_how(unpaid_within), "surplus_before_breakage_if_all_paid": sum(surplus(d, d["ask"]) for d in unpaid_within), "payment_refused": sum(d["payment_refused"] for d in unpaid), "outcomes": dict(Counter(str(d["outcome"]) for d in unpaid_within).most_common(8))}}
    others = [d for d in deals if d["what"] not in ("stopped_at_gate", "model_handled")]
    report["other"] = {"deals": len(others), "what": dict(Counter(d["what"] for d in others)), "reasons": dict(Counter(str(d["reason"]) for d in others)), "by_how": by_how(others), "paid": sum(d["paid"] for d in others)}
    return report


def deal_rows(begin, end, pay, reading):
    """Every paid deal with its asks under one reading."""
    rows = {}
    for name, record in records(begin, end):
        payment = pay.get(record.get("order")) or {}
        if not payment.get("paid") or not record.get("buyer_log_dir"):
            continue
        replies, sent = deal_files(record)
        if replies is None:
            continue
        asks = defaultdict(list)
        for text, metadata in replies:
            price, code, how = fast.read_ask(text, metadata, reading)
            if how and code == "single_ask":
                asks[how].append(price)
        rows[name] = {"when": name[:15], "shop": record.get("shop_name"), "item": record.get("item"), "settled": payment.get("price"), "cap": record.get("budget"), "asks": dict(asks), "sent": sent, "trail_empty": not record.get("decision_trail"),
                      "trail_prices": [s.get("price") for s in record.get("decision_trail") or [] if isinstance(s, dict) and s.get("price")]}
    return rows


def judge(row, how, use_requests=True):
    """Against the last ask this expression stated, as the earlier count did. The first ask is kept apart because the rule would act on it."""
    last, settled = row["asks"][how][-1], row["settled"]
    offers = row["sent"] if use_requests else row["trail_prices"]
    if last == settled:
        return "ask_equals_settled"
    if settled in offers and last > settled:
        return "closed_at_our_lower_offer"
    return "read_below_settled_unexplained" if last < settled else "read_above_settled_unexplained"


def tally(rows, how, use_requests=True):
    picked = [r for r in rows.values() if how in r["asks"]]
    table = Counter(judge(r, how, use_requests) for r in picked)
    ratio = sorted(round(r["asks"][how][0]/r["settled"], 3) for r in picked if r["settled"])
    within = [r for r in picked if r["asks"][how][0] <= (r["cap"] or 0)]
    extra = sum(r["asks"][how][0]-r["settled"] for r in within)
    return {"deals": len(picked), **dict(table), "first_ask_within_cap": len(within), "first_ask_above_cap": len(picked)-len(within), "extra_if_rule_took_first_ask_within_cap": extra, "extra_share_of_those_paid": round(extra/sum(r["settled"] for r in within), 4) if within else None, "first_ask_equals_settled": sum(r["asks"][how][0] == r["settled"] for r in picked), "first_ask_over_settled_median": ratio[len(ratio)//2] if ratio else None, "shops": dict(Counter(r["shop"] for r in picked).most_common(6))}


def part_two(pay, begin, end):
    expressions = {}
    for how in ALONE:
        rows = deal_rows(begin, None, pay, ALONE[how])
        older = {k: r for k, r in rows.items() if k[:15] < OLD_END}
        newer = {k: r for k, r in rows.items() if k[:15] >= OLD_END}
        expressions[how] = {"all": tally(rows, how), "up_to_earlier_count": tally(older, how), "after_earlier_count": tally(newer, how)}
        if how in ("lowest_price", "colon_krw"):
            expressions[how]["unexplained_or_negotiated_rows"] = [{"when": r["when"], "shop": r["shop"], "item": r["item"], "first_ask": r["asks"][how][0], "last_ask": r["asks"][how][-1], "settled": r["settled"], "sent": r["sent"], "trail_empty": r["trail_empty"], "verdict": judge(r, how)} for r in rows.values() if how in r["asks"] and judge(r, how) != "ask_equals_settled"]
    # The earlier count's own method, as a check that this script reads the same deals.
    together = deal_rows(begin, OLD_END, pay, ALL)
    reproduced = {}
    for how in ("metadata_ask", "my_price", "final_price", "lowest_price", "colon_krw"):
        reproduced[how] = dict(Counter(judge(r, how, use_requests=False) for r in together.values() if how in r["asks"]))
    my = deal_rows(begin, OLD_END, pay, A)
    twelve = []
    for r in my.values():
        if "my_price" in r["asks"] and r["asks"]["my_price"][-1] != r["settled"]:
            asks = r["asks"]["my_price"]
            twelve.append({"when": r["when"], "shop": r["shop"], "item": r["item"], "asks_in_order": asks, "settled": r["settled"], "numbers_we_sent": r["sent"], "trail_empty": r["trail_empty"], "settled_is_our_last_number": bool(r["sent"]) and r["sent"][-1] == r["settled"], "first_ask_minus_settled": asks[0]-r["settled"], "ask_moved_inside_deal": len(set(asks)) > 1})
    return expressions, reproduced, twelve


def offered_the_ask(begin, end, pay):
    """Each time we sent exactly the figure this expression had just stated: did the deal end paid? Runs over paid and unpaid deals alike."""
    table = defaultdict(Counter)
    same_metadata = Counter()
    for name, record in records(begin, end):
        if not record.get("buyer_log_dir"):
            continue
        replies, sent = deal_files(record)
        if replies is None:
            continue
        payment = pay.get(record.get("order")) or {}
        for turn, (text, metadata) in enumerate(replies, 1):
            price, code, how = fast.read_ask(text, metadata, ALL)
            if not how or code != "single_ask":
                continue
            if how == "my_price":
                same_metadata["my_price_replies"] += 1
                same_metadata["with_equal_metadata_ask"] += fast.structured_ask(metadata) == price
            if len(sent) >= turn and sent[turn-1] == price:
                table[how]["times_we_sent_the_stated_figure"] += 1
                table[how]["deal_paid_at_it"] += bool(payment.get("paid")) and payment.get("price") == price
                if not (payment.get("paid") and payment.get("price") == price):
                    table[how]["not_paid_at_it"] += 1
    return {how: dict(c) for how, c in table.items()}, dict(same_metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="20261105T000000", help="UTC, as in record names; default 11-05 09:00 KST")
    parser.add_argument("--until", required=True, help="UTC, as in record names")
    parser.add_argument("--since-deals", default="20261103T120000", help="UTC; where the earlier count began")
    parser.add_argument("--out")
    args = parser.parse_args()
    pay = load_payments()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    settings = load_settings()
    hours = (datetime.strptime(args.until, "%Y%m%dT%H%M%S")-datetime.strptime(args.since, "%Y%m%dT%H%M%S")).total_seconds()/3600
    population, replies, deals, refused, consistency, rule_offer = part_one(args.since, args.until, pay, catalog)
    others = {}
    for label, reading in (("lowest_price_alone", ALONE["lowest_price"]), ("colon_krw_alone", ALONE["colon_krw"])):
        _, seen, found, not_read, _, _ = part_one(args.since, args.until, pay, catalog, reading)
        others[label] = {"replies": {how: dict(c) for how, c in seen.items()}, "refused": dict(not_read), "deals": len(found), **summarize_part_one(found, hours, settings)}
    expressions, reproduced, twelve = part_two(pay, args.since_deals, args.until)
    offered, same_metadata = offered_the_ask(args.since_deals, args.until, pay)
    report = {"part_one": {"window_utc": [args.since, args.until], "hours": round(hours, 2), "population": dict(population), "parser_replay_vs_records": dict(consistency),
                           "replies_group_a_would_read": {how: dict(c) for how, c in replies.items()}, "replies_group_a_saw_but_refused": dict(refused), "deals_group_a_would_read": len(deals),
                           **summarize_part_one(deals, hours, settings), "measured_rule_offer_to_paid_same_window": dict(rule_offer), "window_scale": window_scale(args.since, args.until, pay), "same_replay_for_group_b_switches": others},
              "part_two": {"window_utc_from": args.since_deals, "earlier_count_reproduced_by_its_method": reproduced, "expressions": expressions, "when_we_sent_the_stated_figure": offered, "my_price_and_metadata": same_metadata},
              "my_price_twelve": twelve, "deals": deals}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "deals"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
