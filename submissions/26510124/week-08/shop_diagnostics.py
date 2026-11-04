"""Read-only, bounded seller bottleneck report; no network or model calls.

Counts observed tasks, never messages or board visits. Signed receipts remain
UNCONFIRMED until matched settlement evidence appears; missing evidence cannot
prove a checkout failed. No transcripts, keys, customer identities or prices
are emitted. Run this on the server for the server's local evidence.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2_000_000
MAX_ROWS = 4000
SETTLED = re.compile(r"^\d{2}:\d{2}:\d{2} \[acp\] settled .+ at ([0-9]+), ledger #([0-9]+), unit ([A-Za-z0-9_.:-]+) grade [ABC]$")
SETTLE_REFUSED = re.compile(r"^\d{2}:\d{2}:\d{2} \[acp\] settle refused: .+$")
REASONS = {"stock_unavailable", "stock_reserved", "inventory_unavailable", "price_rejected", "model_error",
           "invalid_request", "negotiation_expired", "buyer_cancelled", "capacity_busy", "internal_error"}
TERMINAL = {"receipt_issued", "rejected", "stock_unavailable", "error", "expired", "cancelled"}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 200


def read_json(path, now):
    meta = {"status": "missing", "limit_bytes": MAX_BYTES}
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        meta["age_seconds"] = round(now - path.stat().st_mtime, 1)
        if len(data) > MAX_BYTES:
            return None, {**meta, "status": "oversized"}
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return value, {**meta, "status": "read"}
    except FileNotFoundError:
        return None, meta
    except (OSError, ValueError, UnicodeError):
        return None, {**meta, "status": "unreadable"}


def read_tail(path, now, *, jsonl=True):
    meta = {"status": "missing", "limit_bytes": MAX_BYTES, "limit_rows": MAX_ROWS,
            "truncated": False, "malformed_rows": 0, "partial_last_line": False}
    try:
        with path.open("rb") as stream:
            start = max(0, stream.seek(0, 2) - MAX_BYTES)
            stream.seek(start)
            raw = stream.read(MAX_BYTES)
        meta["age_seconds"] = round(now - path.stat().st_mtime, 1)
    except FileNotFoundError:
        return [], meta
    except OSError:
        return [], {**meta, "status": "unreadable"}
    lines = raw.splitlines()
    if start:
        lines = lines[1:]  # May be a partial row; never interpret it.
    if raw and not raw.endswith(b"\n"):
        lines = lines[:-1]
        meta["partial_last_line"] = True
    meta.update(status="read", truncated=bool(start or len(lines) > MAX_ROWS))
    rows = []
    for line in lines[-MAX_ROWS:]:
        try:
            value = json.loads(line) if jsonl else line.decode("utf-8")
            if jsonl and not isinstance(value, dict):
                raise ValueError("Expected object")
            rows.append(value)
        except (ValueError, UnicodeError):
            meta["malformed_rows"] += 1
    meta["rows_read"] = len(rows)
    return rows, meta


def recent(row, rnd, now, since, time_key="time"):
    return (type(row.get("round")) is int and row["round"] == rnd
            and number(row.get(time_key)) and since <= row[time_key] <= now)


def inventory_summary(inv, holds, meta, rnd, now):
    if not inv or inv.get("round") != rnd or not isinstance(inv.get("units"), list):
        return {"status": "unavailable_or_different_round"}
    units = {u["unit"]: u for u in inv["units"] if isinstance(u, dict) and identifier(u.get("unit"))}
    blocked = {key: u for key, u in units.items() if key in (holds or {})}
    eligible = {key: u for key, u in units.items() if u.get("grade") in ("A", "B", "C")
                and (u.get("kind") == "goods" or u.get("kind") == "food"
                     and number(u.get("expires_at")) and u["expires_at"] > now)}
    return {"status": "fresh" if -5 <= meta.get("age_seconds", -999) <= 45 else "stale",
            "units": len(units), "skus": len({u.get("sku") for u in units.values() if identifier(u.get("sku"))}),
            "blocked_by_issued_receipt": len(blocked) if holds is not None else None,
            "blocked_skus": len({u.get("sku") for u in blocked.values() if identifier(u.get("sku"))}) if holds is not None else None,
            "eligible_not_receipt_held": len(set(eligible) - set(blocked)) if holds is not None else None,
            "note": "Active negotiations live in the seller process and are not included; issued receipt holds are never released by this report."}


def build_report(root=ROOT, *, hours=24, rnd=None, now=None):
    """Return only aggregate observations; never update cache or live state."""
    root, now = Path(root), time.time() if now is None else now
    if not number(hours) or not 0 < hours <= 168 or not number(now):
        raise ValueError("hours must be greater than 0 and at most 168")
    if rnd is not None and (type(rnd) is not int or rnd < 1):
        raise ValueError("round must be a positive integer")
    sources, states = {}, {}
    for name in ("inventory", "receipt-reservations", "sales-performance", "market-evidence"):
        states[name], sources[name] = read_json(root / ".market" / f"{name}.json", now)
    inv, memory, public = (states[k] or {} for k in ("inventory", "sales-performance", "market-evidence"))
    if rnd is None:
        rnd = inv.get("round")
        if type(rnd) is not int or rnd < 1:
            raise ValueError("Cannot identify the current round from inventory; pass --round explicitly")
    since = now - hours * 3600
    resets = [s["reset_at"] for s in (memory, public) if s.get("round") == rnd
              and number(s.get("reset_at")) and since < s["reset_at"] <= now]
    if resets:
        since = max(resets)
    events, sources["seller_events"] = read_tail(root / "logs/inventory-seller-events.jsonl", now)
    restock, sources["restock_decisions"] = read_tail(root / "logs/restock-decisions.jsonl", now)
    kit, sources["kit_log"] = read_tail(root / "logs/market-kit.log", now, jsonl=False)
    tasks, excluded = {}, Counter()
    for row in events:
        if not recent(row, rnd, now, since):
            excluded["unknown_round" if type(row.get("round")) is not int else "other_round_or_time"] += 1
            continue
        if not identifier(row.get("task")):
            excluded["invalid_task"] += 1
            continue
        task = tasks.setdefault(row["task"], {"opened": False, "receipt": None, "end": None})
        task["opened"] |= row.get("outcome") == "opened"
        if row.get("outcome") == "receipt_issued":
            task["receipt"] = row
        if (row.get("terminal") is True
                or row.get("terminal") is not False and isinstance(row.get("outcome"), str)
                and row["outcome"] in TERMINAL):
            if task["end"] is None or row["time"] >= task["end"]["time"]:
                task["end"] = row
    receipts = [t["receipt"] for t in tasks.values() if t["receipt"]]
    unit_tasks = Counter(r.get("unit") for r in receipts if identifier(r.get("unit")))
    confirmations = set()
    memory_rows = memory.get("sales", []) if memory.get("schema") == 1 and memory.get("round") == rnd else []
    public_rows = public.get("events", []) if public.get("schema") == 1 and public.get("round") == rnd else []
    memory_rows = memory_rows[-1000:] if isinstance(memory_rows, list) else []
    public_rows = public_rows[-2000:] if isinstance(public_rows, list) else []
    # Lookup indexes bound matching work; never compare every task against every event.
    cached = {(r.get("unit"), r.get("sku"), r.get("price"), r.get("receipt_time"))
              for r in memory_rows if isinstance(r, dict) and identifier(r.get("unit"))
              and identifier(r.get("sku")) and type(r.get("price")) is int
              and type(r.get("ledger")) is int and r["ledger"] > 0 and number(r.get("receipt_time"))
              and number(r.get("observed_at")) and r["receipt_time"] <= r["observed_at"] <= now}
    ledger = {}
    for row in public_rows:
        if (isinstance(row, dict) and row.get("kind") == "settle" and identifier(row.get("unit"))
                and identifier(row.get("sku")) and type(row.get("price")) is int
                and type(row.get("seq")) is int and row["seq"] > 0 and number(row.get("ts"))
                and since <= row["ts"] <= now):
            key = (row["unit"], row["sku"], row["price"])
            ledger[key] = max(ledger.get(key, 0), row["ts"])
    kit_settled = set()
    for line in kit:
        match = SETTLED.fullmatch(line)
        if match and int(match[2]) > 0:
            kit_settled.add((match[3], int(match[1])))
    for receipt in receipts:
        unit, sku, price = (receipt.get(k) for k in ("unit", "sku", "price"))
        if (not identifier(unit) or unit_tasks[unit] != 1 or not identifier(sku)
                or type(price) is not int or price <= 0):
            continue
        if ((unit, sku, price, receipt["time"]) in cached
                or ledger.get((unit, sku, price), 0) >= receipt["time"]
                or (unit, price) in kit_settled):
            confirmations.add(receipt["task"])
    counts = Counter({"observed_tasks": len(tasks), "opened_tasks": 0, "tasks_without_open_event": 0,
                      "receipt_issued": 0, "settled": 0, "receipt_unconfirmed": 0,
                      "open_or_missing_end": 0})
    reasons = Counter({reason: 0 for reason in sorted(REASONS)})
    for task_id, task in tasks.items():
        counts["opened_tasks" if task["opened"] else "tasks_without_open_event"] += 1
        if task["receipt"]:
            counts["receipt_issued"] += 1
            counts["settled" if task_id in confirmations else "receipt_unconfirmed"] += 1
        elif task["end"]:
            row = task["end"]
            reason = row.get("reason_code")
            # Legacy outcomes cannot reliably distinguish stock contention, price or model failure.
            if isinstance(reason, str) and reason in REASONS:
                reasons[reason] += 1
            elif row.get("outcome") == "expired":
                reasons["negotiation_expired"] += 1
            elif row.get("outcome") == "cancelled":
                reasons["buyer_cancelled"] += 1
            else:
                reasons["unknown_terminal_reason"] += 1
        else:
            counts["open_or_missing_end"] += 1
    statuses, restock_unknown = Counter(), 0
    for row in restock:
        if type(row.get("round")) is not int and number(row.get("time")) and since <= row["time"] <= now:
            restock_unknown += 1
        if recent(row, rnd, now, since):
            status = row.get("status")
            # Do not echo untrusted detail strings, exceptions, prompts or arbitrary status text.
            if not isinstance(status, str) or status not in {"planned", "refused", "closed", "window_closed", "window_invalid", "window_closing", "window_changed", "inventory_changed",
                               "already_planned", "cooldown", "unavailable"}:
                status = "other_status"
            statuses[status] += 1
    return {"schema": 1, "generated_at": now, "round": rnd, "since": since, "requested_hours": hours,
            "tasks": dict(counts), "terminal_reasons": dict(reasons),
            "checkout": {"status": "UNCONFIRMED" if counts["receipt_unconfirmed"] else "no_unconfirmed_receipt_in_sample",
                         "unconfirmed_tasks": counts["receipt_unconfirmed"], "confirmed_failed_tasks": None,
                         "undated_tail_settle_refusal_attempts": (sum(bool(SETTLE_REFUSED.fullmatch(line)) for line in kit)
                                                                 if sources["kit_log"]["status"] == "read" else None),
                         "note": "Shop settle-refused lines are UNDATED_TAIL attempt counts, not unique tasks or current-round/window counts. Receipt without matched settlement does not prove failure."},
            "inventory": inventory_summary(inv, states["receipt-reservations"], sources["inventory"], rnd, now),
            "restock": {"decisions_by_status": dict(statuses), "excluded_unknown_round": restock_unknown,
                        "note": "A plan is not proof of a completed inventory purchase; retries may create multiple decision rows."},
            "coverage": {"excluded_seller_rows": dict(excluded), "ambiguous_receipt_units": sum(n > 1 for n in unit_tasks.values()),
                         "sources": sources,
                         "limits": ["Bounded local tails, not all board visits; counts are unique task IDs within the window.",
                                    "Missing open events may be pre-reservation rejects or truncated history.",
                                    "Current-round records only; legacy events with no round are excluded.",
                                    "Settlements require matching local receipts and cached confirmation; samples may miss settlements.",
                                    "No live requests, model calls, reservation releases or state writes."]}}


def format_report(report):
    tasks = report["tasks"]
    when = datetime.fromtimestamp(report["generated_at"], timezone.utc).isoformat(timespec="seconds")
    lines = [f"Shop diagnostics: round {report['round']}, last {report['requested_hours']:g}h, generated {when}",
             f"Unique observed tasks: {tasks['observed_tasks']} (opened: {tasks['opened_tasks']}; no open event: {tasks['tasks_without_open_event']})",
             f"Receipts: {tasks['receipt_issued']}; confirmed settlements: {tasks['settled']}; UNCONFIRMED: {tasks['receipt_unconfirmed']}",
             f"Open / missing terminal event: {tasks['open_or_missing_end']}", "Terminal reasons:"]
    lines.extend(f"  {key}: {value}" for key, value in report["terminal_reasons"].items())
    inv = report["inventory"]
    lines.append(f"Inventory: {inv['status']}; units={inv.get('units', 'unknown')}; blocked by issued receipt={inv.get('blocked_by_issued_receipt', 'unknown')}; blocked SKUs={inv.get('blocked_skus', 'unknown')}")
    lines.append(f"Restock decision rows: {json.dumps(report['restock']['decisions_by_status'], sort_keys=True)}")
    lines.append(f"UNDATED_TAIL shop settle-refused attempts: {report['checkout']['undated_tail_settle_refusal_attempts']} (no task IDs, dates or rounds).")
    lines.append("Failed checkout tasks: UNKNOWN. Receipt without matched settlement is not proof of failed payment.")
    lines.extend("Limit: " + note for note in report["coverage"]["limits"])
    lines.append("Source status: " + ", ".join(f"{name}={meta['status']}" + (" (truncated)" if meta.get("truncated") else "") for name, meta in report["coverage"]["sources"].items()))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT, help="Agent directory containing .market and logs")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--round", type=int, dest="rnd", help="Explicit round if inventory is unavailable")
    ap.add_argument("--json", action="store_true", help="Emit aggregate JSON instead of text")
    args = ap.parse_args(argv)
    try:
        report = build_report(args.root, hours=args.hours, rnd=args.rnd)
    except ValueError as exc:
        ap.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, allow_nan=False) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
