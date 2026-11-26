"""Saved ranking and refusal memory retain safe, inexpensive candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from r3 import buyer
from r3.events import EventLedger
from r3.market_files import CatalogItem, Order, ShopEntry
from tests.r3_stock import CURTAIN, LAMP
from tests.test_r3_top5_buyer_deadline import DeadlinePeers, run_order

if TYPE_CHECKING:
    from pathlib import Path


def record_refusals(tmp_path: Path, sku: str) -> None:
    """Record independent local receipt rejections in the real event store."""
    ledger = EventLedger(tmp_path / "events.sqlite")
    for number in range(3):
        ledger.record_attempt(
            order=f"rejected-{number}",
            shop="http://first.test/",
            shop_name="first.test",
            sku=sku,
            list_price=40000,
            price=None,
            outcome="no_deal",
            reason=buyer.RECEIPT_REFUSAL + " (claim B, warranty 0 h)",
            market_round=3,
        )


@pytest.mark.parametrize("rejected_sku", [LAMP, ""])
def test_refused_or_unidentified_unit_does_not_hide_another_sku(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rejected_sku: str,
) -> None:
    # Given: one shop repeatedly returned a bad lamp or an unidentified unit.
    record_refusals(tmp_path, rejected_sku)
    peers = DeadlinePeers({"first.test": "deal"}, CURTAIN)
    # When: that shop offers a different acceptable SKU on a new order.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: the safe new SKU stays available and passes ordinary receipt checks.
    assert peers.visits == ["first.test"]
    assert outcome.receipt is not None


def test_repeated_bad_sku_is_skipped_only_at_the_affected_shop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the first shop repeatedly failed for this exact SKU.
    record_refusals(tmp_path, LAMP)
    peers = DeadlinePeers({"first.test": "deal", "second.test": "deal"}, LAMP)
    # When: another shop offers the same acceptable product.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: only the affected shop/SKU pair is excluded.
    assert peers.visits == ["second.test"]
    assert outcome.receipt is not None


def test_rivals_are_demoted_even_when_cheaper() -> None:
    # Owner 11/13 19:3x: avoid buying from the players above us and just below us.
    # Given: equally reliable shops, with the direct rival offering a lower ratio.
    order = Order(order="saved-rival", kind="goods", budget=60000, acceptable=(LAMP,))
    shops = tuple(
        ShopEntry.model_validate(
            {
                "name": name,
                "url": f"http://{name}.test",
                "offers": [{"sku": LAMP, "qty": 1}],
            }
        )
        for name in ("rival", "ordinary")
    )
    catalog = {LAMP: CatalogItem(sku=LAMP, list_price=40000, kind="goods")}
    # When: Saved ranking receives the same public competitor labels as before.
    chosen = buyer.choose(
        order,
        shops,
        catalog,
        {},
        {},
        ratios={"http://rival.test": 0.50, "http://ordinary.test": 0.60},
        rivals={"rival": "rival"},
        surplus_score=False,
    )
    # Then: the rival is demoted although it quoted a lower ratio.
    assert chosen is not None
    assert chosen.shop.name == "ordinary"
