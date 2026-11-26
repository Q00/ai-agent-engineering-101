"""Read-only hourly audit; snapshots never authorize policy changes or trading."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, ClassVar, Final

import typer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
)

from r3.accounting import STOCK_FILE, financial_window
from r3.growth import GrowthEvidence, SkuGrowth, build_growth
from r3.ledger_db import LEDGER_FILE
from r3.market_files import Inventory, read_catalog
from r3.policy import PolicyHistory

HOUR: Final = 3600
GRACE: Final = 60
ROUND: Final = 3
app = typer.Typer()


class AuditModel(BaseModel):
    """Immutable parsed audit boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="ignore", allow_inf_nan=False
    )


class AuditRequest(AuditModel):
    """Explicit local paths and observation identity."""

    run_dir: Path
    output: Path
    shop_url: str
    buyer_name: str
    release: str
    deadline: AwareDatetime | None = None


class Financial(AuditModel):
    """Existing accounting output; unavailable is never zero."""

    shop_net_profit: float | None = None
    financial_status: str = "unavailable"
    financial_gaps: tuple[str, ...] = ("ledger_missing",)


class BuyerCohort(AuditModel):
    """Created and settled inside the same half-open observation window."""

    orders: int | None = None
    settled: int | None = None
    settled_rate: float | None = None
    status: str = "unavailable"


class AuditSnapshot(AuditModel):
    """Recorded facts; held price points are actual prices, not demand coverage."""

    observed_at: float
    window_start: float
    window_end: float
    release: str
    policy_version: int | None
    cash: int | None
    stock_cost: int | None
    stock_units: int | None
    refund_reserve: int | None = None
    supply_factors: dict[str, float] | None = None
    held_price_points: dict[str, list[int]] | None = None
    financial: Financial
    buyer: BuyerCohort
    mature_available: bool
    mature_by_sku: dict[str, SkuGrowth]
    unknown_mature_skus: int | None
    grade_profit_status: str = "unavailable: growth API exposes SKU totals only"
    freshness_seconds: dict[str, float | None]
    previous_observed_at: float | None = None
    comparison_confounded: bool = True
    net_profit_change: float | None = None
    comparison_basis: str = "descriptive_only"


def _read[T: BaseModel](path: Path, model: type[T]) -> T | None:
    try:
        return model.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def _inventory(path: Path) -> Inventory | None:
    value = _read(path, Inventory)
    if value is None or value.round != ROUND:
        return None
    if not {"balance", "units"} <= value.model_fields_set:
        return None
    if any("cost" not in unit.model_fields_set for unit in value.units):
        return None
    return value


def _age(path: Path, now: float) -> float | None:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return None


def _db(path: Path) -> sqlite3.Connection:
    """Open existing SQLite only; callers own closure."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)


def _cohort(
    db: sqlite3.Connection, buyer: str, start: float, end: float
) -> BuyerCohort:
    bounds = TypeAdapter(tuple[float | None, float | None]).validate_python(
        db.execute("SELECT min(ts),max(ts) FROM rows WHERE round=3").fetchone()
    )
    if bounds[0] is None or bounds[1] is None or bounds[0] > start or bounds[1] < end:
        return BuyerCohort(status="incomplete_ledger_window")
    orders, settled = TypeAdapter(tuple[int, int]).validate_python(
        db.execute(
            """WITH cohort AS (
        SELECT json_extract(data,'$.order') AS oid,min(ts) AS created
        FROM rows WHERE round=3 AND kind='order' AND buyer=? AND ts>=? AND ts<?
        GROUP BY oid HAVING oid IS NOT NULL AND oid!='')
        SELECT count(*),coalesce(sum(EXISTS(
        SELECT 1 FROM rows s WHERE s.round=3 AND s.kind='settle' AND s.buyer=?
        AND s.ts>=cohort.created AND s.ts<?
        AND json_extract(s.data,'$.order')=cohort.oid
        )),0) FROM cohort""",
            (buyer, start, end, buyer, end),
        ).fetchone()
    )
    return BuyerCohort(
        orders=orders,
        settled=settled,
        settled_rate=settled / orders if orders else None,
        status="complete",
    )


def collect(request: AuditRequest, now: float) -> AuditSnapshot:
    """Read existing sources without creating or migrating databases."""
    start, end = now - HOUR, now - GRACE
    folder = request.run_dir / ".market"
    inv = _inventory(folder / "inventory.json")
    policy = _read(request.run_dir / "r3-policy.json", PolicyHistory)
    financial, buyer = Financial(), BuyerCohort()
    freshness: dict[str, float | None] = {
        "inventory": _age(folder / "inventory.json", now),
        "policy": _age(request.run_dir / "r3-policy.json", now),
        "ledger": None,
        "stock": None,
    }
    try:
        with closing(_db(request.run_dir / LEDGER_FILE)) as db:
            latest = TypeAdapter(tuple[float | None]).validate_python(
                db.execute("SELECT max(ts) FROM rows WHERE round=3").fetchone()
            )[0]
            freshness["ledger"] = now - latest if latest is not None else None
            buyer = _cohort(db, request.buyer_name, start, end)
            financial = Financial.model_validate(
                financial_window(
                    (request.run_dir / STOCK_FILE).resolve(),
                    db,
                    start=start,
                    end=end,
                    shop=request.shop_url,
                )
            )
            if buyer.orders is None:
                financial = Financial(financial_gaps=("ledger_window_not_covered",))
        with closing(_db(request.run_dir / STOCK_FILE)) as db:
            latest = TypeAdapter(tuple[float | None]).validate_python(
                db.execute("SELECT max(ts) FROM snapshots").fetchone()
            )[0]
            freshness["stock"] = now - latest if latest is not None else None
    except (sqlite3.Error, ValidationError):
        financial = Financial(financial_gaps=("source_unreadable",))
    growth = (
        build_growth(request.run_dir, request.shop_url, read_catalog(folder), inv, now)
        if inv is not None
        else GrowthEvidence({}, available=False)
    )
    mature = {sku: value for sku, value in growth.by_sku.items() if value.mature_sales}
    points: dict[str, set[int]] = {}
    if inv:
        for unit in inv.units:
            points.setdefault(unit.category, set()).add(unit.list_price)
    return AuditSnapshot(
        observed_at=now,
        window_start=start,
        window_end=end,
        release=request.release,
        policy_version=policy.current.version if policy else None,
        cash=inv.balance if inv else None,
        stock_cost=sum(u.cost for u in inv.units) if inv else None,
        stock_units=len(inv.units) if inv else None,
        refund_reserve=growth.refund_reserve if growth.available else None,
        supply_factors={row.sku: row.factor for row in inv.surge} if inv else None,
        held_price_points={c: sorted(p) for c, p in points.items()} if inv else None,
        financial=financial,
        buyer=buyer,
        mature_available=growth.available,
        mature_by_sku=mature,
        unknown_mature_skus=sum(v.mature_net is None for v in mature.values())
        if growth.available
        else None,
        freshness_seconds=freshness,
    )


def audit(request: AuditRequest, now: float) -> AuditSnapshot | None:
    """Stop before any file access at deadline; otherwise publish atomic latest."""
    if request.deadline is not None and now >= request.deadline.timestamp():
        return None
    current = collect(request, now)
    previous = _read(request.output / "latest.json", AuditSnapshot)
    if previous is not None:
        old, new = previous.financial.shop_net_profit, current.financial.shop_net_profit
        current = current.model_copy(
            update={
                "previous_observed_at": previous.observed_at,
                "comparison_confounded": previous.release != current.release
                or previous.policy_version != current.policy_version
                or current.policy_version is None
                or previous.window_end > current.window_start,
                "net_profit_change": new - old
                if new is not None and old is not None
                else None,
            }
        )
    request.output.mkdir(parents=True, exist_ok=True)
    payload = current.model_dump_json(indent=2) + "\n"
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    _ = (request.output / f"{stamp}.json").write_text(payload, encoding="utf-8")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=request.output, delete=False, prefix=".latest-", encoding="utf-8"
    ) as temporary:
        _ = temporary.write(payload)
        name = Path(temporary.name)
    try:
        _ = name.replace(request.output / "latest.json")
    finally:
        name.unlink(missing_ok=True)
    return current


@app.command()
def main(  # noqa: PLR0913 - explicit operator CLI contract
    *,
    run_dir: Annotated[Path, typer.Option()],
    shop_url: Annotated[str, typer.Option()],
    buyer_name: Annotated[str, typer.Option()],
    release: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    deadline: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Print one local audit, or expired without reading or writing."""
    request = AuditRequest(
        run_dir=run_dir,
        output=output,
        shop_url=shop_url,
        buyer_name=buyer_name,
        release=release,
        deadline=datetime.fromisoformat(deadline) if deadline else None,
    )
    result = audit(request, time.time())
    typer.echo(result.model_dump_json() if result else '{"status":"expired"}')


if __name__ == "__main__":
    app()
