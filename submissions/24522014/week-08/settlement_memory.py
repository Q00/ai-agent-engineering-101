"""Join local trade observations with kit payment outcomes without rewriting history."""

import json
from pathlib import Path


def payment_outcomes(path):
    path = Path(path)
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    outcomes = {}
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break  # The kit may still be appending the last line.
            raise
        if not isinstance(row, dict) or not row.get("order"):
            raise ValueError("Invalid payment memory record")
        outcomes[row["order"]] = row
    return outcomes


def reconcile(records, payments):
    merged = []
    for original in records:
        row = dict(original)
        if row.get("order") and row.get("outcome") == "purchased":
            payment = payments.get(row["order"])
            if payment is None:
                row["outcome"] = "awaiting_payment"
            elif (payment.get("shop") != row.get("url_at_time")
                  or payment.get("item") != row.get("item") or payment.get("price") != row.get("price")):
                row["outcome"] = "payment_record_mismatch"
            elif payment.get("paid") is True and payment.get("ledger") is not None:
                row["outcome"] = "purchased"
                row["verification"] = "market_settled"
                row["settlement_ledger"] = payment["ledger"]
            else:
                row["outcome"] = "payment_refused"
                row["payment_reason"] = payment.get("reason", "unknown")
        merged.append(row)
    return merged
