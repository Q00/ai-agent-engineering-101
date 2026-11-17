"""Record terminal seller outcomes in JSON using only the Python standard library.

Example (run from week-08):
  python transaction/after_transaction_record.py --task t1 --shop shop1 \
    --item sku1 --status completed --price 120 --cost 80 --currency KRW

Prices and costs are TOTAL amounts for the transaction, in one currency/unit.
Use market-credit as the currency for market credits. Missing amounts stay null.
Call record_result() from a seller after its outcome is confirmed, or use the CLI.
This tool does not infer outcomes from incomplete logs or contact a live market.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile

DEFAULT_LEDGER = Path(__file__).with_name("after_transaction_records.json")
STATUSES = ("completed", "rejected", "cancelled")


def validate(record):
    if not isinstance(record, dict):
        raise ValueError("Each record must be an object")
    record = dict(record)
    for field in ("task_id", "shop", "item", "currency", "recorded_at"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    timestamp = datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("recorded_at must include a timezone")
    if record.get("status") == "canceled":
        record["status"] = "cancelled"
    if record.get("status") not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    for field in ("price", "cost", "opening_ask", "last_offer"):
        value = record.get(field)
        if value is not None and (type(value) not in (int, float)
                                  or not math.isfinite(value) or value < 0):
            raise ValueError(f"{field} must be a finite nonnegative number or null")
        record[field] = value
    for field in ("quantity", "turns"):
        value = record.get(field)
        if value is not None and (type(value) is not int or value < (1 if field == "quantity" else 0)):
            raise ValueError(f"Invalid {field}")
        record[field] = value
    if record.get("reason") is not None and not isinstance(record["reason"], str):
        raise ValueError("reason must be a string or null")
    return record


def load_records(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
        raise ValueError("Expected schema_version: 1 and a records array")
    records = [validate(row) for row in data["records"]]
    keys = [(row["shop"], row["task_id"]) for row in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate shop/task_id records; resolve them before analysis")
    return records


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="after_transaction_", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def record_result(record, path=DEFAULT_LEDGER):
    """Append once per (shop, task_id). Identical retries do not duplicate results.

    Conflicting outcomes raise ValueError, preserving the earlier record.
    The recorded timestamp is an ingestion time, not a guessed transaction time.
    """
    row = validate({"recorded_at": datetime.now(timezone.utc).isoformat(), **record})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = load_records(path) if path.exists() else []
        for existing in records:
            if (existing["shop"], existing["task_id"]) == (row["shop"], row["task_id"]):
                without_time = lambda value: {k: v for k, v in value.items() if k != "recorded_at"}
                if without_time(existing) != without_time(row):
                    raise ValueError("Conflicting record for this shop/task_id")
                return existing
        records.append(row)
        write_json(path, {"schema_version": 1, "records": records})
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    for flag in ("task", "shop", "item", "currency"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--status", required=True, choices=(*STATUSES, "canceled"))
    for flag in ("price", "cost", "opening-ask", "last-offer"):
        parser.add_argument("--" + flag, type=float)
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--turns", type=int)
    parser.add_argument("--reason")
    args = vars(parser.parse_args())
    path = args.pop("ledger")
    args["task_id"] = args.pop("task")
    try:
        print(json.dumps(record_result(args, path), indent=2, allow_nan=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Cannot record transaction: {exc}\n")


if __name__ == "__main__":
    main()
