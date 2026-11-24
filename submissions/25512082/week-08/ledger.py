"""Small atomic ledger used by the operating Seller.

The market ledger remains authoritative for settlement. This file records
local A2A agreements so a restarted Seller does not offer the same physical
unit twice while settlement is being confirmed.
"""
from __future__ import annotations

import json
import os
import time
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

    def contains(
        self,
        *,
        transaction_id: str | None = None,
        order_id: str | None = None,
        unit_id: str | None = None,
    ) -> bool:
        return any(
            (transaction_id and row.get("transaction_id") == transaction_id)
            or (order_id and row.get("order_id") == order_id)
            or (unit_id and row.get("unit_id") == unit_id)
            for row in self.rows
        )

    def unit_reserved(self, unit_id: str, status: str, window: float, now: float) -> bool:
        """True while an agreement with ``status`` for this unit is younger than ``window`` s.

        A rejected row or an agreement the market never settled must not lock a
        physical unit forever (2026-11-10: three held units were unsellable).
        A settled unit leaves inventory.json, so it is never offered again.
        """
        return any(
            row.get("unit_id") == unit_id
            and row.get("status") == status
            and (
                not isinstance(row.get("recorded_at"), (int, float))
                or now - row["recorded_at"] < window
            )
            for row in self.rows
        )

    def append(
        self,
        row: dict[str, Any],
        *,
        unit_window: float | None = None,
        now: float | None = None,
    ) -> None:
        """Append atomically. Without ``unit_window`` any row with the same unit_id
        blocks (original behaviour); with it only a fresh agreement of the same
        status blocks the unit. Transaction and order ids stay unique either way."""
        required = ("transaction_id", "order_id", "unit_id", "status")
        if any(not isinstance(row.get(key), str) or not row[key] for key in required):
            raise LedgerError("ledger row is missing required IDs")
        if unit_window is None:
            duplicate = self.contains(
                transaction_id=row["transaction_id"],
                order_id=row["order_id"],
                unit_id=row["unit_id"],
            )
        else:
            current = time.time() if now is None else now
            duplicate = self.contains(
                transaction_id=row["transaction_id"], order_id=row["order_id"]
            ) or self.unit_reserved(row["unit_id"], row["status"], unit_window, current)
        if duplicate:
            raise LedgerError("duplicate transaction, order, or unit")

        new_rows = [*self.rows, dict(row)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(new_rows, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise LedgerError("ledger write failed atomically") from exc
        self.rows = new_rows
