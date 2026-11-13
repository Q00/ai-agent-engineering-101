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

from market_clock import schedule_now

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2_000_000
MAX_ROWS = 4000
SETTLED = re.compile(r"^\d{2}:\d{2}:\d{2} \[acp\] settled .+ at ([0-9]+), ledger #([0-9]+), unit ([A-Za-z0-9_.:-]+) grade [ABC]$")
SETTLE_REFUSED = re.compile(r"^\d{2}:\d{2}:\d{2} \[acp\] settle refused: .+$")
REASONS = {"stock_unavailable", "stock_reserved", "inventory_unavailable", "price_rejected", "model_error",
           "invalid_request", "negotiation_expired", "buyer_cancelled", "capacity_busy", "internal_error"}
TERMINAL = {"receipt_issued", "rejected", "stock_unavailable", "error", "expired", "cancelled"}
LEASE_STATUSES = ("issued", "checkout", "settling", "uncertain", "completed", "revoked")
EVIDENCE_ERRORS = {"ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "ConnectError",
                   "ReadError", "RemoteProtocolError", "HTTPStatusError", "ValueError", "OSError",
                   "JSONDecodeError"}


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


def lifecycle_summary(state, holds, source, now):
    """Read aggregate lease observations only; never instantiate the mutating store."""
    result = {"status": "unavailable", "legacy_holds": None,
              **{status: None for status in LEASE_STATUSES},
              "deadline_passed_issued_or_checkout": None,
              "note": "All retained lifecycle rows, not a round/window transaction count. "
                      "Separate file snapshots may change during this read. A passed deadline is not proof "
                      "that a hold was released. Settling or uncertain does not mean unpaid or failed; "
                      "this report never expires, repairs or releases reservations."}
    valid_id = lambda value: isinstance(value, str) and 0 < len(value) <= 256
    holds_valid = (isinstance(holds, dict)
                   and all(valid_id(unit) and valid_id(task) for unit, task in holds.items()))
    if source["status"] == "missing":
        if holds_valid:
            result.update(status="legacy_no_lifecycle", legacy_holds=len(holds))
        return result
    if state is None:
        return result
    if (type(state.get("version")) is not int or state["version"] != 1
            or not isinstance(state.get("leases"), dict)):
        return {**result, "status": "invalid_lifecycle"}
    rows = state["leases"]
    counts = Counter({status: 0 for status in LEASE_STATUSES})
    overdue = 0
    for task, row in rows.items():
        if (not valid_id(task) or not isinstance(row, dict) or not valid_id(row.get("unit"))
                or not isinstance(row.get("status"), str) or row["status"] not in LEASE_STATUSES
                or not number(row.get("issued_at")) or not number(row.get("deadline"))
                or row["deadline"] < row["issued_at"]
                or row["status"] != "revoked" and row["deadline"] == row["issued_at"]):
            return {**result, "status": "invalid_lifecycle"}
        counts[row["status"]] += 1
        overdue += row["status"] in ("issued", "checkout") and row["deadline"] <= now
    result.update(status="snapshot", **counts, deadline_passed_issued_or_checkout=overdue)
    if holds_valid:
        # A task/unit disagreement may be corruption or snapshots taken during
        # an atomic-file update. Do not classify it as a legacy reservation.
        if any(task in rows and rows[task]["unit"] != unit for unit, task in holds.items()):
            result["status"] = "snapshot_hold_mismatch"
        else:
            result["legacy_holds"] = sum(task not in rows for task in holds.values())
    return result


def supply_schedule(inv, meta, rnd, now):
    """Display only the current kit announcement, never a guessed 20-minute clock."""
    if inv.get("round") != rnd or not -5 <= meta.get("age_seconds", -999) <= 45:
        return {"status": "inventory_unavailable_or_stale"}
    raw = inv.get("restock")
    if not isinstance(raw, dict) or type(raw.get("open")) is not bool:
        return {"status": "schedule_unavailable"}
    times = {k: raw[k] for k in ("opens_at", "closes_at") if number(raw.get(k)) and raw[k] >= 0}
    if raw["open"]:
        if (len(times) != 2 or times["closes_at"] <= times["opens_at"]
                or not times["opens_at"] <= now < times["closes_at"]):
            return {"status": "schedule_inconsistent", **times}
        return {"status": "open", **times, "seconds_remaining": max(0, int(times["closes_at"] - now))}
    result = {"status": "closed", **times}
    if times.get("opens_at", 0) > now:
        result["seconds_until_open"] = math.ceil(times["opens_at"] - now)
    return result


def evidence_summary(state, source, polls, rnd, now, since):
    """Report availability separately from demand; all ages use local log time."""
    age = now - state["updated_at"] if number(state.get("updated_at")) else None
    if source["status"] != "read":
        status = source["status"]
    elif state.get("round") != rnd:
        status = "other_round"
    elif state.get("schema") != 1 or not isinstance(state.get("events"), list) or age is None:
        status = "invalid"
    else:
        status = "fresh" if -5 <= age <= 3600 else "stale_or_clock_changed"
    failures = Counter()
    rows = [row for row in polls if recent(row, rnd, now, since)]
    for row in rows:
        if row.get("status") == "unavailable":
            error = row.get("error")
            failures[error if isinstance(error, str) and error in EVIDENCE_ERRORS else "other_error"] += 1
    latest = max(rows, key=lambda row: row["time"], default={})
    stock = state.get("market_stock")
    stock_fresh = (isinstance(stock, dict) and stock.get("round") == rnd
                   and number(stock.get("observed_at")) and -5 <= now - stock["observed_at"] <= 3600)
    names = stock.get("shop_names_by_url") if stock_fresh else None
    return {"cache_status": status, "cache_age_seconds": round(age, 1) if age is not None else None,
            "cached_fact_rows": len(state["events"]) if isinstance(state.get("events"), list) else None,
            "stock_snapshot_fresh": stock_fresh,
            "named_shop_urls": sum(isinstance(v, str) and bool(v) for v in names.values()) if isinstance(names, dict) else 0,
            "refresh_failures_by_error": dict(failures),
            "last_refresh_attempt_age_seconds": round(now - latest["time"], 1) if latest else None,
            "note": "Missing/stale evidence is not zero market demand. Counts cover bounded local records."}


def response_summary(events, rnd, now, since):
    """One latency sample and latest observed negotiation stage per task."""
    first, last = {}, {}
    for row in events:
        if not recent(row, rnd, now, since) or not identifier(row.get("task")):
            continue
        task = row["task"]
        duration = row.get("first_response_seconds")
        if number(duration) and duration >= 0:
            first.setdefault(task, duration)
        if task not in last or row["time"] >= last[task]["time"]:
            last[task] = row
    samples = sorted(first.values())
    stages = {"opening", "negotiate", "close", "final_offer", "final_reply"}
    return {"first_response_samples": len(samples),
            "first_response_p50_seconds": samples[(len(samples)-1)//2] if samples else None,
            "first_response_p95_seconds": samples[max(0, math.ceil(len(samples)*.95)-1)] if samples else None,
            "last_stage_by_task": dict(Counter(r.get("stage") if r.get("stage") in stages else "unknown"
                                                for r in last.values())),
            "note": "Successful first quote publication latency; missing samples are unknown, not zero."}


def checkout_summary(rows, receipts, now):
    """Correlate bounded events to current-round receipts; no missing=failed inference."""
    by_task = {r["task"]: r for r in receipts}
    allowed = {"checkout_created", "checkout_cancelled", "settlement_started",
               "settlement_refused", "settlement_uncertain", "settlement_confirmed"}
    seen, confirmed = {}, set()
    for row in rows:
        receipt = by_task.get(row.get("task")) if identifier(row.get("task")) else None
        if (not receipt or row.get("unit") != receipt.get("unit")
                or not number(row.get("time")) or not receipt["time"] <= row["time"] <= now
                or row.get("stage") not in allowed):
            continue
        if row["stage"] == "settlement_confirmed":
            if type(row.get("ledger")) is not int or row["ledger"] <= 0:
                continue
            confirmed.add(row["task"])
        seen.setdefault(row["stage"], set()).add(row["task"])
    return {"matched_tasks_by_stage": {k: len(v) for k, v in seen.items()},
            "receipt_without_observed_checkout": len(set(by_task) - seen.get("checkout_created", set())),
            "note": "Stages may overlap. Only events matched by task, unit and receipt time count. Missing checkout evidence is unknown."}, confirmed


def build_report(root=ROOT, *, hours=24, rnd=None, now=None):
    """Return only aggregate observations; never update cache or live state."""
    root, now = Path(root), time.time() if now is None else now
    if not number(hours) or not 0 < hours <= 168 or not number(now):
        raise ValueError("hours must be greater than 0 and at most 168")
    if rnd is not None and (type(rnd) is not int or rnd < 1):
        raise ValueError("round must be a positive integer")
    sources, states = {}, {}
    for name in ("inventory", "receipt-reservations", "receipt-leases", "sales-performance", "market-evidence"):
        states[name], sources[name] = read_json(root / ".market" / f"{name}.json", now)
    inv, memory, public = (states[k] or {} for k in ("inventory", "sales-performance", "market-evidence"))
    if rnd is None:
        rnd = inv.get("round")
        if type(rnd) is not int or rnd < 1:
            raise ValueError("Cannot identify the current round from inventory; pass --round explicitly")
    clock = schedule_now(root / ".market", rnd, local_now=now)
    since = now - hours * 3600
    resets = [s["reset_at"] for s in (memory, public) if s.get("round") == rnd
              and number(s.get("reset_at")) and since < s["reset_at"] <= now]
    if resets:
        since = max(resets)
    events, sources["seller_events"] = read_tail(root / "logs/inventory-seller-events.jsonl", now)
    restock, sources["restock_decisions"] = read_tail(root / "logs/restock-decisions.jsonl", now)
    evidence_polls, sources["market_evidence_polls"] = read_tail(root / "logs/market-evidence.jsonl", now)
    kit, sources["kit_log"] = read_tail(root / "logs/market-kit.log", now, jsonl=False)
    checkout_events, sources["checkout_events"] = read_tail(root / ".market/checkout-events.jsonl", now)
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
    pipeline, local_confirmed = checkout_summary(checkout_events, receipts, now)
    confirmations.update(local_confirmed)
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
    statuses, restock_unknown, unknown_refusals = Counter(), 0, Counter()
    plans = Counter({"nonempty": 0, "empty": 0, "unknown_or_invalid": 0})
    for row in restock:
        if type(row.get("round")) is not int and number(row.get("time")) and since <= row["time"] <= now:
            restock_unknown += 1
            if row.get("status") == "refused":
                # Older restock.main exceptions omitted round. Make these
                # visible separately, without pretending they belong to rnd or
                # echoing exception text that might contain private state.
                error = row.get("error")
                known = {"ValueError", "TypeError", "KeyError", "OSError", "FileNotFoundError",
                         "PermissionError", "JSONDecodeError", "InventoryError", "RuntimeError"}
                unknown_refusals[error if isinstance(error, str) and error in known else "other_error"] += 1
        if recent(row, rnd, now, since):
            status = row.get("status")
            # Do not echo untrusted detail strings, exceptions, prompts or arbitrary status text.
            if not isinstance(status, str) or status not in {"planned", "refused", "closed", "window_closed", "window_invalid", "window_closing", "window_changed", "inventory_changed",
                               "already_planned", "cooldown", "unavailable"}:
                status = "other_status"
            statuses[status] += 1
            if status == "planned":
                buy = row.get("buy")
                if isinstance(buy, list) and not buy:
                    plans["empty"] += 1
                elif (isinstance(buy, list) and len(buy) <= 10 and all(isinstance(b, dict)
                      and identifier(b.get("sku")) and type(b.get("qty")) is int
                      and 1 <= b["qty"] <= 20 for b in buy)):
                    plans["nonempty"] += 1
                else:
                    plans["unknown_or_invalid"] += 1
    return {"schema": 1, "generated_at": now, "round": rnd, "since": since, "requested_hours": hours,
            "tasks": dict(counts), "terminal_reasons": dict(reasons),
            "response_timing": response_summary(events, rnd, now, since),
            "checkout_pipeline": pipeline,
            "checkout": {"status": "UNCONFIRMED" if counts["receipt_unconfirmed"] else "no_unconfirmed_receipt_in_sample",
                         "unconfirmed_tasks": counts["receipt_unconfirmed"], "confirmed_failed_tasks": None,
                         "undated_tail_settle_refusal_attempts": (sum(bool(SETTLE_REFUSED.fullmatch(line)) for line in kit)
                                                                 if sources["kit_log"]["status"] == "read" else None),
                         "note": "Shop settle-refused lines are UNDATED_TAIL attempt counts, not unique tasks or current-round/window counts. Receipt without matched settlement does not prove failure."},
            "inventory": inventory_summary(inv, states["receipt-reservations"], sources["inventory"], rnd, now),
            "receipt_lifecycle": lifecycle_summary(states["receipt-leases"], states["receipt-reservations"],
                                                    sources["receipt-leases"], now),
            "restock": {"decisions_by_status": dict(statuses), "excluded_unknown_round": restock_unknown,
                        "plans_by_contents": dict(plans),
                        "market_evidence": evidence_summary(public, sources["market-evidence"], evidence_polls, rnd, now, since),
                        "unknown_round_refusals_by_error": dict(unknown_refusals),
                        "schedule": supply_schedule(inv, sources["inventory"], rnd, clock["now"]),
                        "clock": {key: clock.get(key) for key in ("status", "offset_seconds", "uncertainty_seconds")},
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
    lines.append("Response timing: " + json.dumps(report.get("response_timing", {}), sort_keys=True))
    lines.append("Checkout pipeline: " + json.dumps(report.get("checkout_pipeline", {}), sort_keys=True))
    lifecycle = report.get("receipt_lifecycle")
    if lifecycle is not None:
        lines.append("Receipt lifecycle: " + lifecycle["status"] + "; " + "; ".join(
            f"{key}={lifecycle[key] if lifecycle[key] is not None else 'unknown'}"
            for key in ("legacy_holds", *LEASE_STATUSES)))
        overdue = lifecycle["deadline_passed_issued_or_checkout"]
        lines.append(f"Passed deadlines (issued/checkout): {overdue if overdue is not None else 'unknown'}; release is not confirmed by this read.")
        lines.append("Lifecycle limit: " + lifecycle["note"])
    lines.append(f"Restock decision rows: {json.dumps(report['restock']['decisions_by_status'], sort_keys=True)}")
    lines.append("Restock plans by contents (not confirmed purchases): " + json.dumps(report["restock"].get("plans_by_contents", {}), sort_keys=True))
    lines.append("Market evidence: " + json.dumps(report["restock"].get("market_evidence", {}), sort_keys=True))
    lines.append("Restock rows without a known round: " + str(report["restock"]["excluded_unknown_round"])
                 + "; refusals by error: " + json.dumps(report["restock"].get("unknown_round_refusals_by_error", {}), sort_keys=True)
                 + " (within time window; excluded from current-round counts).")
    lines.append(f"Supply window (kit announcement): {json.dumps(report['restock']['schedule'], sort_keys=True)}")
    lines.append(f"Supply clock: {json.dumps(report['restock'].get('clock', {}), sort_keys=True)}")
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
