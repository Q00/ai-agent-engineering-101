"""A costly FIFO goods unit must not hide an in-band unit of the same grade."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest

from market_a2a.remote import Remote
from r3.market_files import Unit
from r3.policy import Policy, SellerPolicy
from r3.pricing import quote
from r3.seller import A_LOW_FLOOR, HOLD_SECONDS, Deal, R3Seller, StockView
from tests.r3_stock import CURTAIN, LAMP, MILK, unit, write_market
from tests.test_r3_seller import shop

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1793948400.0
PRICE = SellerPolicy(max_ask_ratio=0.76, accept_margin=0.18, min_margin=0.05)


def seller(tmp_path: Path, rows: list[dict[str, object]]) -> R3Seller:
    """Construct the real opening path without models or market writes."""
    folder = write_market(tmp_path / ".market", rows)
    return R3Seller(
        "test",
        signer=None,
        stock=StockView(folder),
        policy_file=tmp_path / "policy.json",
        events=None,
        emit=lambda _m: None,
    )


def goods(
    *, cost: int = 140000, grade: str = "A", number: int = 2
) -> dict[str, object]:
    return unit(CURTAIN, number, cost=cost, grade=grade, now=NOW)


def old_goods() -> dict[str, object]:
    return unit(CURTAIN, 1, cost=200000, now=NOW, supplied_hours_ago=2)


def test_opening_uses_same_grade_unit_that_fits_policy_cap(tmp_path: Path) -> None:
    older, cheaper = old_goods(), goods()
    engine = seller(tmp_path, [older, cheaper])
    deal = engine.opening("new", CURTAIN, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.unit.unit == cheaper["unit"]
    assert deal.quote.ask == round(236900 * 0.76)
    assert deal.quote.floor == round(140000 * 1.05)
    assert deal.quote.accept_at == round(140000 * 1.18)
    assert deal.unit.grade == "A"
    assert deal.unit.cert == cheaper["cert"]


@pytest.mark.parametrize("reservation", ["open", "committed"])
def test_price_alternative_never_reuses_reserved_unit(
    tmp_path: Path,
    reservation: str,
) -> None:
    cheaper = goods()
    engine = seller(tmp_path, [old_goods(), cheaper])
    candidate = Unit.model_validate(cheaper)
    if reservation == "committed":
        engine.committed[candidate.unit] = NOW
    else:
        priced = quote(candidate, NOW, None, PRICE)
        engine.open["other"] = Deal(candidate, None, priced, priced.ask, opened_at=NOW)
    deal = engine.opening("new", CURTAIN, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.unit.unit == old_goods()["unit"]


@pytest.mark.parametrize(
    "alternative", ["different_grade", "different_sku", "over_cap"]
)
def test_fallback_preserves_sku_grade_and_cost_floor(
    tmp_path: Path,
    alternative: str,
) -> None:
    other = {
        "different_grade": goods(grade="B"),
        "different_sku": unit(LAMP, 2, cost=20000, now=NOW),
        "over_cap": goods(cost=190000),
    }[alternative]
    engine = seller(tmp_path, [old_goods(), other])
    deal = engine.opening("new", CURTAIN, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    # 11/13 owner rule: the same SKU and grade's lowest-cost unit goes first.
    expected = other if alternative == "over_cap" else old_goods()
    assert deal.unit.unit == expected["unit"]
    assert deal.quote.accept_at >= deal.quote.floor > deal.unit.cost


def test_lowest_cost_goods_unit_goes_first(tmp_path: Path) -> None:
    older = unit(CURTAIN, 1, cost=145000, now=NOW, supplied_hours_ago=2)
    cheaper = goods(cost=130000)
    engine = seller(tmp_path, [older, cheaper])
    deal = engine.opening("new", CURTAIN, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.unit.unit == cheaper["unit"]


def test_food_with_time_left_goes_lowest_cost_first(tmp_path: Path) -> None:
    first = unit(MILK, 1, cost=19000, now=NOW, shelf_left_ratio=0.8)
    later = unit(MILK, 2, cost=11000, now=NOW, shelf_left_ratio=0.9)
    deal = seller(tmp_path, [first, later]).opening("food", MILK, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.unit.unit == later["unit"]


def test_cheap_a_pilot_cap_and_margin_are_preserved(tmp_path: Path) -> None:
    older = unit(LAMP, 1, cost=30000, now=NOW, supplied_hours_ago=2)
    cheaper = unit(LAMP, 2, cost=20000, now=NOW)
    deal = seller(tmp_path, [older, cheaper]).opening("cheap", LAMP, NOW, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.unit.unit == cheaper["unit"]
    assert deal.quote.ask == round(40000 * PRICE.cheap_max_ask_ratio)
    # 11/16: grade A goods are never accepted under cost x A_REFUND_FLOOR.
    a_line = round(20000 * A_LOW_FLOOR)  # the lamp lists under 500k
    assert deal.quote.accept_at == max(round(20000 * 1.08), a_line)
    # The floor moves only when the A line lifts the accept line.
    lifted = a_line > round(20000 * 1.08)
    assert deal.quote.floor == (a_line if lifted else round(20000 * 1.05))


def test_endgame_goods_still_select_an_affordable_same_grade_unit(
    tmp_path: Path,
) -> None:
    # 11/17 13:0x: 14:00 KST sat within the conftest wall-clock hour (endgame pressure
    # zeroed); 30 min before the close is far from any deploy-time wall clock today.
    ending = 1794927540.0 - 1800  # ROUND_END (23:59 KST) - 30 min
    older = old_goods()
    deal = seller(tmp_path, [older, goods()]).opening("end", CURTAIN, ending, PRICE)
    assert deal is not None
    assert deal.unit is not None
    assert deal.quote.endgame > 0
    assert deal.unit.unit == goods()["unit"]
    assert deal.quote.ask <= round(236900 * PRICE.max_ask_ratio)
    assert deal.quote.floor == round(deal.unit.cost * (1 + PRICE.min_margin))


def test_signed_receipt_and_commit_use_selected_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("r3.seller.load_policy", lambda _p: Policy(seller=PRICE))
    now = time.time()
    older = unit(CURTAIN, 1, cost=200000, now=now, supplied_hours_ago=2)
    cheaper = unit(CURTAIN, 2, cost=140000, now=now)

    async def run() -> None:
        async with (
            shop(tmp_path, [older, cheaper]) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            opened = await remote.send(endpoint, CURTAIN)
            assert opened.asking_price() == round(236900 * 0.76)
            done = await remote.send(endpoint, str(opened.asking_price()), opened)
            assert done.status.state == "TASK_STATE_COMPLETED"
            receipt = done.artifacts[0].parts[0].data
            assert receipt is not None
            assert receipt["unit"] == cheaper["unit"]
            assert receipt["claim"] == "A"
            second = await remote.send(endpoint, CURTAIN)
            assert second.asking_price() == round(200000 * 1.18)

    anyio.run(run)


def test_a_walked_away_buyer_frees_its_unit_after_the_hold(tmp_path: Path) -> None:
    held = goods()
    engine = seller(tmp_path, [held])
    candidate = Unit.model_validate(held)
    priced = quote(candidate, NOW, None, PRICE)
    engine.open["walked"] = Deal(candidate, None, priced, priced.ask, opened_at=NOW)
    assert engine.opening("next", CURTAIN, NOW + 10, PRICE) is None
    later = engine.opening("later", CURTAIN, NOW + HOLD_SECONDS + 1, PRICE)
    assert later is not None
    assert later.unit is not None
    assert later.unit.unit == held["unit"]
    assert HOLD_SECONDS <= 120
