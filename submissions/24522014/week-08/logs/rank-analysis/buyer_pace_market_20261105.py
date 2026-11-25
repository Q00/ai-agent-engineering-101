"""Read-only: hour-by-hour market conditions from the kit log, for telling the ten-second interval apart from the market. No network, no model.

The kit's ledger numbers cover every row in the market, so their rate is a complete control, unlike the bounded public sample.
From the same log: registered shops, waits for an empty shop list, the delay between an order line and the shop list that follows, and restock read timeouts.
Attempts come from the buyer state, which keeps only orders that reached a shop; selection ends with no shop are counted from the records.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
LINE = re.compile(rb"^(\d\d):(\d\d):(\d\d) ")
ORDER = re.compile(rb"\[order (o-[0-9a-f]+)\]")
SHOPS = re.compile(rb"\[kit\] (\d+) shops in ")
LEDGER = re.compile(rb"ledger #(\d+)")
FIRST_DAY = datetime(2026, 11, 4, 10, 15, tzinfo=KST)


def kit_lines():
    """Dated lines, walking back from the file's own time over each midnight."""
    path = ROOT / "logs/market-kit.log"
    day = datetime.fromtimestamp(path.stat().st_mtime, KST).replace(hour=0, minute=0, second=0, microsecond=0)
    previous = None
    dated = []
    for raw in reversed(path.read_bytes().splitlines()):
        match = LINE.match(raw)
        if not match:
            continue
        seconds = int(match[1])*3600 + int(match[2])*60 + int(match[3])
        if previous is not None and seconds > previous:
            day -= timedelta(days=1)
        previous = seconds
        dated.append((day + timedelta(seconds=seconds), raw))
    dated.reverse()
    return dated


def market_table(dated):
    hours = defaultdict(lambda: {"shops": [], "ledger": [], "delays": [], "empty_list_waits": 0, "read_timeouts": 0, "order_lines": 0})
    waits = []
    last_order = None
    for moment, raw in dated:
        cell = hours[moment.strftime("%m-%d %H")]
        if ORDER.search(raw):
            cell["order_lines"] += 1
            last_order = moment
            continue
        found = SHOPS.search(raw)
        if found:
            cell["shops"].append(int(found[1]))
            if last_order:
                cell["delays"].append((moment-last_order).total_seconds())
            continue
        if b"no shops in the market yet" in raw:
            cell["empty_list_waits"] += 1
            waits.append(moment)
            continue
        if b"ReadTimeout" in raw:
            cell["read_timeouts"] += 1
        found = LEDGER.search(raw)
        if found:
            cell["ledger"].append((moment, int(found[1])))
    table = {}
    for hour, cell in sorted(hours.items()):
        rows = cell["ledger"]
        span = (rows[-1][0]-rows[0][0]).total_seconds() if len(rows) > 1 else 0
        table[hour] = {"ledger_rows_per_hour": round((rows[-1][1]-rows[0][1])/span*3600) if span else None, "shops_median": median(cell["shops"]) if cell["shops"] else None, "shops_min": min(cell["shops"]) if cell["shops"] else None,
                       "shops_max": max(cell["shops"]) if cell["shops"] else None, "order_to_shoplist_seconds_median": median(cell["delays"]) if cell["delays"] else None, "order_lines": cell["order_lines"],
                       "empty_list_waits": cell["empty_list_waits"], "read_timeouts": cell["read_timeouts"]}
    spans, start, end = [], None, None
    for moment in waits:
        if end and (moment-end).total_seconds() <= 120:
            end = moment
        else:
            if start:
                spans.append((start, end))
            start = end = moment
    if start:
        spans.append((start, end))
    return table, [{"from_kst": a.strftime("%m-%d %H:%M:%S"), "to_kst": b.strftime("%m-%d %H:%M:%S"), "seconds": (b-a).total_seconds()} for a, b in spans]


def buyer_table():
    """By hour, from the buyer's own records: reached a shop, ended in selection, and what each paid or failed."""
    state = json.loads((ROOT / ".market/buyer-support-state.json").read_text(encoding="utf-8"))
    reached = {a["order"] for a in state["attempts"]}
    oldest = min(a["at"] for a in state["attempts"])
    pay = {}
    for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        pay[row.get("order")] = row
    table = defaultdict(Counter)
    refused = defaultdict(Counter)
    folder = ROOT / "logs/market-buyer"
    unreadable = 0
    for name in sorted(n for n in os.listdir(folder) if n[:15] >= "20261105T000000"):
        try:
            record = json.loads((folder / name).read_text(encoding="utf-8"))["record"]
        except (OSError, ValueError, KeyError):
            unreadable += 1
            continue
        at = datetime.strptime(name[:21], "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc).astimezone(KST)
        hour = at.strftime("%H")
        order = record.get("order")
        cell = table[hour]
        cell["orders"] += 1
        cell["reached_a_shop"] += order in reached
        trail = [s for s in record.get("decision_trail") or [] if isinstance(s, dict)]
        code = str(trail[-1].get("reason_code")) if trail else str(record.get("failure_reason") or record.get("outcome"))
        for watched in ("selection_attempt_cooldown", "selection_no_useful_research", "seller_rejected", "quote_shelf_life_short", "transport_error", "agent_error"):
            cell[watched] += code == watched
        payment = pay.get(order)
        if payment:
            if payment.get("paid"):
                cell["paid"] += 1
            else:
                reason = str(payment.get("reason"))
                kind = "not_in_stock" if "not in stock" in reason else "shelf_life" if "shelf life" in reason else "grade" if "grade" in reason else "other"
                refused[hour][kind] += 1
    return {hour: dict(cell) | {"paid_per_reached": round(cell["paid"]/cell["reached_a_shop"], 4) if cell["reached_a_shop"] else None, "payment_refused": dict(refused[hour])} for hour, cell in sorted(table.items())}, datetime.fromtimestamp(oldest, KST).isoformat(), unreadable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    table, spans = market_table(kit_lines())
    buyer, oldest, unreadable = buyer_table()
    report = {"market_by_hour_kst": table, "empty_shop_list_spans": spans, "buyer_by_hour_kst_1105": buyer, "attempt_state_starts_kst": oldest, "unreadable_records": unreadable}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
