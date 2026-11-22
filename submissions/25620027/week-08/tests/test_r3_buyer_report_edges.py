from __future__ import annotations

import fcntl
import sqlite3
import tracemalloc
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from r3.buyer_report_models import MATURITY, ReportPayload
from r3.market_files import Order
from tests.test_r3_buyer_report import NOW, append, report, setup

if TYPE_CHECKING:
    from pathlib import Path


def test_duplicate_unit_is_idempotent_but_two_units_flagged(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, _ = paths
    events.record_order_metadata(
        Order(order="o", budget=200000), observed_at=NOW - 1800
    )
    sale = ReportPayload(order="o", unit="u", item="sku", amount=200000)
    append(ledger, 1, NOW - 1500, "settle", sale)
    append(ledger, 2, NOW - 1490, "settle", sale)
    append(ledger, 3, NOW, "tick", ReportPayload())
    assert report(paths).bands["200000-499999"].duplicate_settlements == 0
    append(ledger, 4, NOW, "settle", sale.model_copy(update={"unit": "u2"}))
    actual = report(paths).bands["200000-499999"]
    assert actual.duplicate_settlements == 1
    assert actual.provisional_surplus is None


def test_maturity_boundary_and_food_unknown(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, _ = paths
    issued = (NOW - MATURITY) // 3600 * 3600 - 1800
    events.record_order_metadata(Order(order="o", budget=200000), observed_at=issued)
    append(
        ledger,
        1,
        NOW - MATURITY + 1,
        "settle",
        ReportPayload(order="o", unit="u", item="sku", amount=200000),
    )
    append(ledger, 2, NOW, "tick", ReportPayload())
    assert report(paths).mature_bands["200000-499999"].mature_surplus is None
    assert report(paths, NOW + 1).mature_bands["200000-499999"].mature_surplus == 100000
    events.record_order_metadata(
        Order(order="food", budget=20, kind="food"), observed_at=issued
    )
    append(
        ledger,
        3,
        NOW - 25000,
        "settle",
        ReportPayload(order="food", unit="f", item="unknown", amount=10),
    )
    assert report(paths).mature_bands["<50000"].mature_surplus is None


def test_large_source_stream_keeps_memory_bounded(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    events, ledger, _, output = paths
    events.record_order_metadata(
        Order(order="o", budget=200000), observed_at=NOW - 1800
    )
    with closing(sqlite3.connect(ledger)) as db, db:
        _ = db.executemany(
            "INSERT INTO rows VALUES (?,?,?,?,3,?)",
            (
                (
                    i,
                    NOW - 1800,
                    "order",
                    "me",
                    ReportPayload(order=f"old-{i}").model_dump_json(),
                )
                for i in range(1, 10001)
            ),
        )
    append(ledger, 10001, NOW, "tick", ReportPayload())
    tracemalloc.start()
    actual = report(paths)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 5 * 1024 * 1024
    assert actual.bands["unknown"].orders == 10000
    with closing(sqlite3.connect(output.with_suffix(".sqlite"))) as db:
        assert db.execute("SELECT seq FROM report_cursor").fetchone() == (10001,)
        assert db.execute("SELECT count(*) FROM issued").fetchone() == (10000,)
    assert report(paths).bands == actual.bands


def test_publication_lock_refuses_overlap(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    with paths[3].with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            _ = report(paths)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    assert not paths[3].exists()


def test_unidentified_orders_keep_denominator_and_suppress_rate(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    append(paths[1], 1, NOW - 1800, "order", ReportPayload())
    append(paths[1], 2, NOW, "tick", ReportPayload())
    actual = report(paths).bands["unknown"]
    assert actual.orders == 1
    assert actual.unidentified_orders == 1
    assert actual.settled_rate is None
    assert actual.mature_surplus_per_order is None
