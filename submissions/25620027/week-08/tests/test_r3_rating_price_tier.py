"""Only cheap A goods receive the rating pilot's smaller acceptance margin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from r3.endgame import ROUND_END
from r3.market_files import Unit
from r3.policy import SellerPolicy
from r3.pricing import counter, decide, quote
from r3.seller import R3Seller, StockView
from tests.r3_stock import LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

NOW = ROUND_END - 10 * 86400


def good(*, price: int = 40000, cost: int = 26000, grade: str = "A") -> Unit:
    return Unit.model_validate(
        unit(LAMP, 1, grade=grade, cost=cost, now=NOW)
    ).model_copy(update={"list_price": price})


def policy() -> SellerPolicy:
    return SellerPolicy(
        accept_margin=0.20,
        min_margin=0.05,
        max_ask_ratio=0.76,
        cheap_max_ask_ratio=0.67,
        warranty_a=72,
        warranty_b=72,
        warranty_c=72,
    )


@pytest.mark.parametrize(("price", "margin"), [(49999, 0.08), (50000, 0.20)])
def test_cheap_boundary_changes_only_acceptance_margin(
    price: int, margin: float
) -> None:
    item = good(price=price, cost=20000)
    result = quote(item, NOW, None, policy())
    assert result.accept_at == round(item.cost * (1 + margin))
    assert result.floor == 21000
    assert result.ask == round(price * (0.67 if price < 50000 else 0.76))
    assert result.ask >= result.accept_at >= result.floor >= item.cost


@pytest.mark.parametrize("grade", ["B", "C"])
def test_cheap_b_and_c_keep_original_margin(grade: str) -> None:
    item = good(grade=grade)
    result = quote(item, NOW, None, policy())
    assert result.accept_at == 31200


def test_c_and_high_special_margins_still_take_precedence() -> None:
    current = policy().model_copy(
        update={"accept_margin_c": 0.35, "accept_margin_high": 0.17}
    )
    assert quote(good(grade="C"), NOW, None, current).accept_at == 35100
    for grade in ["A", "B"]:
        assert (
            quote(
                good(price=1000000, cost=600000, grade=grade), NOW, None, current
            ).accept_at
            == 702000
        )
    # Preserve the current high rule even when custom thresholds overlap cheap.
    overlap = current.model_copy(update={"high_list_price": 30000})
    assert quote(good(), NOW, None, overlap).accept_at == 30420


def test_cheap_food_and_high_goods_do_not_receive_rating_discount() -> None:
    food = good().model_copy(update={"kind": "food", "expires_at": NOW + 86400})
    assert quote(food, NOW, 24, policy()).accept_at == 31200
    assert (
        quote(good(price=1000000, cost=600000), NOW, None, policy()).accept_at == 720000
    )


@pytest.mark.parametrize(
    ("margin", "expected"), [(0.02, 27300), (0.06, 27560), (0.08, 28080), (0.20, 28080)]
)
def test_pilot_is_ceiling_not_an_increase_to_lower_policy_margin(
    margin: float, expected: int
) -> None:
    current = policy().model_copy(update={"accept_margin": margin})
    assert quote(good(), NOW, None, current).accept_at == expected


@pytest.mark.parametrize("floor_margin", [0.09, 0.12, 0.30])
def test_configured_min_margin_overrides_pilot(floor_margin: float) -> None:
    current = policy().model_copy(update={"min_margin": floor_margin})
    result = quote(good(), NOW, None, current)
    assert result.accept_at == result.floor == round(26000 * (1 + floor_margin))
    assert result.ask >= result.floor


@pytest.mark.parametrize("cost", [1, 19, 999, 26001, 33333, 49000])
def test_rounding_and_price_caps_never_cross_floor(cost: int) -> None:
    result = quote(good(price=49999, cost=cost), NOW, None, policy())
    assert result.accept_at == max(round(cost * 1.05), round(cost * 1.08))
    assert result.ask >= result.accept_at >= result.floor >= cost
    assert isinstance(result.ask, int)
    assert isinstance(result.accept_at, int)


def test_expensive_cost_is_not_forced_down_to_cheap_ask_cap() -> None:
    item = good(cost=30000)
    result = quote(item, NOW, None, policy())
    assert result.accept_at == result.ask == 32400
    assert result.ask > round(item.list_price * 0.67)
    # Existing effective-list normalization also excludes cost >= cheap threshold.
    expensive = quote(good(cost=60000), NOW, None, policy())
    assert expensive.accept_at == 72000


def test_negotiation_only_adds_intended_acceptance_range() -> None:
    current = policy()
    result = quote(good(), NOW, None, current)
    assert result.accept_at == 28080
    assert decide(28080, result.ask, result.accept_at, 0, current) == ("accept", 28080)
    assert decide(28079, result.ask, result.accept_at, 0, current)[0] == "counter"
    assert decide(28079, result.ask, result.accept_at, current.max_rounds, current) == (
        "reject",
        0,
    )
    assert decide(31200, result.ask, result.accept_at, 0, current) == ("accept", 31200)
    assert counter(result.ask, result.accept_at, current) >= result.accept_at


def test_policy_keys_prices_and_warranty_are_not_mutated() -> None:
    current = policy()
    before = current.model_dump()
    _ = quote(good(), NOW, None, current)
    assert current.model_dump() == before
    assert current.max_ask_ratio == 0.76
    assert current.cheap_max_ask_ratio == 0.67
    assert [current.warranty(g) for g in ["A", "B", "C"]] == [72, 72, 72]
    assert not any("rating" in key for key in before)


def test_legacy_opening_stays_on_existing_fixed_reserve(tmp_path: Path) -> None:
    folder = write_market(tmp_path / ".market", [], market_round=2)
    seller = R3Seller(
        "Test",
        signer=None,
        stock=StockView(folder),
        policy_file=tmp_path / "policy.json",
        events=None,
        emit=lambda _m: None,
    )
    deal = seller.opening("task", "lamp", NOW, policy())
    assert deal is not None
    assert deal.ask == 110
    assert deal.quote.accept_at == 100
    assert deal.unit is None
