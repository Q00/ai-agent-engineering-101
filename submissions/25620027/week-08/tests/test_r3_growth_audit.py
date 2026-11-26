"""Real SQLite and CLI checks for the read-only hourly audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from r3.accounting import STOCK_FILE, StockJournal
from r3.growth_audit import AuditRequest, AuditSnapshot, app, audit, collect
from r3.ledger_db import LEDGER_FILE, LedgerDB
from r3.policy import Policy, PolicyHistory
from tests.test_r3_accounting import cover, event, snapshot, unit

if TYPE_CHECKING:
    from pathlib import Path

NOW = 10_000.0
SHOP = "https://our-shop"


def request(folder: Path) -> AuditRequest:
    """Use explicit source and output paths inside each isolated fixture."""
    return AuditRequest(
        run_dir=folder,
        output=folder / "audit",
        shop_url=SHOP,
        buyer_name="ours",
        release="release-one",
    )


def test_deadline_cli_expires_without_touching_sources_or_output(
    tmp_path: Path,
) -> None:
    # Given nonexistent sources and an expired explicit deadline.
    output = tmp_path / "new-output"
    # When the real CLI runs.
    result = CliRunner().invoke(
        app,
        [
            "--run-dir",
            str(tmp_path / "absent"),
            "--shop-url",
            SHOP,
            "--buyer-name",
            "ours",
            "--release",
            "r1",
            "--output",
            str(output),
            "--deadline",
            "2000-01-01T00:00:00+00:00",
        ],
    )
    # Then it reports expiration without creating either path.
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "expired"}
    assert list(tmp_path.iterdir()) == []


def test_exact_cohort_excludes_other_windows_duplicates_and_buyers(
    tmp_path: Path,
) -> None:
    # Given duplicate settlements, late orders, old orders and another buyer.
    ledger = LedgerDB(tmp_path / LEDGER_FILE)
    rows = [
        event(1, 6300, "order", order="old", buyer="ours"),
        event(2, 6500, "settle", order="old", buyer="ours"),
        event(3, 6600, "order", order="one", buyer="ours"),
        event(4, 6700, "settle", order="one", buyer="ours"),
        event(5, 6800, "settle", order="one", buyer="ours"),
        event(6, 9800, "order", order="two", buyer="ours"),
        event(7, 9900, "settle", order="two", buyer="other"),
        event(8, 9950, "settle", order="two", buyer="ours"),
        event(9, 9970, "order", order="late", buyer="ours"),
    ]
    _ = ledger.ingest(rows)
    original = ledger.path.read_bytes()
    # When the observation uses the same half-open creation/settlement cutoff.
    result = collect(request(tmp_path), NOW)
    # Then only one of two eligible orders settled, and the ledger is unchanged.
    assert (result.window_start, result.window_end) == (6400, 9940)
    assert (result.buyer.orders, result.buyer.settled, result.buyer.settled_rate) == (
        2,
        1,
        0.5,
    )
    assert ledger.path.read_bytes() == original


def test_missing_sources_publish_unknown_instead_of_zero(tmp_path: Path) -> None:
    # Given no ledger, stock history, inventory or policy.
    req = request(tmp_path)
    # When a nonexpired audit is published.
    result = audit(req, NOW)
    # Then unknown facts stay null in the actual latest artifact.
    assert result is not None
    saved = AuditSnapshot.model_validate_json((req.output / "latest.json").read_text())
    assert saved.cash is None
    assert saved.stock_cost is None
    assert saved.financial.shop_net_profit is None
    assert saved.buyer.orders is None
    assert saved.unknown_mature_skus is None
    assert saved.refund_reserve is None
    assert saved.supply_factors is None
    assert saved.held_price_points is None
    assert len(list(req.output.glob("*.json"))) == 2
    assert not (tmp_path / LEDGER_FILE).exists()


def test_records_refund_reserve_supply_and_actual_held_prices(tmp_path: Path) -> None:
    # Given real fresh journals, one immature sale, and duplicate held prices.
    inventory = snapshot(
        [
            {**unit(name, NOW - 100), "category": category, "list_price": price}
            for name, category, price in [
                ("a", "chairs", 200_000),
                ("b", "chairs", 100_000),
                ("c", "chairs", 100_000),
                ("d", "tables", 300_000),
            ]
        ],
        5_000_000,
    )
    inventory["surge"] = [{"sku": "sku", "factor": 1.5}]
    folder = tmp_path / ".market"
    folder.mkdir()
    _ = (folder / "inventory.json").write_text(json.dumps(inventory))
    stock = StockJournal(tmp_path / STOCK_FILE)
    assert stock.observe(inventory, stamp=NOW)
    ledger = LedgerDB(tmp_path / LEDGER_FILE)
    _ = ledger.ingest(
        [
            event(
                1,
                NOW - 60,
                "settle",
                unit="sold",
                sku="sku",
                amount=1000,
                grade="A",
                warranty_hours=6,
            ),
        ]
    )
    # When the existing growth API and inventory facts feed the published audit.
    result = audit(request(tmp_path), NOW)
    # Then reserve is observed and prices are sorted/deduplicated per category.
    assert result is not None
    assert result.mature_available
    assert result.refund_reserve == 1000
    assert result.supply_factors == {"sku": 1.5}
    assert result.held_price_points == {
        "chairs": [100_000, 200_000],
        "tables": [300_000],
    }


def test_cash_to_stock_transfer_is_not_operating_loss(tmp_path: Path) -> None:
    # Given a food purchase moving 13,000 from cash to stock, without a sale.
    start, end = NOW - 3600, NOW - 60
    stock = StockJournal(tmp_path / STOCK_FILE)
    ledger = LedgerDB(tmp_path / LEDGER_FILE)
    _ = ledger.ingest(
        [
            event(1, start - 1, "order", buyer="other", order="before"),
            event(2, end + 1, "order", buyer="other", order="after"),
        ]
    )
    assert stock.observe(snapshot([], 5_000_000), stamp=start)
    inventory = snapshot(
        [unit("food", start + 50, cost=13_000, kind="food", expires=20_000)], 4_987_000
    )
    assert stock.observe(inventory, stamp=end)
    cover(stock, start, end)
    folder = tmp_path / ".market"
    folder.mkdir()
    _ = (folder / "inventory.json").write_text(json.dumps(inventory))
    # When the real accounting window is used by the audit.
    result = collect(request(tmp_path), NOW)
    # Then cash and stock are distinct facts and operating profit remains zero.
    assert (result.cash, result.stock_cost, result.stock_units) == (
        4_987_000,
        13_000,
        1,
    )
    assert result.financial.shop_net_profit == 0


@pytest.mark.parametrize(
    ("release", "version"), [("release-two", 7), ("release-one", 8)]
)
def test_release_or_policy_change_flags_noncausal_comparison(
    tmp_path: Path,
    release: str,
    version: int,
) -> None:
    # Given an earlier audit with one release and a real policy version.
    req = request(tmp_path)
    _ = (tmp_path / "r3-policy.json").write_text(
        PolicyHistory(current=Policy(version=7)).model_dump_json()
    )
    first = audit(req, NOW)
    assert first is not None
    _ = (tmp_path / "r3-policy.json").write_text(
        PolicyHistory(current=Policy(version=version)).model_dump_json()
    )
    # When a later release is observed.
    second = audit(req.model_copy(update={"release": release}), NOW + 3600)
    # Then change is flagged while unavailable economics cannot produce a delta.
    assert second is not None
    assert second.comparison_confounded
    assert second.previous_observed_at == NOW
    assert second.net_profit_change is None


@pytest.mark.parametrize(("offset", "expected"), [(0, False), (1, True)])
def test_deadline_boundary_controls_all_source_and_output_access(
    tmp_path: Path, offset: int, *, expected: bool
) -> None:
    # Given a deadline exactly at, or one second after, the observation clock.
    req = request(tmp_path).model_copy(
        update={
            "deadline": datetime.fromtimestamp(NOW + offset, UTC),
        }
    )
    # When the audit uses the injected observation time.
    result = audit(req, NOW)
    # Then only the still-authorized observation publishes a snapshot.
    assert (result is not None) is expected
    assert req.output.exists() is expected


def test_stale_ledger_cannot_report_zero_operating_profit(tmp_path: Path) -> None:
    # Given complete stock coverage but ledger observations stopping too early.
    start, end = NOW - 3600, NOW - 60
    stock = StockJournal(tmp_path / STOCK_FILE)
    assert stock.observe(snapshot([], 5_000_000), stamp=start)
    assert stock.observe(snapshot([], 5_000_000), stamp=end)
    cover(stock, start, end)
    ledger = LedgerDB(tmp_path / LEDGER_FILE)
    _ = ledger.ingest([event(1, start - 1, "order", buyer="other", order="old")])
    # When an hourly observation is collected.
    result = collect(request(tmp_path), NOW)
    # Then complete inventory cannot conceal absent settlement observations.
    assert result.financial.shop_net_profit is None
    assert result.financial.financial_gaps == ("ledger_window_not_covered",)
