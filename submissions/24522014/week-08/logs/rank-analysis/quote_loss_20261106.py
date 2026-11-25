"""Read-only: orders lost to a stated shelf life or grade, and to a shop refusing at the first message. What could another shop or a check before selection save? No network, no model, no costs.

Population: every market_buyer run file since --since (KST), with its own selection evidence, and the shop replies in logs/buyer. Rescue figures are estimates, marked as such:
- another candidate = eligible at selection, other than the one tried. Its discount is what the floor ceiling or its last known price would give (a known price above the ceiling does not count).
- rescued deals = lost orders with another candidate x the share of tries that got paid in this window. This assumes the second try does as well as a first.
- a check before selection can only use what we saw earlier: shops.json names no grade or date, so the predictor here is the same shop and SKU's previous quote within --memory-hours.
Run from week-08 with --since --until --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import discount_floor as floor  # noqa: E402
from settlement_memory import payment_outcomes  # noqa: E402

KST = timezone(timedelta(hours=9))
FILTERS = ("quote_shelf_life_short", "quote_grade_unacceptable")
GRADES = ["A", "B"]


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def stamp_of(name):
    return datetime.strptime(name[:21], "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)


def runs(start, end):
    first = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for path in sorted((ROOT / "logs/market-buyer").glob("*.json")):
        if path.name[:15] < first:
            continue
        moment = stamp_of(path.name)
        if moment >= end:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        yield moment, data


def stop_of(record):
    trail = record.get("decision_trail") or []
    return trail[-1].get("reason_code") if trail else (record.get("failure_reason") or record.get("outcome"))


def seconds_taken(record, moment):
    name = Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    try:
        return (moment-stamp_of(name)).total_seconds()
    except ValueError:
        return None


def alternatives(data, record, kind, settings):
    """Other candidates and the discount each would likely give under the floor."""
    found = []
    for row in data.get("selection_evidence") or []:
        if not row.get("support_eligible") or (row["shop"] == record.get("shop_name") and row["sku"] == record.get("item")):
            continue
        value = row["catalogue_value"]
        covered = bool(row.get("warranty_required"))
        top = floor.ceiling(value, floor.required(kind, None, covered, SETTINGS, GRADES))
        price = row.get("past_settled_price")
        if price is not None and price > top:
            continue
        found.append((1-(price if price is not None else top)/value, price is not None, row["shop"] != record.get("shop_name")))
    return found


def refusal_text(record):
    folder = ROOT / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    for path in sorted(folder.glob("*.response.txt")):
        try:
            reply = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        result = reply.get("result") if isinstance(reply, dict) else None
        task = result.get("task", result) if isinstance(result, dict) else None
        if isinstance(task, dict) and isinstance(task.get("status"), dict) and task["status"].get("state"):
            message = task["status"].get("message") or {}
            return " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))
    return ""


def shape(text, skus):
    text = re.sub(r"G[0-9a-f]{8}", "<sku>", text)
    text = re.sub(r"\d[\d,.]*", "#", text)
    return text[:90] or "(no text)"


def group_of(text):
    low = text.casefold()
    for name, words in (("no stock or sold out", ("in stock", "no matching", "matching stock", "out of stock", "sold out", "no stock", "don't have", "do not have", "not available", "unavailable", "품절", "재고", "none left", "no units", "not in stock")),
                        ("certificate or warranty asked", ("certificate", "warranty", "cert", "보증", "인증")),
                        ("busy or limit", ("busy", "rate", "too many", "limit", "try again", "later")),
                        ("not sold here or not understood", ("not sell", "don't sell", "do not sell", "unknown", "understand", "no such", "not found", "don't carry", "do not carry")),
                        ("budget or price", ("budget", "price", "offer"))):
        if any(word in low for word in words):
            return name
    return "other" if text else "no text"


def summary(values):
    return {"count": len(values), "median": round(median(values), 4) if values else None, "mean": round(sum(values)/len(values), 4) if values else None}


def predictor(all_quotes, hours):
    """The previous quote of the same pair says the unit would fail this order's terms. Food shelf life is aged by the time passed."""
    table = Counter()
    last = {}
    for moment, pair, check, paid in all_quotes:
        previous = last.get(pair)
        real = check.get("reason_code") in FILTERS
        if previous and 0 <= (moment-previous[0]).total_seconds() <= hours*3600:
            old = previous[1]
            required = check.get("min_hours_left")
            elapsed = (moment-previous[0]).total_seconds()/3600
            short = required is not None and old.get("hours_left") is not None and old["hours_left"]-elapsed < required
            allowed = check.get("accept_grades")
            bad = bool(allowed) and old.get("grade") is not None and old["grade"] not in allowed
            said = short or bad
            table["with_a_previous_quote"] += 1
            table["refused_with_a_previous_quote"] += real
            table["predicted_refused"] += said
            table["predicted_and_refused"] += said and real
            table["predicted_but_passed_would_lose_the_try"] += said and not real
            table["predicted_but_paid_would_lose_the_deal"] += said and not real and paid
        last[pair] = (moment, check)
    table["all_quotes"] = len(all_quotes)
    table["refused_all"] = sum(c.get("reason_code") in FILTERS for _, _, c, _ in all_quotes)
    return dict(table)


def main():
    global SETTINGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--memory-hours", type=float, default=1)
    parser.add_argument("--out")
    args = parser.parse_args()
    SETTINGS = floor.load()
    start, end = at(args.since), at(args.until)
    payments = payment_outcomes(ROOT / ".market/payments.jsonl")
    stops = Counter()
    lost = {name: {"rows": 0, "with_other": 0, "with_other_shop": 0, "by_kind": Counter(), "best_discount": [], "known_price_best": 0, "seconds": []} for name in (*FILTERS, "seller_rejected_at_open")}
    tried = paid_tried = 0
    all_quotes = []
    texts, groups, shape_count = Counter(), Counter(), Counter()
    by_qty, by_shop, by_warranty = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    durations = defaultdict(list)
    for moment, data in runs(start, end):
        record = data["record"]
        if not record.get("shop_name"):
            stops[record.get("failure_reason") or "no_shop"] += 1
            continue
        stop = stop_of(record)
        stops[stop] += 1
        payment = payments.get(record.get("order")) or {}
        paid = bool(payment.get("paid"))
        tried += 1
        paid_tried += paid
        taken = seconds_taken(record, moment)
        if taken is not None:
            durations["paid" if paid else stop].append(taken)
        trail = record.get("decision_trail") or []
        if trail and trail[0].get("quote_check"):
            all_quotes.append((moment, (record["shop_name"], record["item"]), trail[0]["quote_check"], paid))
        kind = (record.get("accept_conditions") or {}).get("min_hours_left") is not None
        kind = "food" if kind else "goods"
        refused_open = record.get("refused_at_open")
        key = "seller_rejected_at_open" if refused_open else stop if stop in FILTERS else None
        qty = record.get("selected_offer_qty")
        band = "unknown" if type(qty) not in (int, float) else "1" if qty < 2 else "2-3" if qty < 4 else "4+"
        by_qty[band]["tried"] += 1
        by_qty[band]["refused_at_open"] += bool(refused_open)
        wants = bool((record.get("accept_conditions") or {}).get("warranty_hours"))
        by_warranty["asked_for_warranty" if wants else "no_warranty_required"]["tried"] += 1
        by_warranty["asked_for_warranty" if wants else "no_warranty_required"]["refused_at_open"] += bool(refused_open)
        by_shop[record["shop_name"]]["tried"] += 1
        by_shop[record["shop_name"]]["refused_at_open"] += bool(refused_open)
        if key:
            group = lost[key]
            group["rows"] += 1
            group["by_kind"][kind] += 1
            others = alternatives(data, record, kind, SETTINGS)
            if others:
                group["with_other"] += 1
                group["with_other_shop"] += any(o[2] for o in others)
                best = max(others, key=lambda o: o[0])
                group["best_discount"].append(best[0])
                group["known_price_best"] += best[1]
            if taken is not None:
                group["seconds"].append(taken)
        if refused_open:
            text = refusal_text(record)
            groups[group_of(text)] += 1
            shape_count[shape(text, ())] += 1
    paid_share = paid_tried/tried if tried else None
    report = {"kst": [start.isoformat(), end.isoformat()], "runs_with_a_shop": tried, "paid": paid_tried, "paid_per_try": round(paid_share, 4) if paid_share is not None else None, "stops": dict(stops.most_common()),
              "seconds_per_hook_by_ending": {k: summary(v) for k, v in sorted(durations.items()) if len(v) >= 5}, "lost": {},
              "previous_quote_predictor": {"memory_hours": args.memory_hours, **predictor(all_quotes, args.memory_hours)}}
    for name, group in lost.items():
        discounts = group["best_discount"]
        report["lost"][name] = {"orders": group["rows"], "by_kind": dict(group["by_kind"]), "with_another_candidate": group["with_other"], "with_another_shop": group["with_other_shop"],
                                "best_other_discount_under_the_floor": summary(discounts), "best_other_has_a_known_price": group["known_price_best"], "seconds_lost_median": round(median(group["seconds"]), 2) if group["seconds"] else None,
                                "estimated_deals_rescued": round(group["with_other"]*paid_share, 1) if paid_share is not None else None, "estimated_note": "with_another_candidate x paid_per_try; an estimate"}
    report["refused_at_open"] = {"by_text_group": dict(groups.most_common()), "top_text_shapes": shape_count.most_common(12),
                                 "by_listed_qty": {k: {**v, "share": round(v["refused_at_open"]/v["tried"], 3)} for k, v in sorted(by_qty.items())},
                                 "by_warranty_request": {k: {**v, "share": round(v["refused_at_open"]/v["tried"], 3)} for k, v in by_warranty.items()},
                                 "shops_most_refusing": sorted(({"shop": k, **v} for k, v in by_shop.items() if v["refused_at_open"]), key=lambda r: -r["refused_at_open"])[:12], "shops_refusing": sum(1 for v in by_shop.values() if v["refused_at_open"]), "shops_tried": len(by_shop)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
