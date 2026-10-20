"""Append-only seller memory: snapshots grouped by A2A task, not by URL.

Open snapshots are pending/interrupted, not failed sales. A restart restores
historical evidence only; A2A's in-memory task store is not restored.
"""
import json
import os
from pathlib import Path
import threading

OUTCOMES = {"open", "deal", "rejected", "canceled"}


class SellerMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.latest = {}
        try:
            with self.path.open(encoding="utf-8") as source:
                for number, line in enumerate(source, 1):
                    try:
                        row = json.loads(line)
                        self._validate(row)
                    except (ValueError, TypeError) as error:
                        raise ValueError(f"seller memory line {number} is invalid; file retained") from error
                    self.latest[row["task_id"]] = row
        except FileNotFoundError:
            pass

    @staticmethod
    def _validate(row):
        if (not isinstance(row, dict) or not isinstance(row.get("task_id"), str)
                or not row["task_id"] or not isinstance(row.get("item"), str)
                or not isinstance(row.get("outcome"), str) or row["outcome"] not in OUTCOMES):
            raise ValueError("seller memory needs task_id, item and a known outcome")
        if row["outcome"] == "deal" and (type(row.get("sale_price")) is not int or row["sale_price"] < 0):
            raise ValueError("completed sale needs a nonnegative integer sale_price")

    def record(self, row: dict):
        self._validate(row)
        encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_APPEND positions each write at EOF. A single write prevents
            # independent seller processes from overwriting each other's rows.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                if os.write(fd, encoded) != len(encoded):
                    raise OSError("short write to seller memory")
                os.fsync(fd)
            finally:
                os.close(fd)
            self.latest[row["task_id"]] = dict(row)

    def summary(self, item: str) -> dict:
        with self._lock:
            rows = [dict(row) for row in self.latest.values() if row["item"] == item]
        closed = [row for row in rows if row["outcome"] != "open"]
        sold = [row for row in closed if row["outcome"] == "deal"]
        recent = sorted(closed, key=lambda row: row.get("updated_at", ""))[-5:]
        return {
            "item": item,
            "completed_negotiations": len(closed),
            "sales": len(sold),
            "rejected": sum(row["outcome"] == "rejected" for row in closed),
            "canceled": sum(row["outcome"] == "canceled" for row in closed),
            "pending_or_interrupted": len(rows) - len(closed),
            "sale_rate": len(sold) / len(closed) if closed else None,
            "average_sale_price": sum(row["sale_price"] for row in sold) / len(sold) if sold else None,
            "recent": [{key: row.get(key) for key in
                        ("outcome", "opening_ask", "last_ask", "last_offer", "sale_price",
                         "buyer_messages", "seller_quotes", "elapsed_seconds")} for row in recent],
        }

    def completed_purchases(self, customer_id: str) -> int:
        """Only completed tasks count, across all items; snapshots count once."""
        with self._lock:
            return sum(row["outcome"] == "deal" and row.get("customer_id") == customer_id
                       for row in self.latest.values())
