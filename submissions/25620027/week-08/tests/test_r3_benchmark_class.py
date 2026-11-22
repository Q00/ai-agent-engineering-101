"""Benchmarks select graded Class identities and never relabel legacy tables."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

import pytest

from r3.benchmark import build, compact, leaders, load_day_ago
from r3.ledger_db import LedgerDB
from r3.standing import board_rows
from tests.test_r3_class_standing import class_board

if TYPE_CHECKING:
    from pathlib import Path


def test_class_benchmark_uses_saved_and_book_not_currency(tmp_path: Path) -> None:
    # Given: raw cash and surplus leaders differ from the graded leaders.
    board = class_board()
    board["shops"][1]["profit"] = 999999
    board["buyers"][0]["surplus"] = 999999
    board["buyers"].append(
        {"name": "alternate-a", "owner": {"login": "a"}, "saved": 0.99, "deals": 1}
    )

    # When: the benchmark reads this public board.
    table = build(board, LedgerDB(tmp_path / "ledger.sqlite"), {}, {}, ours="us")

    # Then: only Class representative identities compete by scored metrics.
    shops = board_rows(table, "shops")
    buyers = board_rows(table, "buyers")
    assert [row["name"] for row in shops] == ["us", "b", "a"]
    assert [row["name"] for row in buyers] == ["a", "b", "us"]
    assert table["scope"] == "class"
    assert table["rank_metrics"] == {"shops": "book", "buyers": "saved"}
    assert shops[0]["book"] == 300
    assert shops[0]["stars_rank"] == 4.5
    assert buyers[2]["surplus"] == 999999


def test_ties_share_rank_and_selection_preserves_board() -> None:
    # Given: two buyers have the same scored rate.
    board = class_board()
    board["buyers"][2]["saved"] = 0.3
    original = copy.deepcopy(board)

    # When: the Class leaders are selected.
    rows = leaders(board, "buyers", "saved", "us")

    # Then: ties have the same rank and no temporary keys reach the shared board.
    assert [(row["name"], row["_rank"]) for row in rows] == [
        ("a", 1),
        ("b", 1),
        ("us", 3),
    ]
    assert board == original


@pytest.mark.parametrize("missing", ["players", "buyer", "owner", "saved"])
def test_unknown_membership_or_metric_never_falls_back_to_all(missing: str) -> None:
    # Given: the Class cohort or a ranked value cannot be established.
    board = class_board()
    targets = {
        "players": board,
        "buyer": board["players"][0],
        "owner": board["players"][0],
        "saved": board["buyers"][0],
    }
    del targets[missing][missing]

    # When: buyer leaders are requested.
    rows = leaders(board, "buyers", "saved", "us")

    # Then: an incomplete cohort is not ranked as zero or replaced by All.
    assert rows == []


def test_owner_mismatch_invalidates_class_identity() -> None:
    # Given: a representative name is served by a different owner.
    board = class_board()
    board["buyers"][0]["owner"] = {"login": "not-me"}

    # When: the Class cohort is selected.
    rows = leaders(board, "buyers", "saved", "us")

    # Then: the missing selected identity leaves the benchmark unknown.
    assert rows == []


def test_own_role_names_follow_owner_and_ignore_case(tmp_path: Path) -> None:
    # Given: the owner's selected buyer and shop have different names.
    board = class_board()
    board["buyers"][0]["name"] = "our-other-name"
    board["players"][0]["buyer"] = {"name": "our-other-name"}
    board["buyers"][0]["owner"] = {"login": " mE "}

    # When: the benchmark is requested using the shop name.
    table = build(board, LedgerDB(tmp_path / "ledger.sqlite"), {}, {}, ours="us")

    # Then: the selected buyer is still marked as ours.
    ours = [row["name"] for row in board_rows(table, "buyers") if row["ours"]]
    assert ours == ["our-other-name"]


def test_stock_only_shop_is_ranked_but_unsettled_buyer_is_not() -> None:
    # Given: a shop purchased inventory but its owner has not settled a buy.
    board = class_board()
    board["shops"][0].update(deals=0, stock_units=2)
    board["buyers"][0]["deals"] = 0

    # When: each role's Class leaders are selected.
    shops = leaders(board, "shops", "book", "us")
    buyers = leaders(board, "buyers", "saved", "us")

    # Then: participation matches the corresponding graded role.
    assert [row["name"] for row in shops] == ["us", "b", "a"]
    assert [row["name"] for row in buyers] == ["a", "b"]


def test_top_ten_boundary_keeps_ties_and_our_lower_rank() -> None:
    # Given: two representatives tie at tenth, with our buyer below them.
    board = class_board()
    board["buyers"][0]["saved"] = 0.01
    board["buyers"][1]["saved"] = 0.9
    board["buyers"][2]["saved"] = 0.8
    for index, saved in enumerate([0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.05]):
        name = f"extra-{index}"
        board["buyers"].append(
            {"name": name, "owner": {"login": name}, "saved": saved, "deals": 1}
        )
        board["players"].append(
            {"student": True, "owner": {"login": name}, "buyer": {"name": name}}
        )

    # When: the benchmark keeps the top ten ranks and our own row.
    rows = leaders(board, "buyers", "saved", "us")

    # Then: the boundary tie is complete and our actual rank stays visible.
    assert [(row["name"], row["_rank"]) for row in rows[-3:]] == [
        ("extra-7", 10),
        ("extra-8", 10),
        ("us", 12),
    ]
    assert rows[-1]["_ours"] is True


def test_missing_class_data_has_explicit_status(tmp_path: Path) -> None:
    # Given: the All tables exist but Class membership is absent.
    board = class_board()
    del board["players"]

    # When: a complete benchmark is requested.
    table = build(board, LedgerDB(tmp_path / "ledger.sqlite"), {}, {}, ours="us")

    # Then: the model sees explicit uncertainty, never a substituted All table.
    assert table["status"] == "insufficient_class_data"
    assert table["role_status"] == {
        "shops": "insufficient_class_data",
        "buyers": "insufficient_class_data",
    }
    assert table["shops"] == []
    assert table["buyers"] == []


def test_legacy_cache_is_not_forwarded_as_new_class_leaders() -> None:
    # Given: a previous currency-ranked cache has no Class metadata.
    legacy = {"ts": 1000, "shops": [{"rank": 1, "name": "old-cash-leader"}]}

    # When: it is compacted for the policy model.
    table = compact(legacy)

    # Then: old ranks are withheld with explicit incompatible status.
    assert table["shops"] == []
    assert table["buyers"] == []
    assert table["status"] == "incompatible_selection"


def test_day_ago_skips_legacy_comparison(tmp_path: Path) -> None:
    # Given: the closest old row uses the incompatible currency ranking.
    latest = tmp_path / "r3-benchmark.json"
    table = build(
        class_board(), LedgerDB(tmp_path / "ledger.sqlite"), {}, {}, ours="us"
    )
    current = {**table, "ts": 900}
    legacy = {"ts": 1000, "shops": [{"rank": 1, "name": "old-cash-leader"}]}
    _ = (tmp_path / "r3-benchmark.jsonl").write_text(
        json.dumps(current) + "\n" + json.dumps(legacy) + "\n"
    )

    # When: a day-ago comparison is requested.
    previous = load_day_ago(latest, now=87400)

    # Then: the compatible row is used, not the closer legacy ranking.
    assert previous["ts"] == 900
