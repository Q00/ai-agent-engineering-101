"""Offline/read-only quote alignment ITT audit. Never imports trading modules.

Explicit input snapshots only. Missing outcomes remain in assignment denominators;
receipt/completed is not paid. Output is a new file (-o) or stdout, never an input.
"""
import argparse
import collections
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(row):
    value = row.get("ts", row.get("time", row.get("at")))
    if number(value):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))).timestamp()
        except ValueError:
            pass
    return None


def load(path, asof):
    data = path.read_bytes()
    rows, bad, untimed = [], 0, 0
    for line in data.splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("object required")
        except (ValueError, UnicodeError):
            bad += 1
            continue
        at = timestamp(row)
        if at is None:
            untimed += 1
        if at is None or at <= asof:
            rows.append(row)
    return rows, {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                  "malformed": bad, "untimed_rows": untimed}


def index(rows):
    """Never silently pick one of conflicting outcomes for a single order."""
    by, duplicates, conflicts = {}, 0, set()
    for row in rows:
        oid = row.get("order")
        if not isinstance(oid, str) or not oid:
            continue
        if oid in by:
            duplicates += 1
            if row != by[oid]:
                conflicts.add(oid)
        else:
            by[oid] = row
    return {k: v for k, v in by.items() if k not in conflicts}, duplicates, len(conflicts)


def summarize(journal, orders, payments, settled, start, end, asof, study):
    assignments, dup, conflict = index([r for r in journal if r.get("event") == "assignment" and r.get("study") == study
                                       and number(r.get("at")) and start <= r["at"] < end])
    audit = {"assignment_duplicates": dup, "assignment_conflicts": conflict}
    mappings = []
    for label, rows in (("orders", orders), ("payments", payments), ("settled", settled)):
        mapped, dup, conflict = index(rows)
        mappings.append(mapped)
        audit[label + "_duplicates"] = dup
        audit[label + "_conflicts"] = conflict
    ob, pb, sb = mappings
    invalid = [oid for oid, r in assignments.items() if r.get("arm") not in ("A", "B")
               or r.get("start") != start or r.get("end") != end]
    audit["invalid_assignments"] = len(invalid)
    events = collections.defaultdict(list)
    audit["orphan_quote_events"] = audit["mismatched_quote_arms"] = 0
    for row in journal:
        if row.get("event") != "quote" or row.get("study") != study:
            continue
        oid = row.get("order")
        if oid not in assignments:
            audit["orphan_quote_events"] += 1
            continue
        if row.get("arm") != assignments[oid].get("arm"):
            audit["mismatched_quote_arms"] += 1
            continue
        events[oid].append(row)
    arms = {}
    for arm in ("A", "B"):
        ids = [oid for oid, a in assignments.items() if a.get("arm") == arm]
        d = collections.Counter(itt_orders=len(ids))
        states, skus, shops, kinds, reasons, actual_skus = [collections.Counter() for _ in range(6)]
        seconds = []
        for oid in ids:
            a = assignments[oid]
            kinds[str(a.get("kind"))] += 1
            r, p, s = ob.get(oid), pb.get(oid), sb.get(oid)
            d["missing_order"] += r is None
            d["missing_payment"] += p is None
            d["paid_observed"] += bool(p and p.get("paid") is True)
            d["payment_rejected"] += bool(p and p.get("paid") is False)
            d["paid_settlement_missing"] += bool(p and p.get("paid") is True and not s)
            d["settlement_without_paid"] += bool(s and not (p and p.get("paid") is True))
            d["paid_and_settled"] += bool(p and p.get("paid") is True and s)
            if s:
                actual_skus[str(s.get("sku"))] += 1
                d["settled_sku_mismatch"] += bool(r and s.get("sku") and r.get("sku") and s["sku"] != r["sku"])
            qs = events[oid]
            eligible = [q for q in qs if q.get("reason") == "opportunity" and number(q.get("candidate")) and q["candidate"] > 0]
            d["quote_events"] += len(qs)
            d["opportunity_quotes"] += len(eligible)
            d["opportunity_orders"] += bool(eligible)
            # New rows are durable proposals only: a timed-out writer can finish
            # later. Confirm actual publication from the final order record.
            confirmed = (r or {}).get("quote_alignment_applied", [])
            applied = [q for q in qs if (q.get("applied") is True if "confirmation" not in q else
                       any(c.get("shop") == q.get("shop") and c.get("sku") == q.get("sku")
                           for c in confirmed if isinstance(c, dict)))]
            d["applied_quotes"] += len(applied)
            d["applied_orders"] += bool(applied)
            reasons.update(str(q.get("reason")) for q in qs)
            if r is None:
                d["missing_turns"] += 1
                continue
            states[str(r.get("state"))] += 1
            skus[str(r.get("sku"))] += 1
            shops[str(r.get("shop"))] += 1
            d["selected_opportunity_orders"] += any(q.get("shop") == r.get("shop") and q.get("sku") == r.get("sku") for q in eligible)
            d["changed_shop_orders"] += bool(r.get("first_choice") and r.get("shop") and r["first_choice"] != r["shop"])
            d["selection_missing"] += not bool(r.get("sku") and r.get("shop"))
            d["warning_orders"] += bool(r.get("warnings") or r.get("problems") or r.get("no_warranty"))
            if s and number(s.get("price")) and number(r.get("limit")):
                d["settlement_over_limit"] += s["price"] > r["limit"]
            turns = r.get("turns")
            if (isinstance(turns, dict) and turns and set(turns) <= {"take", "fast", "code", "harness"}
                    and all(number(v) and v >= 0 and int(v) == v for v in turns.values())):
                d["harness_turns"] += turns.get("harness", 0)
                d["harness_orders"] += turns.get("harness", 0) > 0
                d["code_turns"] += turns.get("code", 0)
            else:
                d["missing_turns"] += 1
            sec = (r.get("seconds") or {}).get("end")
            if number(sec) and sec >= 0:
                seconds.append(sec)
        # Explicit zeros matter for sparse opportunities and empty arms.
        keys = ("missing_order missing_payment paid_observed payment_rejected paid_settlement_missing "
                "settlement_without_paid paid_and_settled quote_events opportunity_quotes opportunity_orders "
                "applied_quotes applied_orders selected_opportunity_orders changed_shop_orders selection_missing "
                "warning_orders settlement_over_limit settled_sku_mismatch missing_turns harness_turns harness_orders code_turns").split()
        result = {k: d[k] for k in ["itt_orders", *keys]}
        n = len(ids)
        result.update(paid_and_settled_per_itt=d["paid_and_settled"] / n if n else None,
                      opportunity_per_itt=d["opportunity_orders"] / n if n else None,
                      harness_turns_per_itt=d["harness_turns"] / n if n and not d["missing_turns"] else None,
                      known_end_seconds_sum=sum(seconds), end_seconds_missing=n - len(seconds),
                      end_seconds_median=statistics.median(seconds) if seconds else None,
                      end_seconds_per_itt=sum(seconds) / n if n and len(seconds) == n else None,
                      selected_sku=dict(skus), settled_sku=dict(actual_skus), selected_shop=dict(shops), assigned_kind=dict(kinds),
                      states=dict(states), quote_reasons=dict(reasons))
        arms[arm] = result
    return {"study": study, "window": [start, end], "asof": asof,
            "followup_5m_complete": asof >= end + 300, "arms": arms, "audit": audit,
            "note": "Assignment ITT before product selection, left joined through asof. Observed paid+settled is not "
                    "mature utility/Players. Missing is unresolved, not failure or zero cost. No buyer LLM reduction "
                    "implies score gain. Changed shop compares first choice, not a counterfactual policy selection. "
                    "Untimed rows are snapshot observations, not proven asof events; naive times use Asia/Seoul. "
                    "Sparse opportunity may support no adoption. No adoption verdict is automated."}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "orders", "payments", "settled"):
        ap.add_argument("--" + name, required=True, type=Path)
    for name in ("start", "end", "asof"):
        ap.add_argument("--" + name, required=True, type=float)
    ap.add_argument("--study", required=True)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()
    if not all(math.isfinite(x) for x in (args.start, args.end, args.asof)) or args.end - args.start != 1800 or args.asof < args.start:
        ap.error("finite 30 minute window and asof >= start required")
    inputs = [args.journal, args.orders, args.payments, args.settled]
    if args.output and (args.output.exists() or args.output.resolve() in {p.resolve() for p in inputs}):
        ap.error("output must be a new file distinct from all inputs")
    try:
        loaded = [load(p, args.asof) for p in inputs]
        report = summarize(*(x[0] for x in loaded), args.start, args.end, args.asof, args.study)
    except OSError:
        ap.error("input unreadable; no report generated")
    report["inputs"] = dict(zip(("journal", "orders", "payments", "settled"), (x[1] for x in loaded)))
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text, end="")
    invalid = any(x[1]["malformed"] for x in loaded) or any(v for k, v in report["audit"].items() if "conflict" in k or k in ("invalid_assignments", "mismatched_quote_arms"))
    return 2 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
