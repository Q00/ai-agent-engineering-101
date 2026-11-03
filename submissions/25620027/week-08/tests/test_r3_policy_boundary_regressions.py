"""Restock and tactical loops keep policy boundaries under active demand."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
import pytest

from r3 import model_client
from r3.manager import Manager
from r3.market_files import (
    CatalogItem,
    DemandRow,
    read_catalog,
    read_demand,
    read_inventory,
)
from r3.model_client import ModelClient
from r3.policy import RestockPolicy, load_policy
from r3.restock import DemandSignal, RestockState, plan
from tests.r3_fake_model import FakeModel
from tests.r3_stock import KIMCHI, LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("replenish", [False, True])
def test_restock_clips_each_batch_to_remaining_unit_capacity(
    tmp_path: Path,
    *,
    replenish: bool,
) -> None:
    now = time.time()
    folder = write_market(tmp_path / ".market", [unit(LAMP, 1, now=now)])
    state = RestockState(last_sold_at={KIMCHI: now}) if replenish else RestockState()
    policy = RestockPolicy(max_units=2, units_per_sku=4, replenish_qty=4)
    inventory = read_inventory(folder)
    lines, _ = plan(
        inventory, read_catalog(folder), read_demand(folder), policy, state, now
    )
    assert sum(int(str(line["qty"])) for line in lines) == 1
    assert (
        len(inventory.units) + sum(int(str(line["qty"])) for line in lines)
        <= policy.max_units
    )


@pytest.mark.parametrize("replenish", [False, True])
@pytest.mark.parametrize("with_signal", [False, True])
def test_zero_food_allocation_applies_to_replenishment_and_demand_signals(
    tmp_path: Path,
    *,
    replenish: bool,
    with_signal: bool,
) -> None:
    now = time.time()
    folder = write_market(tmp_path / ".market", [])
    state = RestockState(last_sold_at={KIMCHI: now}) if replenish else RestockState()
    signal = DemandSignal({}, {KIMCHI: 1000}) if with_signal else None
    lines, _ = plan(
        read_inventory(folder),
        read_catalog(folder),
        read_demand(folder),
        RestockPolicy(food_share=0),
        state,
        now,
        signal,
    )
    assert lines
    assert all(line["sku"] != KIMCHI for line in lines)


def test_hot_food_demand_does_not_override_the_food_line_allowance() -> None:
    from r3.market_files import Inventory  # noqa: PLC0415

    catalog = {
        f"food-{n}": CatalogItem(
            sku=f"food-{n}",
            category=f"food-category-{n}",
            list_price=10_000,
            kind="food",
            shelf_hours=720,
        )
        for n in range(3)
    }
    demand = tuple(
        DemandRow(category=i.category, kind="food") for i in catalog.values()
    )
    lines, _ = plan(
        Inventory(round=3, balance=5_000_000),
        catalog,
        demand,
        RestockPolicy(food_share=0.1),
        RestockState(),
        time.time(),
        DemandSignal({i.category: 100 for i in catalog.values()}, {}),
    )
    assert len(lines) == 1


@pytest.mark.parametrize("reply", [None, "{broken", {"changes": []}])
def test_failed_tactical_reply_does_not_turn_into_an_automatic_policy_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reply: object,
) -> None:
    monkeypatch.setattr(model_client, "AsyncOpenAI", FakeModel.script(reply))
    manager = Manager(
        tmp_path,
        "https://market.test",
        shop_url="http://us",
        buyer_name="me",
        shop_name="me",
    )
    client = ModelClient("fake", events=manager.events, quota=None)
    monkeypatch.setattr(manager, "client", lambda: client)
    monkeypatch.setattr(
        manager,
        "tactical_summary",
        lambda: {
            "triggers": [
                {
                    "name": "buyer_walkaway",
                    "field": "buyer.quick_accept_ratio",
                    "direction": 1,
                    "why": "4 of 10 orders agreed",
                }
            ]
        },
    )
    before = load_policy(manager.policy_file)
    try:
        assert anyio.run(manager.tactical) is None
        assert load_policy(manager.policy_file) == before
        assert manager.events.loop_runs(time.time() - 60)[-1]["outcome"] == "failed"
    finally:
        manager.http.close()


def test_valid_empty_tactical_proposal_still_allows_the_bounded_code_nudge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_client,
        "AsyncOpenAI",
        FakeModel.script({"changes": {}, "reason": "hold"}),
    )
    manager = Manager(
        tmp_path,
        "https://market.test",
        shop_url="http://us",
        buyer_name="me",
        shop_name="me",
    )
    client = ModelClient("fake", events=manager.events, quota=None)
    monkeypatch.setattr(manager, "client", lambda: client)
    monkeypatch.setattr(
        manager,
        "tactical_summary",
        lambda: {
            "triggers": [
                {
                    "name": "buyer_walkaway",
                    "field": "buyer.quick_accept_ratio",
                    "direction": 1,
                    "why": "4 of 10 orders agreed",
                }
            ]
        },
    )
    before = load_policy(manager.policy_file)
    try:
        published = anyio.run(manager.tactical)
        assert published is not None
        assert published.buyer.quick_accept_ratio > before.buyer.quick_accept_ratio
        assert manager.events.loop_runs(time.time() - 60)[-1]["outcome"] == "nudged"
    finally:
        manager.http.close()


def test_tactical_limit_counts_valid_changes_instead_of_the_first_input_fields(
    tmp_path: Path,
) -> None:
    manager = Manager(
        tmp_path,
        "https://market.test",
        shop_url="http://us",
        buyer_name="me",
        shop_name="me",
    )
    try:
        kept = manager.filter_tactical(
            {
                "seller.markup_a": 0.9,
                "unknown": 1,
                "buyer.first_offer_ratio": True,
                "seller.concession": 0.31,
                "buyer.quick_accept_ratio": 0.73,
                "seller.accept_margin": 0.16,
                "seller.clearance_shelf_ratio": 0.21,
            },
            "fake",
            "bounded proposals",
        )
        assert kept == {
            "seller.concession": 0.31,
            "buyer.quick_accept_ratio": 0.73,
            "seller.accept_margin": 0.16,
        }
    finally:
        manager.http.close()
