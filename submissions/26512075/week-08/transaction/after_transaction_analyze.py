"""Export a seller strategy dataset without inventing prices or prescribing policy.

Run from week-08:
  python transaction/after_transaction_analyze.py
  python transaction/after_transaction_analyze.py --ledger PATH --output PATH

Groups are shop/item/currency. Rates use all recorded terminal outcomes, including
cancellations. Revenue uses completed records with known prices only; profit uses
completed records with BOTH price and cost. Costs exclude unrecorded fees.
This is descriptive history, not evidence that a pricing change causes a sale.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from after_transaction_record import DEFAULT_LEDGER, STATUSES, load_records, write_json


def total(values):
    return float(sum((Decimal(str(v)) for v in values), Decimal(0)))


def average(values):
    return total(values) / len(values) if values else None


def summarize(rows):
    counts = Counter(row["status"] for row in rows)
    completed = [r for r in rows if r["status"] == "completed"]
    prices = [r["price"] for r in completed if r["price"] is not None]
    paired = [r for r in completed if r["price"] is not None and r["cost"] is not None]
    profit = total([Decimal(str(r["price"])) - Decimal(str(r["cost"])) for r in paired])
    return {
        "terminal_count": len(rows),
        "outcome_counts": {s: counts[s] for s in STATUSES},
        "outcome_rates": {s: counts[s] / len(rows) if rows else None for s in STATUSES},
        "priced_completed_count": len(prices),
        "completed_missing_price_count": len(completed) - len(prices),
        "known_revenue": total(prices) if prices else None,
        "mean_completed_total_price": average(prices),
        "min_completed_total_price": min(prices) if prices else None,
        "max_completed_total_price": max(prices) if prices else None,
        "profit_observation_count": len(paired),
        "known_gross_profit": profit if paired else None,
        "mean_known_gross_profit": profit / len(paired) if paired else None,
        "mean_turns": average([r["turns"] for r in rows if r["turns"] is not None]),
        "unsuccessful_reasons": dict(Counter(r.get("reason") or "unknown" for r in rows
                                             if r["status"] != "completed")),
    }


def analyze(records):
    groups = defaultdict(list)
    dataset = []
    for row in records:
        groups[(row["shop"], row["item"], row["currency"])].append(row)
        complete = row["status"] == "completed"
        known_profit = complete and row["price"] is not None and row["cost"] is not None
        dataset.append({**row, "sale_completed": int(complete),
                        "realized_revenue": row["price"] if complete else 0,
                        "gross_profit": total([Decimal(str(row["price"])) - Decimal(str(row["cost"]))])
                        if known_profit else None})
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "outcome_counts": {s: sum(r["status"] == s for r in records) for s in STATUSES},
        "definitions": {
            "grain": "One terminal outcome per shop/task_id; amounts are transaction totals.",
            "rates": "Outcome count divided by recorded terminal outcomes, including cancellations.",
            "coverage": "Only explicitly recorded outcomes; not all live-market transactions.",
            "currency": "Monetary summaries are separated by shop, item, and currency.",
            "profit": "Completed price minus known acquisition cost; excludes unrecorded fees.",
            "strategy_use": "Compare item outcomes and observed prices; small samples and buyer mix may distort comparisons.",
            "prediction": "Outcome, realized revenue, and profit are labels, not pre-sale prediction inputs.",
        },
        "groups": [{"shop": key[0], "item": key[1], "currency": key[2], **summarize(rows)}
                   for key, rows in sorted(groups.items())],
        "dataset": dataset,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("after_transaction_analysis.json"))
    args = parser.parse_args()
    try:
        if args.ledger.resolve() == args.output.resolve():
            raise ValueError("Output must differ from the source ledger")
        result = analyze(load_records(args.ledger))
        result["source"] = str(args.ledger.resolve())
        write_json(args.output, result)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Cannot analyze transactions: {exc}\n")
    print(f"Exported {result['record_count']} records to {args.output}")


if __name__ == "__main__":
    main()
