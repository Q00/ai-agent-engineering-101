"""Class uses its own roster, role percentiles and official total, never All."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from r3.manager import CLASS_SCORING_RULES, PROMPT, TACTICAL_PROMPT, Manager
from r3.standing import SCORING_VERSION, standing

if TYPE_CHECKING:
    from pathlib import Path

Board = dict[str, list[dict[str, object]]]


def class_board() -> Board:
    shops: list[dict[str, object]] = [
        {
            "name": "us",
            "owner": {"login": "ME"},
            "deals": 10,
            "book": 300,
            "revenue": 50,
            "stars_rank": 4.5,
            "all": 0.333,
        },
        {
            "name": "a",
            "owner": {"login": "A"},
            "deals": 10,
            "book": 100,
            "revenue": 90,
            "stars_rank": 4.9,
        },
        {
            "name": "b",
            "owner": {"login": "B"},
            "deals": 10,
            "book": 200,
            "revenue": 70,
            "stars_rank": 4.8,
        },
        {
            "name": "outsider",
            "owner": {"login": "OTHER"},
            "deals": 10,
            "book": 900,
            "revenue": 990,
            "stars_rank": 5.0,
        },
    ]
    buyers: list[dict[str, object]] = [
        {
            "name": "us",
            "owner": {"login": "me"},
            "deals": 5,
            "saved": 0.1,
            "forum": 0.7,
            "posts": 9,
            "all": 0.277,
        },
        {
            "name": "a",
            "owner": {"login": "a"},
            "deals": 9,
            "saved": 0.3,
            "forum": 0.5,
            "posts": 1,
        },
        {
            "name": "b",
            "owner": {"login": "b"},
            "deals": 7,
            "saved": 0.2,
            "forum": 0.6,
            "posts": 2,
        },
        {
            "name": "outsider",
            "owner": {"login": "other"},
            "deals": 12,
            "saved": 0.9,
            "forum": 0.1,
            "posts": 20,
        },
    ]
    players: list[dict[str, object]] = [
        {
            "player": "Me",
            "owner": {"login": "Me"},
            "student": True,
            "shop": {"name": "us"},
            "buyer": {"name": "us"},
            "total": 0.7,
            "student_total": 0.81,
            "student_shop": 0.5,
            "student_buyer": 0.31,
        },
        {
            "player": "A",
            "owner": {"login": "a"},
            "student": True,
            "shop": {"name": "a"},
            "buyer": {"name": "a"},
            "total": 1.0,
            "student_total": 1.1,
        },
        {
            "player": "B",
            "owner": {"login": "b"},
            "student": True,
            "shop": {"name": "b"},
            "buyer": {"name": "b"},
            "total": 0.8,
            "student_total": 1.0,
        },
        {"player": "Other", "owner": {"login": "other"}, "total": 1.6},
    ]
    return {"shops": shops, "buyers": buyers, "players": players}


def parts(table: dict[str, object], side: str) -> dict[str, dict[str, object]]:
    return {
        str(c["metric"]): c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["side"] == side
    }


def test_default_is_official_class_and_all_is_separate_diagnostic() -> None:
    table = standing(class_board(), "us")
    assert table["scope"] == "class"
    assert (table["rank"], table["total"], table["of"]) == (3, 0.81, 3)
    assert table["gap_to_first"] == 0.29
    assert table["gap_to_next_up"] == 0.19
    assert table["buyer_reconstruction"] == {
        "status": "matched",
        "official": 0.31,
        "rebuilt": 0.31,
    }
    assert table["shop_reconstruction"] == {
        "status": "matched",
        "official": 0.5,
        "rebuilt": 0.5,
    }
    assert parts(table, "shop")["book"]["of"] == 3
    assert parts(table, "buyer")["saved"]["points_per_rank"] == 0.25
    diagnostic = cast("dict[str, object]", table["all_diagnostic"])
    assert (diagnostic["scope"], diagnostic["total"], diagnostic["rank"]) == (
        "all",
        0.7,
        4,
    )
    assert standing(class_board(), "us", scope="all") == diagnostic


def test_house_or_nonstudent_movement_does_not_change_class_components() -> None:
    data = class_board()
    before = standing(data, "us")
    data["shops"][3].update(book=-999, revenue=0, stars_rank=1)
    data["buyers"][3].update(saved=-1, forum=1, deals=1, posts=0)
    data["players"][3]["total"] = 0
    after = standing(data, "us")
    for key in [
        "components",
        "rank",
        "total",
        "shop_reconstruction",
        "buyer_reconstruction",
    ]:
        assert after[key] == before[key]
    assert after["all_diagnostic"] != before["all_diagnostic"]


def test_shop_and_buyer_names_can_differ_within_one_student_player() -> None:
    data = class_board()
    data["buyers"][0]["name"] = "my-buyer"
    data["players"][0]["buyer"] = {"name": "my-buyer"}
    shop_view = standing(data, "us")
    buyer_view = standing(data, "my-buyer")
    assert shop_view == buyer_view
    assert len(parts(buyer_view, "shop")) == 3
    assert len(parts(buyer_view, "buyer")) == 4


def test_old_board_does_not_silently_grade_all_as_class() -> None:
    data = class_board()
    for p in data["players"]:
        _ = p.pop("student", None)
    table = standing(data, "us")
    assert table["class_roster_status"] == "unavailable"
    assert table["components"] == []
    assert "rank" not in table
    assert "total" not in table
    assert cast("dict[str, object]", table["all_diagnostic"])["total"] == 0.7


def test_unknown_student_owner_withholds_class_roster() -> None:
    data = class_board()
    _ = data["players"][1].pop("owner")
    table = standing(data, "us")
    assert table["class_roster_status"] == "unavailable"
    assert table["components"] == []
    assert "total" not in table


@pytest.mark.parametrize(
    ("field", "side"), [("student_shop", "shop"), ("student_buyer", "buyer")]
)
@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf"), 0.99])
def test_missing_or_mismatched_class_role_does_not_borrow_all_score(
    field: str, side: str, value: object
) -> None:
    data = class_board()
    data["players"][0][field] = value
    table = standing(data, "us")
    assert not parts(table, side)
    assert table["total"] == 0.81


@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf")])
def test_invalid_class_total_is_unknown_not_zero_or_all(value: object) -> None:
    data = class_board()
    data["players"][0]["student_total"] = value
    table = standing(data, "us")
    assert "total" not in table
    assert "rank" not in table
    assert parts(table, "buyer")["saved"]["value"] == 0.1


def test_missing_other_class_total_withholds_rank_but_keeps_own_official_total() -> (
    None
):
    data = class_board()
    _ = data["players"][1].pop("student_total")
    table = standing(data, "us")
    assert table["total"] == 0.81
    assert "rank" not in table


def test_class_stock_only_shop_and_ties_use_its_own_denominator() -> None:
    data = class_board()
    data["shops"][1].update(deals=0, stock_units=1, book=300)
    data["players"][0]["student_shop"] = 0.375
    table = standing(data, "us")
    book = parts(table, "shop")["book"]
    assert (book["of"], book["rank"], book["points"]) == (3, 1, 0.375)


def test_class_history_never_compares_legacy_or_all_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="us",
        shop_name="us",
    )
    try:

        def cached(_key: str, _url: str, _ttl: float) -> object:
            return class_board()

        monkeypatch.setattr(manager, "cached_json", cached)
        monkeypatch.setattr("r3.manager.time.time", lambda: 10000)
        history = [
            {"ts": 5000, "scope": "all", "rank": 2, "total": 1.5},
            {"ts": 6000, "rank": 3, "total": 1.4},
            {"ts": 6100, "scope": "class", "rank": 3, "total": 1.4},
            {
                "ts": 6150,
                "scope": "class",
                "scoring_version": SCORING_VERSION,
                "scoring_verified": False,
                "rank": 2,
                "total": 1.3,
            },
        ]
        manager.events.set_value("standing_history", json.dumps(history))
        table = manager.standing()
        assert "about_an_hour_ago" not in table
        history.append(
            {
                "ts": 6200,
                "scope": "class",
                "scoring_version": SCORING_VERSION,
                "scoring_verified": True,
                "rank": 4,
                "total": 0.8,
            }
        )
        manager.events.set_value("standing_history", json.dumps(history))
        assert manager.standing()["about_an_hour_ago"] == {
            "scope": "class",
            "scoring_version": SCORING_VERSION,
            "scoring_verified": True,
            "rank": 4,
            "total": 0.8,
        }
        data = class_board()
        data["players"][0]["student_buyer"] = 0.4
        monkeypatch.setattr(manager, "cached_json", lambda *_args: data)
        assert "about_an_hour_ago" not in manager.standing()
    finally:
        manager.http.close()


@pytest.mark.parametrize("prompt", [PROMPT, TACTICAL_PROMPT])
def test_model_roles_receive_class_account_review_and_accept_timing_contract(
    prompt: str,
) -> None:
    assert CLASS_SCORING_RULES in prompt
    assert "student-only Players Class" in prompt
    assert "one mean per GitHub account" in prompt
    assert "starts that more expensive acceptance earlier" in prompt
    assert "their reviews weigh the same as a mattress" not in prompt


@pytest.mark.parametrize("side", ["shops", "buyers"])
@pytest.mark.parametrize("strong", [False, True])
def test_class_ignores_unselected_same_owner_accounts(
    side: str, *, strong: bool
) -> None:
    data = class_board()
    expected = standing(data, "us")
    alias: dict[str, object] = {
        "name": "a-alternate",
        "owner": {"login": "A"},
        "deals": 999 if strong else 1,
        "book": 999 if strong else 1,
        "revenue": 999 if strong else 1,
        "stars_rank": 5 if strong else 1,
        "saved": 0.99 if strong else 0.01,
        "forum": 0.99 if strong else 0.01,
        "posts": 999 if strong else 1,
    }
    data[side].append(alias)
    actual = standing(data, "us")
    for key in (
        "components",
        "shop_reconstruction",
        "buyer_reconstruction",
        "total",
        "rank",
    ):
        assert actual[key] == expected[key]
    # All remains the official, separate diagnostic over its original population.
    assert actual["all_diagnostic"] == standing(data, "us", scope="all")


@pytest.mark.parametrize(("side", "role"), [("shops", "shop"), ("buyers", "buyer")])
def test_missing_selected_role_row_withholds_derived_components(
    side: str, role: str
) -> None:
    data = class_board()
    data[side] = [row for row in data[side] if row["name"] != "a"]
    table = standing(data, "us")
    check = cast("dict[str, object]", table[f"{role}_reconstruction"])
    assert check["status"] == "unavailable"
    assert parts(table, role) == {}
    assert table["total"] == 0.81


def test_selected_role_owner_mismatch_does_not_join_another_student() -> None:
    data = class_board()
    data["buyers"][1]["owner"] = {"login": "b"}
    table = standing(data, "us")
    check = cast("dict[str, object]", table["buyer_reconstruction"])
    assert check["status"] == "unavailable"
    assert parts(table, "buyer") == {}
    assert table["total"] == 0.81


def test_class_projection_does_not_mutate_public_all_rows() -> None:
    data = class_board()
    before = json.dumps(data, sort_keys=True)
    _ = standing(data, "us")
    assert json.dumps(data, sort_keys=True) == before


def test_missing_other_role_reference_withholds_denominator() -> None:
    data = class_board()
    _ = data["players"][1].pop("buyer")
    table = standing(data, "us")
    check = cast("dict[str, object]", table["buyer_reconstruction"])
    assert check["status"] == "unavailable"
    assert table["total"] == 0.81


def test_conflicting_owners_for_selected_identity_withholds_denominator() -> None:
    data = class_board()
    data["players"][2]["buyer"] = {"name": "a"}
    table = standing(data, "us")
    check = cast("dict[str, object]", table["buyer_reconstruction"])
    assert check["status"] == "unavailable"
    assert parts(table, "buyer") == {}
    assert table["total"] == 0.81
