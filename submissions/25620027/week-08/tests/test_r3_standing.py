"""The graded standing is rebuilt from the public board, component by component."""

from __future__ import annotations

from r3.standing import standing


def board() -> dict[str, object]:
    shops = [
        {"name": "us", "deals": 10, "profit": 300, "revenue": 50, "stars": 4.5},
        {"name": "a", "deals": 10, "profit": 100, "revenue": 90, "stars": 4.9},
        {"name": "b", "deals": 10, "profit": 200, "revenue": 70, "stars": 4.8},
        {"name": "idle", "deals": 0, "profit": 999, "revenue": 999, "stars": 5.0},
    ]
    buyers = [
        {"name": "us", "deals": 5, "surplus": 10, "forum": 0.7, "posts": 9},
        {"name": "a", "deals": 9, "surplus": 30, "forum": 0.5, "posts": 1},
        {"name": "b", "deals": 7, "surplus": 20, "forum": 0.6, "posts": 2},
    ]
    players = [
        {"player": "me", "shop": {"name": "us"}, "total": 1.2},
        {"player": "pa", "shop": {"name": "a"}, "total": 1.5},
        {"player": "pb", "shop": {"name": "b"}, "total": 1.0},
        {"player": "house", "house": True, "shop": {"name": "h"}, "total": 2.0},
    ]
    return {"shops": shops, "buyers": buyers, "players": players}


def test_standing_ranks_each_component_among_participants_with_a_deal() -> None:
    table = standing(board(), "us")
    # Then: idle shops and house players are left out, as the market does.
    assert (table["rank"], table["of"]) == (2, 3)
    assert table["gap_to_next_up"] == 0.3
    parts = {(c["side"], c["metric"]): c for c in table["components"]}  # pyright: ignore[reportGeneralTypeIssues]
    profit = parts[("shop", "profit")]
    assert (profit["rank"], profit["percentile"], profit["role"]) == (
        1,
        1.0,
        "strength",
    )
    stars = parts[("shop", "stars")]
    assert (stars["rank"], stars["percentile"], stars["role"]) == (3, 0.0, "weakness")
    assert stars["points_left"] == 0.2
    assert stars["gap_up"] == 0.3
    # And: the component with the most points left comes first.
    first = table["components"][0]  # pyright: ignore[reportIndexIssue]
    assert (first["side"], first["metric"]) == ("buyer", "surplus")


def test_unknown_board_or_name_is_empty_not_zero() -> None:
    assert standing(None, "us") == {}
    assert standing(board(), "nobody")["components"] == []
