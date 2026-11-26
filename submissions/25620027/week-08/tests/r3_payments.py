"""Write the kit's payment row for a recorded agreement (test helper)."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from r3.events import EventLedger


def paid(events: EventLedger, order: str, shop: str, sku: str, *, ok: bool) -> None:
    """Record the kit's payment row for one agreed order."""
    with closing(sqlite3.connect(events.path)) as db, db:
        _ = db.execute(
            "INSERT INTO payments (order_id, ts, shop, item, price, paid, ledger,"
            " reason) VALUES (?,?,?,?,?,?,?,?)",
            (order, time.time(), shop, sku, 6000, int(ok), 1 if ok else None, ""),
        )
