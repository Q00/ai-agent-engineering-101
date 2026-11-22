"""The graded standing is rebuilt from the public board, component by component."""

from __future__ import annotations

from typing import cast

import pytest

from r3.manager import PROMPT, TACTICAL_PROMPT
from r3.standing import standing


def board() -> dict[str, list[dict[str, object]]]:
    shops: list[dict[str, object]] = [
        {
            "name": "us",
            "deals": 10,
            "profit": -100,
            "book": 300,
            "stock_units": 0,
            "all": 0.5,
            "revenue": 50,
            "stars": 4.5,
            "stars_rank": 4.5,
        },
        {
            "name": "a",
            "deals": 10,
            "profit": 100,
            "book": 100,
            "stock_units": 0,
            "revenue": 90,
            "stars": 4.9,
            "stars_rank": 4.9,
        },
        {
            "name": "b",
            "deals": 10,
            "profit": 200,
            "book": 200,
            "stock_units": 0,
            "revenue": 70,
            "stars": 4.8,
            "stars_rank": 4.8,
        },
        {
            "name": "idle",
            "deals": 0,
            "profit": 999,
            "book": 999,
            "stock_units": 0,
            "revenue": 999,
            "stars": 5.0,
            "stars_rank": 5.0,
        },
    ]
    buyers: list[dict[str, object]] = [
        {
            "name": "us",
            "deals": 5,
            "surplus": 10,
            "saved": 0.1,
            "all": 0.31,
            "forum": 0.7,
            "posts": 9,
        },
        {
            "name": "a",
            "deals": 9,
            "surplus": 30,
            "saved": 0.3,
            "all": 0.75,
            "forum": 0.5,
            "posts": 1,
        },
        {
            "name": "b",
            "deals": 7,
            "surplus": 20,
            "saved": 0.2,
            "all": 0.53,
            "forum": 0.6,
            "posts": 2,
        },
    ]
    players: list[dict[str, object]] = [
        {"player": "me", "shop": {"name": "us"}, "total": 1.2},
        {"player": "pa", "shop": {"name": "a"}, "total": 1.5},
        {"player": "pb", "shop": {"name": "b"}, "total": 1.0},
        {"player": "house", "house": True, "shop": {"name": "h"}, "total": 2.0},
    ]
    return {"shops": shops, "buyers": buyers, "players": players}


def test_standing_ranks_each_component_among_participants_with_a_deal() -> None:
    table = standing(board(), "us", scope="all")
    # Then: idle shops and house players are left out, as the market does.
    assert (table["rank"], table["of"]) == (2, 3)
    assert table["gap_to_next_up"] == 0.3
    parts = {
        (c["side"], c["metric"]): c
        for c in cast("list[dict[str, object]]", table["components"])
    }
    book = parts[("shop", "book")]
    assert (book["rank"], book["percentile"], book["role"]) == (
        1,
        1.0,
        "strength",
    )
    stars = parts[("shop", "stars_rank")]
    assert (stars["rank"], stars["percentile"], stars["role"]) == (3, 0.0, "weakness")
    assert stars["points_left"] == 0.2
    assert stars["gap_up"] == 0.3
    # And: the component with the most points left comes first.
    first = cast("list[dict[str, object]]", table["components"])[0]
    assert (first["side"], first["metric"]) == ("buyer", "saved")


def test_unknown_board_or_name_is_empty_not_zero() -> None:
    assert standing(None, "us", scope="all") == {}
    assert standing(board(), "nobody", scope="all")["components"] == []


def test_stock_only_purchaser_enters_shop_denominator() -> None:
    data = board()
    shops = data["shops"]
    shops[3]["stock_units"] = 1
    shops[0]["all"] = 0.333
    table = standing(data, "us", scope="all")
    part = next(
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["metric"] == "book"
    )
    assert part["of"] == 4
    assert part["rank"] == 2
    assert (
        cast("dict[str, object]", table["shop_reconstruction"])["status"] == "matched"
    )


def test_tied_book_scores_share_mid_percentile() -> None:
    data = board()
    data["shops"][0]["book"] = 200
    data["shops"][0]["all"] = 0.375
    table = standing(data, "us", scope="all")
    part = next(
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["metric"] == "book"
    )
    assert part["rank"] == 1
    assert part["percentile"] == 0.75
    assert part["points"] == 0.375


def test_adjusted_review_field_is_graded_instead_of_raw_stars() -> None:
    data = board()
    data["shops"][0]["stars"] = 5.0
    table = standing(data, "us", scope="all")
    part = next(
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["metric"] == "stars_rank"
    )
    assert part["rank"] == 3
    assert part["points"] == 0


def test_missing_book_withholds_shop_rebuild_without_cash_substitution() -> None:
    data = board()
    del data["shops"][0]["book"]
    table = standing(data, "us", scope="all")
    assert not [
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["side"] == "shop"
    ]
    assert (
        cast("dict[str, object]", table["shop_reconstruction"])["status"]
        == "unavailable"
    )
    assert table["total"] == 1.2


def test_mismatched_official_all_withholds_derived_shop_components() -> None:
    data = board()
    data["shops"][0]["all"] = 0.99
    table = standing(data, "us", scope="all")
    assert not [
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["side"] == "shop"
    ]
    assert (
        cast("dict[str, object]", table["shop_reconstruction"])["status"]
        == "unverified"
    )
    assert table["total"] == 1.2


def test_null_public_forum_shares_zero_rank_as_observed_on_official_boards() -> None:
    data = board()
    data["buyers"][0]["forum"] = None
    data["buyers"][0]["all"] = 0.1
    data["buyers"][1]["forum"] = 0
    data["buyers"][2]["forum"] = 1
    table = standing(data, "us", scope="all")
    part = next(
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["metric"] == "forum"
    )
    assert part["percentile"] == 0.25
    assert part["points"] == 0


def test_manager_graded_target_names_book_and_keeps_model_locks() -> None:
    for prompt in (PROMPT, TACTICAL_PROMPT):
        assert "shop book value at weight 0.5" in prompt
        assert "shop profit at weight 0.5" not in prompt
        assert "seller.* and restock.*" in prompt


def buyer_parts(table: dict[str, object]) -> list[dict[str, object]]:
    return [
        c
        for c in cast("list[dict[str, object]]", table["components"])
        if c["side"] == "buyer"
    ]


def test_buyer_saved_rate_overrides_currency_surplus_ranking() -> None:
    data = board()
    data["buyers"][0]["surplus"] = 999999
    table = standing(data, "us", scope="all")
    assert table["buyer_reconstruction"] == {
        "status": "matched",
        "rebuilt": 0.31,
        "official": 0.31,
    }
    parts = {str(c["metric"]): c for c in buyer_parts(table)}
    assert set(parts) == {"saved", "forum", "deals", "posts"}
    assert parts["saved"]["points"] == 0
    assert parts["saved"]["points_left"] == 0.5
    assert parts["saved"]["rank"] == 3
    assert parts["saved"]["gap_up"] == 0.1
    assert sum(float(str(p["points"])) for p in parts.values()) == pytest.approx(0.31)


def test_forum_raw_rating_points_do_not_change_when_peers_cross_ours() -> None:
    data = board()
    before = next(
        p
        for p in buyer_parts(standing(data, "us", scope="all"))
        if p["metric"] == "forum"
    )
    data["buyers"][1]["forum"] = 0.9
    data["buyers"][2]["forum"] = 0.8
    after = next(
        p
        for p in buyer_parts(standing(data, "us", scope="all"))
        if p["metric"] == "forum"
    )
    assert (before["rank"], after["rank"]) == (1, 3)
    assert before["points"] == after["points"] == 0.21
    assert after["points_left"] == 0.09
    assert after["score_basis"] == "value"
    assert after["percentile_is_diagnostic"] is True
    assert after["points_per_rank"] == 0
    assert after["points_per_0_01_value"] == 0.003
    assert before["role"] == "weakness"  # rank 1 does not make 0.7 a high rating


@pytest.mark.parametrize("rating", [-0.01, 1.01])
def test_out_of_range_forum_rating_withholds_points(rating: float) -> None:
    data = board()
    data["buyers"][1]["forum"] = rating
    result = standing(data, "us", scope="all")
    assert buyer_parts(result) == []
    assert result["buyer_reconstruction"] == {"status": "unavailable"}
    assert result["scoring_verified"] is False
    assert result["total"] == 1.2


def test_legacy_forum_score_is_withheld_but_total_stays_authoritative() -> None:
    data = board()
    data["buyers"][0]["all"] = 0.4  # captured legacy percentile formula
    result = standing(data, "us", scope="all")
    assert result["buyer_reconstruction"] == {
        "status": "unverified",
        "rebuilt": 0.31,
        "official": 0.4,
    }
    assert buyer_parts(result) == []
    assert result["total"] == 1.2
    assert result["scoring_verified"] is False


@pytest.mark.parametrize("rating", [0.0, 1.0])
def test_forum_endpoints_keep_raw_value_not_peer_percentile(rating: float) -> None:
    data = board()
    data["buyers"][0].update(forum=rating, all=0.1 + 0.3 * rating)
    result = standing(data, "us", scope="all")
    part = next(p for p in buyer_parts(result) if p["metric"] == "forum")
    assert part["points"] == pytest.approx(0.3 * rating)
    assert result["scoring_verified"] is True


def test_both_models_receive_patch35_value_and_irreversible_book_cost() -> None:
    for prompt in (PROMPT, TACTICAL_PROMPT):
        assert "Patch 3.5 uses the forum rating itself" in prompt
        assert "unit * (N - 1)^2" in prompt
        assert "A missing unit is unknown, not free" in prompt
        assert "All ranks are percentiles" not in prompt


@pytest.mark.parametrize("row", [0, 1])
@pytest.mark.parametrize("metric", ["saved", "forum", "deals", "posts"])
def test_missing_buyer_input_withholds_components(row: int, metric: str) -> None:
    data = board()
    del data["buyers"][row][metric]
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"]
        == "unavailable"
    )
    assert (table["rank"], table["total"]) == (2, 1.2)


@pytest.mark.parametrize(
    "value", [None, "bad", float("nan"), float("inf"), -float("inf")]
)
@pytest.mark.parametrize("metric", ["saved", "deals", "posts"])
@pytest.mark.parametrize("row", [0, 1])
def test_nonfinite_buyer_input_withholds_components(
    row: int, metric: str, value: object
) -> None:
    data = board()
    data["buyers"][row][metric] = value
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"]
        == "unavailable"
    )
    assert (table["rank"], table["total"]) == (2, 1.2)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "bad"])
def test_nonfinite_forum_is_not_public_null(value: object) -> None:
    data = board()
    data["buyers"][1]["forum"] = value
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"]
        == "unavailable"
    )


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "bad"])
def test_unavailable_official_buyer_all_withholds_rebuild(value: object) -> None:
    data = board()
    data["buyers"][0]["all"] = value
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"]
        == "unavailable"
    )
    assert (table["rank"], table["total"]) == (2, 1.2)


def test_mismatched_buyer_all_withholds_points_but_preserves_players() -> None:
    data = board()
    data["buyers"][0]["all"] = 0.9
    table = standing(data, "us", scope="all")
    assert table["buyer_reconstruction"] == {
        "status": "unverified",
        "rebuilt": 0.31,
        "official": 0.9,
    }
    assert not buyer_parts(table)
    assert (table["rank"], table["total"]) == (2, 1.2)


@pytest.mark.parametrize("currency", [1.0001, 1000000])
def test_legacy_currency_saved_is_not_rate_even_when_rank_matches(
    currency: float,
) -> None:
    data = board()
    # Retain the rate order and official ALL: rank equality cannot prove units.
    for i, row in enumerate(data["buyers"]):
        row["saved"] = currency * [1, 3, 2][i]
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"]
        == "unavailable"
    )
    assert (table["rank"], table["total"]) == (2, 1.2)


@pytest.mark.parametrize("saved", [0.0, -0.1, -2.0])
def test_zero_negative_saved_remains_rankable_without_lower_clamp(saved: float) -> None:
    data = board()
    data["buyers"][0]["saved"] = saved
    table = standing(data, "us", scope="all")
    part = next(c for c in buyer_parts(table) if c["metric"] == "saved")
    assert (part["value"], part["rank"], part["points"]) == (saved, 3, 0)
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"] == "matched"
    )


def test_saved_ties_share_mid_percentile() -> None:
    data = board()
    data["buyers"][0]["saved"] = 0.2
    data["buyers"][0]["all"] = 0.435
    table = standing(data, "us", scope="all")
    part = next(c for c in buyer_parts(table) if c["metric"] == "saved")
    assert (part["rank"], part["percentile"], part["points"]) == (2, 0.25, 0.125)


def test_single_buyer_and_nonparticipating_row_use_official_denominator() -> None:
    data = board()
    data["buyers"] = [data["buyers"][0], {"name": "idle", "deals": 0}]
    data["buyers"][0].update(saved=1.0, all=0.91)
    table = standing(data, "us", scope="all")
    assert len(buyer_parts(table)) == 4
    assert all(c["of"] == 1 and c["points_per_rank"] == 0 for c in buyer_parts(table))
    assert (
        cast("dict[str, object]", table["buyer_reconstruction"])["status"] == "matched"
    )


def test_saved_small_gaps_keep_four_decimal_board_precision() -> None:
    data = board()
    data["buyers"][0]["saved"] = 0.2307
    data["buyers"][1]["saved"] = 0.2311
    data["buyers"][2]["saved"] = 0.2308
    part = next(
        c
        for c in buyer_parts(standing(data, "us", scope="all"))
        if c["metric"] == "saved"
    )
    assert (part["value"], part["next_up_value"], part["gap_up"]) == (
        0.2307,
        0.2308,
        0.0001,
    )


def test_missing_official_buyer_all_withholds_rebuild() -> None:
    data = board()
    del data["buyers"][0]["all"]
    table = standing(data, "us", scope="all")
    assert not buyer_parts(table)
    assert table["buyer_reconstruction"] == {"status": "unavailable"}
    assert (table["rank"], table["total"]) == (2, 1.2)
