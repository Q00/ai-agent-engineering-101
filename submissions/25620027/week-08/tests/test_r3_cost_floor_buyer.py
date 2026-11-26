"""A buyer whose low per-buyer stars mean we are lifting is sold to at cost."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.policy import SellerPolicy
from r3.seller import R3Seller, StockView, cost_floor_buyer, no_c_buyer
from tests.r3_stock import KIMCHI, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1793948400.0
PRICE = SellerPolicy(max_ask_ratio=0.76, accept_margin=0.18, min_margin=0.05)
Q00 = (
    f"I want to buy {KIMCHI} (buyer: q00). I'm a regular here, this is my "
    "visit #244. Can I take it on credit and pay later?"
)


def seller(tmp_path: Path, cost: int) -> R3Seller:
    folder = write_market(tmp_path / ".market", [unit(KIMCHI, 1, cost=cost, now=NOW)])
    engine = R3Seller(
        "test",
        signer=None,
        stock=StockView(folder),
        policy_file=tmp_path / "policy.json",
        events=None,
        emit=lambda _m: None,
    )
    engine.market_median = lambda _sku, _grade=None: 31200.0  # type: ignore[method-assign]
    return engine


def test_named_buyer_is_read_from_the_opening() -> None:
    assert cost_floor_buyer(Q00)
    assert not cost_floor_buyer(f"I want to buy {KIMCHI} (buyer: usagi).")
    assert not cost_floor_buyer(f"I want to buy {KIMCHI}")
    # 11/16 18:5x: the shelf form (ovotnwjd-maker, food only) accepts down to cost.
    assert cost_floor_buyer(
        f"I want to buy x ({KIMCHI}). It must last 49 hours (need 49 h of shelf life)."
    )
    assert not cost_floor_buyer(f"I want to buy x ({KIMCHI}). It must last 49 hours.")


def test_cost_floor_buyer_is_accepted_down_to_cost_never_below(
    tmp_path: Path,
) -> None:
    deal = seller(tmp_path, 23660).opening("t", Q00, NOW, PRICE)
    plain = seller(tmp_path / "b", 23660).opening(
        "t", f"I want to buy {KIMCHI}", NOW, PRICE
    )
    assert deal is not None
    assert plain is not None
    assert deal.quote.accept_at == 23660
    assert plain.quote.accept_at > 23660
    assert deal.quote.ask == plain.quote.ask


def test_no_c_buyers_are_read_from_the_opening() -> None:
    assert no_c_buyer(f"I want to buy x ({KIMCHI}), it must last at least 120 hours")
    assert no_c_buyer('I want to buy x. policy: {"min_hours_left": 17.05}')
    assert not no_c_buyer(f"I want to buy x ({KIMCHI})")


def test_no_c_buyer_gets_no_grade_c_offer(tmp_path: Path) -> None:
    folder = write_market(
        tmp_path / ".market", [unit(KIMCHI, 1, cost=20000, now=NOW, grade="C")]
    )
    engine = R3Seller(
        "test",
        signer=None,
        stock=StockView(folder),
        policy_file=tmp_path / "policy.json",
        events=None,
        emit=lambda _m: None,
    )
    engine.market_median = lambda _sku, _grade=None: 31200.0  # type: ignore[method-assign]
    text = f"I want to buy x ({KIMCHI}), it must last at least 10 hours"
    assert engine.opening("t", text, NOW, PRICE) is None
    assert engine.opening("t2", f"I want to buy x ({KIMCHI})", NOW, PRICE) is not None


def test_tailored_buyers_16h() -> None:
    gom = f"I want to buy 침구 세트 ({KIMCHI})."
    yeon = f"I want to buy 소파 ({KIMCHI}), grade A or B only, at least 50 hours"
    kc = (
        f"I want to buy a {KIMCHI}. This order accepts grade A, B or C and needs "
        "at least 30 hours of shelf life left. Please include a market certificate."
    )
    assert cost_floor_buyer(gom)
    assert cost_floor_buyer(yeon)
    assert not cost_floor_buyer(f"I want to buy 침구 세트 ({KIMCHI})")
    assert not cost_floor_buyer(f"I want to buy x ({KIMCHI}). Must last 540 hours.")
    assert no_c_buyer(kc)
    assert not no_c_buyer(gom)
    assert no_c_buyer(
        f"I want to buy x ({KIMCHI}). The unit must last at least 72 hours."
    )
    assert not no_c_buyer(
        f"I want to buy x ({KIMCHI}). Must last at least 540.0 hours."
    )
