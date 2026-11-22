"""Half-hour local buyer report from bounded, incremental ledger facts."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import TypeAdapter

from r3.buyer_report_db import open_readonly, update_facts
from r3.buyer_report_models import (
    BANDS,
    HOUR,
    MATURITY,
    MAX_LAG,
    BudgetMetrics,
    BuyerReport,
    budget_band,
)
from r3.buyer_report_orders import ReportContext, order_result
from r3.market_files import CatalogItem

_CATALOG: Final = TypeAdapter(dict[str, CatalogItem])
_ORDERS: Final = TypeAdapter(
    list[tuple[str, float, int | None, str | None, float | None, int]]
)
_ATTEMPTS: Final = TypeAdapter(tuple[int, int, int])
_COUNT: Final = TypeAdapter(tuple[int])


def _cohort(
    context: ReportContext,
    start: float,
    end: float,
) -> tuple[dict[str, BudgetMetrics], int, int]:
    """Read only one completed issuance hour, streaming metadata in small batches."""
    facts, events, fresh = context.facts, context.events, context.fresh
    bands = {name: BudgetMetrics() for name in BANDS}
    authoritative = local = 0
    with closing(
        facts.execute(
            """SELECT o.order_id,coalesce(i.ts,o.observed_at),coalesce(o.budget,
i.budget),o.kind,coalesce(o.expires,i.expires),i.ts IS NOT NULL FROM
event_source.buyer_orders o LEFT JOIN issued i ON
i.order_id=o.order_id WHERE coalesce(i.ts,o.observed_at)>=? AND
coalesce(i.ts,o.observed_at)<? UNION ALL SELECT
i.order_id,i.ts,i.budget,NULL,i.expires,1 FROM issued i WHERE i.ts>=? AND
i.ts<? AND NOT EXISTS (SELECT 1 FROM event_source.buyer_orders o
WHERE o.order_id=i.order_id)""",
            (start, end, start, end),
        )
    ) as rows:
        while batch := _ORDERS.validate_python(rows.fetchmany(128)):
            for order_id, _at, budget, kind, expires, exact in batch:
                authoritative += exact
                local += 1 - exact
                metric = bands[budget_band(budget)]
                metric.orders += 1
                attempts, no_stock, timeouts = _ATTEMPTS.validate_python(
                    events.execute(
                        """SELECT count(*),coalesce(max(
reason LIKE '%not in stock%' OR reason
LIKE '%out of stock%'),0),coalesce(sum(reason='shop timeout'),0)
FROM buyer_attempts WHERE order_id=?""",
                        (order_id,),
                    ).fetchone()
                )
                metric.retries += max(0, attempts - 1)
                metric.no_stock_orders += no_stock
                metric.shop_timeouts += timeouts
                order_result(
                    context, metric, order_id, kind, expires=expires, retry=attempts > 1
                )
    unidentified = _COUNT.validate_python(
        facts.execute(
            "SELECT count(*) FROM unidentified_orders WHERE ts>=? AND ts<?",
            (start, end),
        ).fetchone()
    )[0]
    unknown = bands["unknown"]
    unknown.orders += unidentified
    unknown.pending_or_unknown += unidentified
    unknown.immature_or_unknown_orders += unidentified
    unknown.unidentified_orders = unidentified
    authoritative += unidentified
    for metric in bands.values():
        if metric.orders:
            metric.settled_rate = (
                metric.settled / metric.orders if fresh and not unidentified else None
            )
            metric.no_stock_rate = metric.no_stock_orders / metric.orders
            if metric.provisional_known_orders == metric.orders:
                metric.provisional_surplus = metric.provisional_surplus_known_subtotal
                metric.provisional_surplus_per_order = (
                    metric.provisional_surplus_known_subtotal / metric.orders
                )
            if metric.mature_known_orders == metric.orders:
                metric.mature_surplus = metric.mature_surplus_known_subtotal
                metric.mature_surplus_per_order = (
                    metric.mature_surplus_known_subtotal / metric.orders
                )
    return bands, authoritative, local


def write_buyer_report(  # noqa: PLR0913 - explicit integration boundary
    events_path: Path,
    ledger_path: Path,
    market_dir: Path,
    buyer_name: str,
    output_path: Path,
    *,
    now: float | None = None,
) -> BuyerReport:
    """Serialize cache/JSON/history publication across processes without waiting."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            return _write_buyer_report(
                events_path,
                ledger_path,
                market_dir,
                buyer_name,
                output_path,
                now=now,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_buyer_report(  # noqa: PLR0913 - explicit integration boundary
    events_path: Path,
    ledger_path: Path,
    market_dir: Path,
    buyer_name: str,
    output_path: Path,
    *,
    now: float | None = None,
) -> BuyerReport:
    """Write atomic JSON plus append-only history, without network/model calls."""
    observed = time.time() if now is None else now
    output_path.parent.mkdir(parents=True, exist_ok=True)
    catalog = _CATALOG.validate_json((market_dir / "catalog.json").read_bytes())
    with (
        closing(
            sqlite3.connect(output_path.with_suffix(".sqlite"), timeout=1, uri=True)
        ) as facts,
        closing(open_readonly(events_path)) as events,
    ):
        _ = events.execute("BEGIN")
        latest = update_facts(ledger_path, facts, buyer_name)
        fresh = latest is not None and 0 <= observed - latest <= MAX_LAG
        _ = facts.execute(
            "ATTACH DATABASE ? AS event_source",
            (f"{events_path.resolve().as_uri()}?mode=ro",),
        )
        end = observed // HOUR * HOUR
        context = ReportContext(facts, events, catalog, observed, fresh)
        bands, authoritative, local = _cohort(context, end - HOUR, end)
        mature_end = (observed - MATURITY) // HOUR * HOUR
        mature, _, _ = _cohort(context, mature_end - HOUR, mature_end)
    big_orders = sum(bands[name].orders for name in BANDS[2:5])
    big_settled = sum(bands[name].settled for name in BANDS[2:5])
    summary = " ".join(
        [
            f"Completed-hour buyer orders={sum(m.orders for m in bands.values())};",
            f"big >=200000 settled={big_settled}/{big_orders}; ledger_fresh={fresh};",
            f"unknown budgets={bands['unknown'].orders}. Mature cohort is separate.",
            *[
                ";".join(
                    [
                        f"{name}: orders={m.orders},settled={m.settled}",
                        f"rate={m.settled_rate}",
                        f"provisional/order={m.provisional_surplus_per_order}",
                        f"no_stock={m.no_stock_rate},timeouts={m.shop_timeouts}",
                        f"retries={m.retries},retry_settled={m.retry_settled}",
                    ]
                )
                for name, m in bands.items()
                if m.orders
            ],
            *[
                f"mature {name}: surplus/order={m.mature_surplus_per_order};"
                for name, m in mature.items()
                if m.orders
            ],
        ]
    )
    report = BuyerReport(
        generated_at=observed,
        cohort_start=end - HOUR,
        cohort_end=end,
        ledger_latest_at=latest,
        ledger_fresh=fresh,
        authoritative_order_times=authoritative,
        local_observation_times=local,
        bands=bands,
        mature_bands=mature,
        mature_cohort_start=mature_end - HOUR,
        mature_cohort_end=mature_end,
        summary=summary,
        notes=(
            "Local observation times are not authoritative issuance times.",
            "Goods mature after 6h+180s with fresh ledger; provisional is not final.",
            "Pending surplus stays null; expired failed payments contribute zero.",
            "Goods use settlement value or catalog list price by market rule.",
            "Food without settlement value is unknown.",
            "Local metadata and ledger snapshots are not cross-source atomic.",
            "Missing order IDs are counted as unidentified; rates then stay null.",
        ),
    )
    encoded = report.model_dump_json(indent=2)
    temporary = output_path.with_suffix(".tmp")
    _ = temporary.write_text(encoded + "\n")
    _ = temporary.replace(output_path)
    with output_path.with_suffix(".jsonl").open("a") as history:
        _ = history.write(json.dumps(report.model_dump()) + "\n")
    return report
