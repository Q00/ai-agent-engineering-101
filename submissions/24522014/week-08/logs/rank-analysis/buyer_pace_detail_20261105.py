"""Read-only, for the 17:00 KST check of the ten-second interval: is a change in pace the interval or the hour? No network, no model.

Windows: after the change, the saved baseline, the part of that baseline with today's other settings, and yesterday's same hours.
Evidence kept apart: attempts a twenty-second rule would have blocked vs the rest, time the loop spent in trades, and the public ledger as a market-wide control.
Run from week-08 with --out; prints the same JSON. Holds no costs, floors or caps.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from harness_usage import load_calls, summarize_calls  # noqa: E402

KST = timezone(timedelta(hours=9))
CHANGE = datetime(2026, 11, 5, 14, 57, 53, tzinfo=KST)
END = datetime(2026, 11, 5, 17, 0, 0, tzinfo=KST)
WINDOWS = {
    "after_10s": (CHANGE, END),
    "baseline_0900_1456": (datetime(2026, 11, 5, 9, 0, tzinfo=KST), datetime(2026, 11, 5, 14, 56, 36, tzinfo=KST)),
    "same_settings_1121_1456": (datetime(2026, 11, 5, 11, 21, tzinfo=KST), datetime(2026, 11, 5, 14, 56, 36, tzinfo=KST)),
    "yesterday_1500_1700": (datetime(2026, 11, 4, 15, 0, tzinfo=KST), datetime(2026, 11, 4, 17, 0, tzinfo=KST)),
}


def wilson(hits, total, z=1.96):
    if not total:
        return None
    p = hits / total
    base = 1 + z*z/total
    mid = (p + z*z/(2*total)) / base
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / base
    return [round(mid-half, 4), round(mid+half, 4)]


def when(name):
    return datetime.strptime(name[:21], "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)


def load_records():
    rows, unreadable = [], []
    folder = ROOT / "logs/market-buyer"
    first = (datetime(2026, 11, 4, 0, 0, tzinfo=KST)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for name in sorted(n for n in os.listdir(folder) if n[:15] >= first):
        try:
            rows.append((when(name), json.loads((folder / name).read_text(encoding="utf-8"))["record"]))
        except (OSError, ValueError, KeyError):
            unreadable.append(name)
    if unreadable:
        print(f"unreadable records: {len(unreadable)}", file=sys.stderr)
    return rows


def load_payments():
    found = {}
    for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        found[row.get("order")] = row
    return found


def steps(record):
    return [s for s in record.get("decision_trail") or [] if isinstance(s, dict)]


def last_stop(record):
    trail = steps(record)
    return str(trail[-1].get("reason_code")) if trail else str(record.get("failure_reason") or record.get("outcome"))


def trade_seconds(at, record):
    folder = Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    if not folder:
        return None
    try:
        return (at - datetime.strptime(folder.split("-")[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)).total_seconds()
    except ValueError:
        return None


def summarize(rows, start, end, payments, catalog):
    picked = [(at, r) for at, r in rows if start <= at < end]
    hours = (end - start).total_seconds() / 3600
    stops, any_step = Counter(), Counter()
    attempted = [(at, r) for at, r in picked if r.get("selection_reason") != "attempt_cooldown"]
    surpluses, shops_paid, categories, spans = [], Counter(), Counter(), []
    refused = 0
    for at, record in picked:
        stops[last_stop(record)] += 1
        marks = set()
        for step in steps(record):
            for key in ("reason_code", "parser_reason"):
                if step.get(key):
                    marks.add(str(step[key]))
        any_step.update(marks)
        span = trade_seconds(at, record)
        if span is not None:
            spans.append(span)
        payment = payments.get(record.get("order"))
        if payment:
            if payment.get("paid"):
                value = (catalog.get(payment.get("item")) or {}).get("list_price")
                surpluses.append(value - payment["price"] if value and type(payment.get("price")) in (int, float) else 0)
                shops_paid[record.get("shop_name")] += 1
                categories[record.get("category")] += 1
            else:
                refused += 1
    paid = len(surpluses)
    ordered = sorted(surpluses, reverse=True)
    return {"hours": round(hours, 2), "orders": len(picked), "orders_per_hour": round(len(picked)/hours, 1), "attempts": len(attempted), "attempts_per_hour": round(len(attempted)/hours, 1),
            "paid": paid, "paid_per_hour": round(paid/hours, 1), "paid_per_attempt": round(paid/len(attempted), 4) if attempted else None, "paid_per_attempt_95": wilson(paid, len(attempted)),
            "paid_per_order": round(paid/len(picked), 4) if picked else None,
            "surplus": sum(surpluses), "surplus_per_hour": round(sum(surpluses)/hours), "surplus_per_deal_median": median(surpluses) if surpluses else None, "surplus_top3_share": round(sum(ordered[:3])/sum(ordered), 3) if sum(ordered) else None,
            "payment_refused": refused, "last_stop": dict(stops.most_common()), "any_step_reasons": dict(any_step.most_common()),
            "trades_timed": len(spans), "trade_seconds_median": round(median(spans), 2) if spans else None, "trade_seconds_sum_share_of_window": round(sum(spans)/(hours*3600), 3) if hours else None,
            "shops_attempted": len({r.get("shop_name") for _, r in attempted if r.get("shop_name")}), "shops_paid": len(shops_paid), "paid_by_shop_top": shops_paid.most_common(6), "paid_by_category_top": categories.most_common(6)}


def hourly(rows, day, payments, catalog, first=0, last=24):
    table = {}
    for hour in range(first, last):
        start = datetime(day.year, day.month, day.day, hour, tzinfo=KST)
        s = summarize(rows, start, start + timedelta(hours=1), payments, catalog)
        table[f"{hour:02d}"] = {k: s[k] for k in ("orders", "attempts", "paid", "paid_per_attempt", "surplus")} | {"model_quota_hour": s["last_stop"].get("model_quota_hour", 0), "seller_rejected": s["last_stop"].get("seller_rejected", 0)}
    return table


def twenty_second_split(rows, start, end, payments):
    """Walk the attempts by their recorded start; one under twenty seconds after the last one kept is what the old rule would have blocked. It cannot show what the extra attempts displaced through pair cooldowns."""
    state = json.loads((ROOT / ".market/buyer-support-state.json").read_text(encoding="utf-8"))
    started = {a["order"]: a["at"] for a in state["attempts"]}
    picked = sorted(((started[r["order"]], r) for at, r in rows if start <= at < end and r.get("selection_reason") != "attempt_cooldown" and r.get("order") in started), key=lambda p: p[0])
    groups = {"would_run_at_20s": Counter(), "blocked_at_20s": Counter()}
    surplus = Counter()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    kept = None
    for at, record in picked:
        name = "would_run_at_20s" if kept is None or at-kept >= 20 else "blocked_at_20s"
        if name == "would_run_at_20s":
            kept = at
        groups[name]["attempts"] += 1
        payment = payments.get(record.get("order")) or {}
        if payment.get("paid"):
            groups[name]["paid"] += 1
            value = (catalog.get(payment.get("item")) or {}).get("list_price")
            surplus[name] += value - payment["price"] if value else 0
    return {name: {"attempts": c["attempts"], "paid": c["paid"], "paid_per_attempt": round(c["paid"]/c["attempts"], 4) if c["attempts"] else None, "paid_per_attempt_95": wilson(c["paid"], c["attempts"]), "surplus": surplus[name]} for name, c in groups.items()}


def ledger(windows):
    """Other buyers' settlements in the public sample, with how much of each window the sample actually covers."""
    market = json.loads((ROOT / ".market/market-intel.json").read_text(encoding="utf-8"))
    me = {"kcwarni", "24522014"}
    rows = [r for r in market["rows"] if r.get("kind") == "settle" and type(r.get("ts")) in (int, float)]
    stamps = sorted(r["ts"] for r in rows)
    report = {"sample_rows": len(market["rows"]), "catching_up": market.get("catching_up"), "bounded_sample": market.get("bounded_sample"), "skipped_ranges": len(market.get("skipped_seq_ranges") or []),
              "sample_from_kst": datetime.fromtimestamp(stamps[0], KST).isoformat(), "sample_to_kst": datetime.fromtimestamp(stamps[-1], KST).isoformat()}
    seen = defaultdict(dict)
    for name, (start, end) in windows.items():
        inside = [r for r in rows if start.timestamp() <= r["ts"] < end.timestamp()]
        hours = (end-start).total_seconds()/3600
        others = [r for r in inside if str((r.get("data") or {}).get("buyer")) not in me]
        minutes = len({int(r["ts"]//300) for r in inside})
        seen[name] = {"settle_rows": len(inside), "others": len(others), "others_per_hour_over_window": round(len(others)/hours, 1), "others_per_covered_hour": round(len(others)/(minutes/12), 1) if minutes else None, "five_minute_slots_with_rows": minutes, "five_minute_slots_in_window": int(hours*12), "buyers": len({(r.get("data") or {}).get("buyer") for r in others}), "shops": len({(r.get("data") or {}).get("shop") for r in others})}
    report["windows"] = dict(seen)
    return report


def gate(rows):
    state = json.loads((ROOT / ".market/buyer-support-state.json").read_text(encoding="utf-8"))
    reserved = sorted(r["at"] for r in state.get("model_orders", []))
    by_day = defaultdict(list)
    for at in reserved:
        by_day[datetime.fromtimestamp(at, timezone.utc).date().isoformat()].append(at)
    days = {}
    for day, times in sorted(by_day.items()):
        days[day] = {"reserved": len(times), "first_kst": datetime.fromtimestamp(times[0], KST).strftime("%m-%d %H:%M:%S"), "last_kst": datetime.fromtimestamp(times[-1], KST).strftime("%m-%d %H:%M:%S"),
                     "twenty_fourth_kst": datetime.fromtimestamp(times[23], KST).strftime("%m-%d %H:%M:%S") if len(times) >= 24 else None}
    refused = defaultdict(Counter)
    first_day_stop = {}
    for at, record in rows:
        for step in steps(record):
            code = str(step.get("reason_code"))
            if code.startswith("model_quota") or code.startswith("model_breaker") or code == "model_gate_lock_busy":
                refused[at.date().isoformat()][code] += 1
                if code == "model_quota_day":
                    first_day_stop.setdefault(at.date().isoformat(), at.astimezone(KST).strftime("%m-%d %H:%M:%S"))
    window = {name: sum(start.timestamp() <= at < end.timestamp() for at in reserved) for name, (start, end) in WINDOWS.items()}
    return {"reserved_by_utc_day": days, "gate_refusals_by_utc_day": {k: dict(v) for k, v in sorted(refused.items())}, "first_model_quota_day_refusal_kst": first_day_stop, "reserved_in_windows": window,
            "retention_note": "model_orders keeps two days, so the oldest day may be incomplete", "ungated_rows": len(state.get("ungated_model_orders", []))}


def models():
    report = {}
    for name, (start, end) in WINDOWS.items():
        since, until = (moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S") for moment in (start, end))
        calls = load_calls(ROOT, since, until)
        report[name] = {"calls_by_provider": dict(Counter(c.get("provider") for c in calls)), "by_role_and_model": {k: {f: v[f] for f in ("provider", "calls", "failed", "timeouts", "usage_limit_errors", "tokens_sum", "elapsed_median")} for k, v in summarize_calls(calls).items()}}
    since = datetime(2026, 11, 5, 9, 0, tzinfo=KST).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    calls = load_calls(ROOT, since, END.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    report["utc_day_so_far"] = {"calls_by_provider": dict(Counter(c.get("provider") for c in calls)), "by_role_and_model": {k: {f: v[f] for f in ("provider", "calls", "failed", "timeouts", "usage_limit_errors", "tokens_sum")} for k, v in summarize_calls(calls).items()}}
    router = json.loads((ROOT / ".market/model-router.json").read_text(encoding="utf-8"))
    breaker = json.loads((ROOT / ".market/model-breaker.json").read_text(encoding="utf-8"))
    report["argon_state"] = {"agy_on": router.get("agy_on"), "agy_reason": router.get("agy_reason"), "quota": router.get("quota"), "read_kst": datetime.fromtimestamp((router.get("quota") or {}).get("read_at", 0), KST).isoformat()}
    usage = breaker.get("usage") or {}
    report["codex_state"] = {"model_on": breaker.get("model_on"), "reason": breaker.get("reason"), "weekly_percent": usage.get("weekly_percent"), "five_hour_percent": usage.get("five_hour_percent"), "read_kst": datetime.fromtimestamp(usage.get("read_at", 0), KST).isoformat()}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    rows = load_records()
    payments = load_payments()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    report = {"windows": {name: summarize(rows, start, end, payments, catalog) for name, (start, end) in WINDOWS.items()},
              "hourly_kst_1105": hourly(rows, CHANGE, payments, catalog, 9, 17), "hourly_kst_1104": hourly(rows, datetime(2026, 11, 4, tzinfo=KST), payments, catalog, 9, 24),
              "split_by_twenty_second_rule": twenty_second_split(rows, CHANGE, END, payments), "public_ledger": ledger(WINDOWS), "model_gate": gate(rows), "models": models()}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
