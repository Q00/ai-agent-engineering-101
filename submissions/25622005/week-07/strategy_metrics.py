"""Read-only comparison of recent and previous equal-length memory windows.

Seller tasks belong to their started_at cohort and use the last saved outcome.
Buyer deals are memory outcomes, not independently verified market acceptance.
Windows are [start, end); timestamps without an offset are interpreted as UTC.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from loyalty_pricing import RESERVE


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def price(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def load_rows(directory, kind, quality):
    path = directory / ("seller-memory.jsonl" if kind == "seller" else "buyer-memory.json")
    report = quality[kind] = {"omitted_rows": 0, "file_errors": 0}

    def omit(message):
        report["omitted_rows"] += 1
        print(f"warning: {path.name}: {message}; memory unchanged", file=sys.stderr)

    try:
        content = path.read_text(encoding="utf-8")
        if kind == "seller":
            rows = []
            for number, line in enumerate(content.splitlines(), 1):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    omit(f"invalid/incomplete JSON at line {number}")
        else:
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise ValueError("expected a JSON array")
    except (OSError, ValueError) as error:
        report.update(omitted_rows=None, file_errors=1)
        print(f"warning: {path.name}: {error}; omitted row count unknown; memory unchanged", file=sys.stderr)
        return []
    valid, latest = [], {}
    for number, row in enumerate(rows, 1):
        if kind == "seller" and isinstance(row, dict) and isinstance(row.get("task_id"), str):
            latest[row["task_id"]] = row
        try:
            if not isinstance(row, dict) or not isinstance(row.get("item"), str):
                raise ValueError("missing item")
            if kind == "seller":
                if not isinstance(row.get("task_id"), str) or not row["task_id"]:
                    raise ValueError("missing task_id")
                field, amount = "started_at", "sale_price"
                outcomes = {"open", "deal", "canceled", "rejected"}
            else:
                field, amount = "time", "price"
                outcomes = {"deal", "no_deal", "invalid_receipt", "unavailable"}
            timestamp(row[field])
            if row.get("outcome") not in outcomes:
                raise ValueError("unknown outcome")
            if row["outcome"] == "deal" and not price(row.get(amount)):
                raise ValueError("deal without a valid price")
            valid.append(row)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            omit(f"invalid record {number}: {error}")
    if kind == "seller":
        # An invalid latest snapshot must not resurrect an older success.
        valid = [row for row in valid if latest[row["task_id"]] is row]
    return valid


def summarize(rows, kind, hours=1):
    deals = [row for row in rows if row["outcome"] == "deal"]
    count = len(rows)
    if kind == "seller":
        profit = (sum(r['sale_price'] - RESERVE[r['item']] for r in deals)
                  if all(r['item'] in RESERVE for r in deals) else None)
        return {"inquiries": count, "sales": len(deals),
                "average_sale_price": sum(r["sale_price"] for r in deals) / len(deals) if deals else None,
                "completion_rate": len(deals) / count if count else None,
                "pending": sum(r["outcome"] == "open" for r in rows),
                "recorded_profit": profit, "profit_per_hour": profit / hours if profit is not None else None}
    savings = (sum(r['budget'] - r['price'] for r in deals)
               if all(price(r.get('budget')) and r['budget'] >= r['price'] for r in deals) else None)
    return {"attempts": count, "recorded_deals": len(deals),
            "average_purchase_price": sum(r["price"] for r in deals) / len(deals) if deals else None,
            "success_rate": len(deals) / count if count else None,
            "invalid_receipts": sum(r["outcome"] == "invalid_receipt" for r in rows),
            "recorded_savings": savings, "savings_per_hour": savings / hours if savings is not None else None}


def difference(current, previous):
    """Rate differences are proportions (0.1 means ten percentage points)."""
    return {key: current[key] - previous[key] if current[key] is not None and previous[key] is not None
            else None for key in current}


def compare(directory, now, hours=1):
    quality = {}
    memories = {kind: load_rows(Path(directory), kind, quality) for kind in ("seller", "buyer")}
    duration = timedelta(hours=hours)
    windows = {}
    for name, start, end in (("previous", now - 2 * duration, now - duration),
                             ("current", now - duration, now)):
        window = windows[name] = {"start": start.isoformat(), "end": end.isoformat()}
        for kind, rows in memories.items():
            field = "started_at" if kind == "seller" else "time"
            selected = [r for r in rows if start <= timestamp(r[field]) < end]
            window[kind] = {"total": summarize(selected, kind, hours), "by_item": {
                item: summarize([r for r in selected if r["item"] == item], kind, hours)
                for item in sorted({r["item"] for r in rows})}}
    delta = {}
    for kind in memories:
        current, previous = windows["current"][kind], windows["previous"][kind]
        delta[kind] = {"total": difference(current["total"], previous["total"]), "by_item": {
            item: difference(current["by_item"][item], previous["by_item"][item])
            for item in current["by_item"]}}
    return {"hours_per_window": hours, **windows, "change": delta, "data_quality": quality,
            "scope": "Local memory only; recorded profit/savings are not market-verified; no market share estimate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--hours", type=float, default=1)
    parser.add_argument("--now", type=timestamp, default=datetime.now(timezone.utc))
    args = parser.parse_args()
    if not math.isfinite(args.hours) or not 0 < args.hours <= 24 * 365:
        parser.error("hours must be positive and at most 8760")
    print(json.dumps(compare(args.dir, args.now, args.hours), ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
