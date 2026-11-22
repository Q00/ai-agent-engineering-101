"""Food quotes come down to the SKU's market median too, never below cost x1.02."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.policy import SellerPolicy
from r3.pricing import (
    FOOD_ACCEPT_SHARE,
    FOOD_ASK_SHARE,
    MARKET_ACCEPT_SHARE,
    MARKET_ASK_SHARE,
)
from r3.seller import R3Seller, StockView
from tests.r3_stock import KIMCHI, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1793948400.0
PRICE = SellerPolicy(max_ask_ratio=0.76, accept_margin=0.18, min_margin=0.05)


def seller(tmp_path: Path, cost: int, median: float | None) -> R3Seller:
    folder = write_market(tmp_path / ".market", [unit(KIMCHI, 1, cost=cost, now=NOW)])
    engine = R3Seller(
        "test",
        signer=None,
        stock=StockView(folder),
        policy_file=tmp_path / "policy.json",
        events=None,
        emit=lambda _m: None,
    )
    engine.market_median = lambda _sku, _grade=None: median  # type: ignore[method-assign]
    return engine


def test_kimchi_priced_over_the_market_comes_down_to_it(tmp_path: Path) -> None:
    # 11/13: market kimchi at 0.600 of list (52,000 -> 31,200), our cost 0.455.
    deal = seller(tmp_path, 23660, 31200.0).opening("t", KIMCHI, NOW, PRICE)
    assert deal is not None
    assert deal.quote.accept_at <= round(31200 * FOOD_ACCEPT_SHARE)
    assert deal.quote.ask <= max(deal.quote.accept_at, round(31200 * FOOD_ASK_SHARE))
    assert deal.quote.accept_at >= round(23660 * 1.02)


def test_food_never_goes_under_cost_plus_two_percent(tmp_path: Path) -> None:
    deal = seller(tmp_path, 31000, 31200.0).opening("t", KIMCHI, NOW, PRICE)
    assert deal is not None
    assert deal.quote.accept_at >= round(31000 * 1.02)


def test_food_without_market_evidence_keeps_its_quote(tmp_path: Path) -> None:
    anchored = seller(tmp_path, 23660, 31200.0).opening("t", KIMCHI, NOW, PRICE)
    plain = seller(tmp_path / "b", 23660, None).opening("t", KIMCHI, NOW, PRICE)
    assert anchored is not None
    assert plain is not None
    assert plain.quote.accept_at >= anchored.quote.accept_at


def test_food_stays_at_the_market_while_goods_go_lower() -> None:
    assert (FOOD_ACCEPT_SHARE, FOOD_ASK_SHARE) == (0.94, 0.97)
    assert MARKET_ACCEPT_SHARE < FOOD_ACCEPT_SHARE
    assert MARKET_ASK_SHARE < FOOD_ASK_SHARE
