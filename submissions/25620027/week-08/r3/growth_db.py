"""Read-only SQLite boundary for SKU growth evidence; no schema creation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
)

from r3.accounting import DAY, STOCK_FILE, STORAGE_RATE
from r3.ledger_db import BREAK_WINDOW, LEDGER_FILE

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from r3.market_files import CatalogItem, Inventory

HOUR: Final = 3600.0
LOOKBACK: Final = DAY + BREAK_WINDOW
MAX_LAG: Final = 180.0
type _LedgerRow = tuple[float, str, str, str, str]
_ROW_BATCH: Final = TypeAdapter(list[_LedgerRow])


def _rows(
    ledger: sqlite3.Connection, kinds: tuple[str, str], now: float
) -> Iterator[_LedgerRow]:
    """Parse bounded batches without retaining the market's raw JSON history."""
    with closing(
        ledger.execute(
            """SELECT ts,kind,shop,sku,data FROM rows WHERE round=3
            AND kind IN (?,?) AND ts>=? AND ts<=? ORDER BY ts,seq""",
            (*kinds, now - LOOKBACK, now),
        )
    ) as cursor:
        while batch := _ROW_BATCH.validate_python(cursor.fetchmany(128)):
            yield from batch


@dataclass(frozen=True, slots=True)
class GrowthSale:
    """An exact-unit settlement and its optional observed break/refund."""

    unit: str
    sku: str
    at: float
    amount: int | None
    grade: str = ""
    goods: bool = True
    own: bool = True
    warranty_hours: float | None = None
    broken_at: float | None = None
    refund: int | None = None


@dataclass(frozen=True, slots=True)
class GrowthStock:
    """Cost identity from an observed unit, never reconstructed from a list price."""

    cost: int
    supplied: float
    first_seen: float


@dataclass(frozen=True, slots=True)
class GrowthFacts:
    """Immutable input surface for offline evidence fixtures."""

    sales: Sequence[GrowthSale]
    stock: Mapping[str, GrowthStock]
    current: Mapping[str, str]
    now: float
    storage_rate: float = STORAGE_RATE


class _Payload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, strict=True, allow_inf_nan=False
    )
    unit: str = ""
    amount: int | None = Field(default=None, ge=0)
    grade: str = ""
    warranty_hours: float | None = Field(default=None, ge=0)
    expires_at: float | None = None
    refund: NonNegativeInt | bool | None = Field(
        default=None,
        validation_alias=AliasChoices("refund_amount", "refund", "refunded"),
    )


def _refund(sale: _Payload, broken: _Payload, delay: float) -> int | None:
    """Honor historical explicit refunds, else the exact sale's warranty."""
    if broken.refund is None:
        if sale.warranty_hours is None:
            return None
        horizon = min(BREAK_WINDOW, sale.warranty_hours * HOUR)
        return sale.amount if 0 <= delay <= horizon and horizon > 0 else 0
    if broken.refund is True:
        return sale.amount
    return int(broken.refund)


def read_growth(
    run_dir: Path,
    shop_url: str,
    catalog: dict[str, CatalogItem],
    inventory: Inventory,
    now: float,
) -> GrowthFacts | None:
    """Read bounded existing journals only; missing/stale evidence fails closed."""
    try:
        with (
            closing(
                sqlite3.connect(
                    f"{(run_dir / LEDGER_FILE).resolve().as_uri()}?mode=ro",
                    uri=True,
                    timeout=1,
                )
            ) as ledger,
            closing(
                sqlite3.connect(
                    f"{(run_dir / STOCK_FILE).resolve().as_uri()}?mode=ro",
                    uri=True,
                    timeout=1,
                )
            ) as journal,
        ):
            # Both passes must observe one ledger snapshot despite collector inserts.
            _ = ledger.execute("BEGIN")
            latest = TypeAdapter(tuple[float] | None).validate_python(
                ledger.execute(
                    "SELECT ts FROM rows ORDER BY seq DESC LIMIT 1"
                ).fetchone()
            )
            snapshot = TypeAdapter(tuple[float] | None).validate_python(
                journal.execute(
                    "SELECT ts FROM snapshots ORDER BY ts DESC LIMIT 1"
                ).fetchone()
            )
            if (
                not shop_url
                or inventory.storage_per_day != STORAGE_RATE
                or not latest
                or not snapshot
                or any(abs(now - row[0]) > MAX_LAG for row in (latest, snapshot))
            ):
                return None
            stock_rows = TypeAdapter(
                list[tuple[str, NonNegativeInt, float, float]]
            ).validate_python(
                journal.execute(
                    """SELECT unit,cost,supplied,first_seen FROM stock
                    WHERE missing_at IS NULL OR missing_at>=?""",
                    (now - LOOKBACK,),
                ).fetchall()
            )
            breaks = {
                p.unit: (ts, p)
                for ts, _, _, _, raw in _rows(ledger, ("broken", "break"), now)
                if (p := _Payload.model_validate_json(raw)).unit
            }
            sales: list[GrowthSale] = []
            for ts, _, shop, sku, raw in _rows(ledger, ("settle", "settle"), now):
                data = _Payload.model_validate_json(raw)
                own = shop.rstrip("/") == shop_url.rstrip("/")
                hit = breaks.get(data.unit)
                if hit is not None and not 0 <= hit[0] - ts <= BREAK_WINDOW:
                    if own:
                        return None
                    # Exclude inconsistent market risk samples rather than treating
                    # them as sound units or suppressing our exact-unit economics.
                    continue
                item = catalog.get(sku)
                sales.append(
                    GrowthSale(
                        data.unit,
                        sku,
                        ts,
                        data.amount,
                        data.grade,
                        item.kind == "goods" if item else data.expires_at is None,
                        own,
                        data.warranty_hours,
                        hit[0] if hit else None,
                        _refund(data, hit[1], hit[0] - ts) if hit else None,
                    )
                )
        return GrowthFacts(
            sales,
            {
                unit: GrowthStock(cost, supplied, first)
                for unit, cost, supplied, first in stock_rows
            },
            {u.unit: u.sku for u in inventory.units},
            now,
            inventory.storage_per_day,
        )
    except (OSError, sqlite3.Error, ValidationError):
        return None
