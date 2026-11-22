from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from r3.growth import (
    GrowthFacts,
    GrowthSale,
    GrowthStock,
    build_growth,
    summarize_growth,
)
from r3.market_files import CatalogItem, Inventory, Unit

if TYPE_CHECKING:
    from pathlib import Path

NOW = 200_000.0
HOUR = 3600.0
SHOP = "https://own-shop"


def facts(*sales: GrowthSale) -> GrowthFacts:
    return GrowthFacts(
        sales,
        {s.unit: GrowthStock(600, s.at - HOUR, s.at - HOUR) for s in sales},
        {},
        NOW,
    )


@pytest.mark.parametrize(("age", "mature"), [(6 * HOUR - 1, 0), (6 * HOUR, 1)])
def test_mature_contribution_when_six_hour_boundary(age: float, mature: int) -> None:
    # Given an identified sale with a one-hour storage hold.
    source = facts(GrowthSale("one", "sku", NOW - age, 1000))
    # When maturity is measured at the boundary.
    result = summarize_growth(source).by_sku["sku"]
    # Then immature sales cannot justify deep growth; storage rounds against profit.
    assert result.mature_sales == mature
    assert result.mature_net == (399 if mature else None)
    assert result.mature_cost == (600 if mature else None)


def test_profit_unknown_when_one_mature_unit_cost_missing() -> None:
    # Given two matured units, one of which was never observed in our stock journal.
    source = facts(*(GrowthSale(u, "sku", NOW - 7 * HOUR, 1000) for u in ("a", "b")))
    source = replace(source, stock={"a": source.stock["a"]})
    # When the SKU is summarized.
    result = summarize_growth(source).by_sku["sku"]
    # Then the second unit is a sale but never a fabricated free profitable unit.
    assert result.mature_sales == 2
    assert result.mature_net is None
    assert result.mature_cost is None
    assert result.median_hold_hours is None


def test_exposure_union_when_stock_overlaps_and_unit_disappears() -> None:
    # Given overlapping observed units and one missing unit with no settlement.
    sale = GrowthSale("sold", "sku", NOW - 900, 1000)
    source = GrowthFacts(
        [sale],
        {
            "sold": GrowthStock(600, NOW - 1800, NOW - 1800),
            "held": GrowthStock(600, NOW - 1500, NOW - 1500),
            "lost": GrowthStock(600, NOW - 5000, NOW - 5000),
        },
        {"held": "sku"},
        NOW,
    )
    # When availability is unioned over the last hour.
    result = summarize_growth(source).by_sku["sku"]
    # Then overlapping units do not multiply time and disappearance is not demand.
    assert result.sales_1h == 1
    assert result.in_stock_hours == pytest.approx(0.5)


def test_exposure_lower_bound_when_first_observation_is_late() -> None:
    # Given a current unit supplied long before the recorder first observed it.
    source = GrowthFacts(
        [], {"held": GrowthStock(600, NOW - 7200, NOW - 600)}, {"held": "sku"}, NOW
    )
    # When computing the observed lower bound.
    result = summarize_growth(source).by_sku["sku"]
    # Then unobserved availability is not claimed.
    assert result.in_stock_hours == pytest.approx(1 / 6)
    assert result.sales_1h == 0


def risk_facts(warranty: float, age: float = 3) -> GrowthFacts:
    mature = [
        GrowthSale(
            f"market-{i}",
            "sku",
            NOW - 7 * HOUR,
            1000,
            grade="C",
            own=False,
            broken_at=NOW - 5 * HOUR if i < 10 else NOW - 2 * HOUR if i < 15 else None,
        )
        for i in range(22)
    ]
    live = GrowthSale(
        "own", "sku", NOW - age * HOUR, 1000, grade="C", warranty_hours=warranty
    )
    return GrowthFacts([*mature, live], {}, {}, NOW)


def test_remaining_risk_when_four_hour_warranty_excludes_late_breaks() -> None:
    # Given the same empirical C cohort with early and late failures.
    sources = [risk_facts(4), risk_facts(6), risk_facts(4, age=4)]
    # When reserving each actual warranty independently.
    four, six, expired = [summarize_growth(s).refund_reserve for s in sources]
    # Then shorter remaining coverage has lower exposure; expired coverage has none.
    assert 0 < four < six <= 1000
    assert expired == 0


def test_remaining_risk_when_exact_unit_already_broken() -> None:
    # Given a refunded live unit alongside an unbroken repeat purchase of the SKU.
    source = risk_facts(6)
    refunded = replace(
        source.sales[-1], unit="refunded", broken_at=NOW - 60, refund=1000
    )
    source = replace(source, sales=[*source.sales, refunded])
    # When comparing future obligations against the unbroken purchase alone.
    result = summarize_growth(source).refund_reserve
    # Then the paid refund does not create another future liability or mask its twin.
    assert result == summarize_growth(risk_facts(6)).refund_reserve


def test_full_reserve_when_grade_history_unknown() -> None:
    # Given an active warranty with no mature observations of that grade.
    source = facts(
        GrowthSale("own", "sku", NOW - HOUR, 1000, grade="C", warranty_hours=4)
    )
    # When estimating future refunds.
    result = summarize_growth(source)
    # Then lack of data cannot masquerade as zero risk.
    assert result.refund_reserve == 1000


def journals(path: Path, source: GrowthFacts) -> None:
    """Create real SQLite journal fixtures with the production column contracts."""
    with closing(sqlite3.connect(path / "r3-ledger.sqlite")) as db, db:
        _ = db.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, shop TEXT, sku TEXT, data TEXT)"""
        )
        events: list[tuple[float, str, str, str, str]] = []
        for sale in source.sales:
            shop = SHOP if sale.own else "https://other"
            data = {
                "unit": sale.unit,
                "buyer": "repeat-buyer",
                "amount": sale.amount,
                "grade": sale.grade,
                "warranty_hours": sale.warranty_hours,
            }
            events.append((sale.at, "settle", shop, sale.sku, json.dumps(data)))
            if sale.broken_at is not None:
                broken = {"unit": sale.unit, "buyer": "repeat-buyer"}
                events.append(
                    (sale.broken_at, "broken", shop, sale.sku, json.dumps(broken))
                )
        events.append((source.now, "heartbeat", SHOP, "", "{}"))
        _ = db.executemany(
            "INSERT INTO rows VALUES(?,?,?,3,?,?,?)",
            [(i, *row) for i, row in enumerate(sorted(events))],
        )
    with closing(sqlite3.connect(path / "r3-stock.sqlite")) as db, db:
        _ = db.execute(
            """CREATE TABLE stock (unit TEXT PRIMARY KEY, cost INTEGER,
            supplied REAL, first_seen REAL, missing_at REAL)"""
        )
        _ = db.execute("CREATE TABLE snapshots (ts REAL, storage_rate REAL)")
        _ = db.execute(
            "INSERT INTO snapshots VALUES(?,?)", (source.now, source.storage_rate)
        )
        _ = db.executemany(
            "INSERT INTO stock VALUES(?,?,?,?,NULL)",
            [(u, s.cost, s.supplied, s.first_seen) for u, s in source.stock.items()],
        )


@pytest.mark.parametrize(("warranty", "expected_net"), [(4, 799), (6, -201)])
def test_sqlite_exact_refunds_when_buyer_repeats_sku(
    tmp_path: Path, warranty: float, expected_net: int
) -> None:
    # Given two same-buyer/SKU units; only one breaks five hours after purchase.
    source = facts(
        GrowthSale(
            "broken",
            "sku",
            NOW - 7 * HOUR,
            1000,
            warranty_hours=warranty,
            broken_at=NOW - 2 * HOUR,
        ),
        GrowthSale("sound", "sku", NOW - 7 * HOUR, 1000, warranty_hours=warranty),
    )
    journals(tmp_path, source)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }
    # When the real read-only adapter joins exact units and their original terms.
    result = build_growth(
        tmp_path, SHOP, {"sku": CatalogItem(sku="sku")}, Inventory(round=3), NOW
    )
    # Then one refund, actual hold/storage, and unchanged source databases are observed.
    assert result.available
    assert result.by_sku["sku"].mature_sales == 2
    assert result.by_sku["sku"].mature_net == expected_net
    assert result.by_sku["sku"].mature_cost == 1200
    assert result.by_sku["sku"].median_hold_hours == 1
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }


def test_adapter_unavailable_when_journals_missing(tmp_path: Path) -> None:
    # Given no journal files.
    # When evidence is requested.
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    # Then there is no new database and no authorization to grow.
    assert not result.available
    assert not result.by_sku
    assert not list(tmp_path.iterdir())


def test_adapter_unavailable_when_ledger_stale(tmp_path: Path) -> None:
    # Given valid historical journals that have stopped updating.
    journals(tmp_path, facts(GrowthSale("u", "sku", NOW - HOUR, 1000)))
    # When read after the explicit freshness limit.
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW + 181)
    # Then recent inventory cannot grant growth using stale transactions.
    assert not result.available


def test_adapter_current_stock_when_sale_history_empty(tmp_path: Path) -> None:
    # Given observed current stock but no sales.
    source = GrowthFacts(
        [], {"u": GrowthStock(600, NOW - HOUR, NOW - HOUR)}, {"u": "sku"}, NOW
    )
    journals(tmp_path, source)
    inventory = Inventory(round=3, units=(Unit(unit="u", sku="sku"),))
    # When reading the real journals.
    result = build_growth(tmp_path, SHOP, {}, inventory, NOW)
    # Then availability is observable but neither sales nor profit are invented.
    assert result.by_sku["sku"].in_stock_hours == 1
    assert result.by_sku["sku"].sales_1h == 0
    assert result.by_sku["sku"].mature_net is None


def test_unknown_profit_and_reserved_cash_when_broken_terms_missing(
    tmp_path: Path,
) -> None:
    # Given a broken mature unit with neither explicit refund nor warranty terms.
    source = facts(
        GrowthSale("u", "sku", NOW - 7 * HOUR, 1000, broken_at=NOW - 2 * HOUR)
    )
    journals(tmp_path, source)
    # When the actual unit evidence cannot resolve its refund.
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    # Then unknown losses cannot authorize growth or silently release cash.
    assert result.by_sku["sku"].mature_net is None
    assert result.refund_reserve == 1000


def test_explicit_refund_when_actual_amount_differs_from_contract(
    tmp_path: Path,
) -> None:
    # Given a recorded partial refund on a six-hour warranty.
    source = facts(
        GrowthSale(
            "u", "sku", NOW - 7 * HOUR, 1000, warranty_hours=6, broken_at=NOW - 2 * HOUR
        )
    )
    journals(tmp_path, source)
    with closing(sqlite3.connect(tmp_path / "r3-ledger.sqlite")) as db, db:
        _ = db.execute(
            "UPDATE rows SET data=? WHERE kind='broken'",
            (json.dumps({"unit": "u", "refunded": 125}),),
        )
    # When actual refunds are available in the ledger.
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    # Then observed cash wins over an inferred full refund and is not reserved again.
    assert result.by_sku["sku"].mature_net == 274
    assert result.refund_reserve == 0


def test_unavailable_when_one_unit_settled_twice() -> None:
    # Given inconsistent duplicate settlement identities.
    source = facts(
        GrowthSale("u", "sku", NOW - 7 * HOUR, 1000),
        GrowthSale("u", "sku", NOW - 8 * HOUR, 1000),
    )
    # When exact-unit evidence is summarized.
    result = summarize_growth(source)
    # Then duplicated revenue cannot trigger growth.
    assert not result.available
    assert not result.by_sku


@pytest.mark.parametrize("delay", [-1.0, 6 * HOUR + 1])
@pytest.mark.parametrize("foreign", [False, True])
def test_foreign_invalid_break_cannot_hide_own_mature_profit(
    tmp_path: Path, delay: float, *, foreign: bool
) -> None:
    source = facts(
        GrowthSale(
            "own-refund",
            "sku",
            NOW - 8 * HOUR,
            1000,
            grade="A",
            warranty_hours=72,
            broken_at=NOW - 7 * HOUR,
        ),
        GrowthSale(
            "inconsistent",
            "other-sku",
            NOW - 7 * HOUR,
            1000,
            own=not foreign,
            grade="C",
            warranty_hours=72,
            broken_at=NOW - 7 * HOUR + delay,
        ),
        GrowthSale("valid-market", "market", NOW - 8 * HOUR, 1000, own=False),
    )
    journals(tmp_path, source)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    assert result.available is foreign
    if foreign:
        assert set(result.by_sku) == {"sku"}
        assert result.by_sku["sku"].mature_sales == 1
        assert result.by_sku["sku"].mature_net == -601
        assert result.by_sku["sku"].mature_cost == 600
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
    }


def test_invalid_foreign_break_cannot_inflate_risk_sample(tmp_path: Path) -> None:
    source = facts(
        GrowthSale("pending", "sku", NOW - HOUR, 1000, grade="C", warranty_hours=72),
        *(
            GrowthSale(
                f"market-{i}",
                "sku",
                NOW - 8 * HOUR,
                1000,
                own=False,
                grade="C",
                warranty_hours=72,
            )
            for i in range(19)
        ),
        GrowthSale(
            "invalid-market",
            "sku",
            NOW - 7 * HOUR,
            1000,
            own=False,
            grade="C",
            warranty_hours=72,
            broken_at=NOW - HOUR + 1,
        ),
    )
    journals(tmp_path, source)
    result = build_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    assert result.available
    assert result.refund_reserve == 1000
    assert result.by_sku["sku"].mature_sales == 0
    assert result.by_sku["sku"].mature_net is None
