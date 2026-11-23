"""Atomic local candidate ledger; separate from kit and production logs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    pass


class TradeLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError("ledger is unreadable or incomplete") from exc
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise LedgerError("ledger has invalid structure")
        return value

    def contains(self, *, transaction_id: str | None = None, order_id: str | None = None,
                 unit_id: str | None = None) -> bool:
        return any((transaction_id and row.get("transaction_id") == transaction_id)
                   or (order_id and row.get("order_id") == order_id)
                   or (unit_id and row.get("unit_id") == unit_id) for row in self.rows)

    def append(self, row: dict[str, Any]) -> None:
        required = ("transaction_id", "order_id", "unit_id", "status")
        if any(not isinstance(row.get(key), str) or not row[key] for key in required):
            raise LedgerError("ledger row is missing required IDs")
        if self.contains(transaction_id=row["transaction_id"], order_id=row["order_id"], unit_id=row["unit_id"]):
            raise LedgerError("duplicate transaction, order, or unit")
        new_rows = [*self.rows, dict(row)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(new_rows, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass
            raise LedgerError("ledger write failed atomically") from exc
        self.rows = new_rows
