"""Read-only: buyer pace in a time window, to compare before and after a change. No network and no model.

Per hour: orders, attempts, paid deals and surplus before breakage, with every order's last stopping point. Also the failures that would
reverse a change: payments refused, transport and agent errors, and from the kit log timeouts and hooks that printed no result.
There is no 429 figure. The kit waits out a market 429 on /api/orders without logging it, so it cannot be counted here; a falling orders_per_hour is the only sign of one.
A shop's 429 to our buyer ends as transport_error in the stops.
Payments carry no time, so they are tied to a window through the order id. Kit log lines carry only a clock time and are read by their KST date.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
STAMP = re.compile(r"^(\d\d):(\d\d):(\d\d) ")
NO_429 = "not measured: the kit waits out a market 429 on orders without logging it; watch orders_per_hour, and transport_error for shops"
KIT_PATTERNS = {"timeout": re.compile(r"timed? ?out|timeout", re.I), "hook_no_result": re.compile(r"market_buyer printed no result"),
                "hook_traceback": re.compile(r"Traceback \(most recent call last\)")}


def parse_time(text):
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def payments(path):
    found = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            found[row.get("order")] = row
    return found


def last_stop(record):
    trail = [s for s in record.get("decision_trail") or [] if isinstance(s, dict)]
    if trail:
        return str(trail[-1].get("reason_code"))
    return str(record.get("failure_reason") or record.get("outcome"))


def kit_counts(root, start, end):
    """Counts from the current kit log, walking back over midnight from its last line. Lines only from the window count."""
    counts = Counter()
    lines = []
    path = root / "logs/market-kit.log"
    if not path.exists():
        return dict(counts)
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    day = datetime.fromtimestamp(path.stat().st_mtime, KST).replace(hour=0, minute=0, second=0, microsecond=0)
    previous = None
    for line in reversed(lines):
        match = STAMP.match(line)
        if not match:
            continue
        seconds = int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3])
        if previous is not None and seconds > previous:
            day -= timedelta(days=1)
        previous = seconds
        moment = day + timedelta(seconds=seconds)
        if moment < start:
            break
        if moment <= end:
            for name, pattern in KIT_PATTERNS.items():
                counts[name] += bool(pattern.search(line))
    return dict(counts)


def measure(root, start, end):
    root = Path(root)
    pay = payments(root / ".market/payments.jsonl")
    catalog = json.loads((root / ".market/catalog.json").read_text(encoding="utf-8"))
    begin, stop = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S"), end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    folder = root / "logs/market-buyer"
    stops, refused = Counter(), Counter()
    orders = attempts = paid = 0
    surplus = 0
    gaps, last = [], None
    for name in sorted(n for n in os.listdir(folder) if begin <= n[:15] < stop):
        try:
            record = json.loads((folder / name).read_text(encoding="utf-8"))["record"]
        except (OSError, ValueError, KeyError):
            stops["unreadable"] += 1
            continue
        orders += 1
        attempts += record.get("selection_reason") != "attempt_cooldown"
        stops[last_stop(record)] += 1
        at = datetime.strptime(name[:15], "%Y%m%dT%H%M%S")
        if last is not None:
            gaps.append((at - last).total_seconds())
        last = at
        payment = pay.get(record.get("order"))
        if payment:
            if payment.get("paid"):
                paid += 1
                value = (catalog.get(payment.get("item")) or {}).get("list_price")
                if value and type(payment.get("price")) in (int, float):
                    surplus += value - payment["price"]
            else:
                refused[re.sub(r"\d+", "#", str(payment.get("reason")))[:60]] += 1
    hours = (end - start).total_seconds() / 3600
    per_hour = lambda n: round(n / hours, 1)
    return {"window_kst": [start.astimezone(KST).isoformat(), end.astimezone(KST).isoformat()], "hours": round(hours, 2), "orders": orders, "orders_per_hour": per_hour(orders),
            "attempts": attempts, "attempts_per_hour": per_hour(attempts), "paid": paid, "paid_per_hour": per_hour(paid),
            "surplus_before_breakage": surplus, "surplus_per_hour": per_hour(surplus), "paid_per_attempt": round(paid / attempts, 4) if attempts else None,
            "payment_refused": sum(refused.values()), "payment_refused_per_paid": round(sum(refused.values()) / paid, 4) if paid else None, "payment_refused_reasons": refused.most_common(8),
            "stops": stops.most_common(), "stops_per_hour": {k: per_hour(v) for k, v in stops.most_common()},
            "gap_under_10s": sum(g < 10 for g in gaps), "gap_under_20s": sum(g < 20 for g in gaps), "gaps": len(gaps), "kit_log": kit_counts(root, start, end), "market_429": NO_429}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO time with offset")
    parser.add_argument("--until", help="ISO time with offset; default now")
    parser.add_argument("--out", help="also write the result to this file")
    args = parser.parse_args()
    end = parse_time(args.until) if args.until else datetime.now(timezone.utc)
    result = measure(ROOT, parse_time(args.since), end)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
