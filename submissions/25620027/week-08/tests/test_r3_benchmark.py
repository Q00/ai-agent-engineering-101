"""The leaders' table measures top players from the ledger and flags unearned rows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from r3.benchmark import build, compact, load_day_ago, load_latest, save
from r3.ledger_db import LedgerDB
from r3.market_files import read_catalog
from tests.r3_stock import KIMCHI, LAMP, write_market

if TYPE_CHECKING:
    from pathlib import Path


def settle(
    seq: int, buyer: str, shop: str, sku: str, amount: int, **extra: object
) -> dict[str, object]:
    return {
        "seq": seq,
        "ts": time.time() - 60,
        "kind": "settle",
        "round": 3,
        "data": {"buyer": buyer, "shop": shop, "item": sku, "amount": amount, **extra},
    }


def test_leaders_table_measures_prices_breadth_and_flags_self_trade(
    tmp_path: Path,
) -> None:
    folder = write_market(tmp_path / ".market", [])
    catalog = read_catalog(folder)
    ledger = LedgerDB(tmp_path / "ledger.sqlite")
    _ = ledger.ingest(
        [
            settle(1, "b-honest", "http://good", KIMCHI, 41600, grade="A", claim="A"),
            settle(
                2,
                "b-other",
                "http://good",
                LAMP,
                32000,
                grade="A",
                claim="A",
                warranty_hours=6,
            ),
            settle(3, "self-buyer", "http://self", LAMP, 40000, grade="A", claim="A"),
            settle(4, "self-buyer", "http://self", LAMP, 40000, grade="A", claim="A"),
            settle(5, "b-honest", "http://self", LAMP, 36000, grade="B", claim="A"),
            settle(6, "25620027", "http://good", LAMP, 30000, grade="A", claim="A"),
        ]
    )
    board = {
        "round": 3,
        "shops": [
            {"name": "Self", "profit": 900, "owner": {"login": "x"}},
            {"name": "Good", "profit": 500, "owner": {"login": "g"}},
            {"name": "House", "profit": 9999, "house": True},
            {"name": "25620027", "profit": 10, "owner": {"login": "me"}},
        ],
        "buyers": [
            {"name": "self-buyer", "saved": 50, "owner": {"login": "x"}},
            {"name": "b-honest", "saved": 900, "owner": {"login": "h"}},
            {"name": "25620027", "saved": 100, "owner": {"login": "me"}},
        ],
    }
    urls = {"Self": "http://self", "Good": "http://good", "25620027": "http://us"}
    table = build(board, ledger, catalog, urls, ours="25620027")
    shops = {r["name"]: r for r in table["shops"]}
    assert "House" not in shops  # house agents are not leaders
    assert shops["Self"]["rank"] == 1
    assert shops["Self"]["self_trade_share"] == 0.67
    assert shops["Self"]["dishonest_share"] == 0.33
    assert shops["Self"]["do_not_copy"] is True
    assert shops["Good"]["do_not_copy"] is False
    assert shops["Good"]["categories"] == 2
    assert shops["Good"]["food_share"] == 0.33
    assert shops["Good"]["median_price_to_list"] == 0.8
    # And: ticket size, grade mix, warranty share and repeat buyers are measured.
    assert shops["Good"]["median_list_price"] == 40000
    assert shops["Good"]["grade_mix"] == {"A": 1.0, "B": 0.0, "C": 0.0}
    assert shops["Good"]["warranty_share"] == 0.33  # one of three goods sales
    assert len(shops["Good"]["top_categories"]) == 2
    assert shops["Self"]["repeat_buyer_share"] == 0.0  # nobody reached five deals
    assert shops["25620027"]["ours"] is True
    buyers = {r["name"]: r for r in table["buyers"]}
    assert buyers["self-buyer"]["do_not_copy"] is True
    assert buyers["b-honest"]["shops_used"] == 2
    assert buyers["25620027"]["median_paid_to_list"] == 0.75
    assert buyers["b-honest"]["median_list_price"] == 46000
    # And: history accumulates and the day-ago lookup finds the closest entry.
    latest = tmp_path / "r3-benchmark.json"
    old = {**table, "ts": time.time() - 86400}
    save(old, latest)
    save(table, latest)
    assert load_latest(latest)["ts"] == table["ts"]
    assert load_day_ago(latest)["ts"] == old["ts"]
    assert len((tmp_path / "r3-benchmark.jsonl").read_text().splitlines()) == 2
    short = compact(table, top=1)
    assert [r["name"] for r in short["shops"]] == ["Self", "25620027"]
