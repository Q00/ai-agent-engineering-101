"""The fast buyer ranks stocked shops by surplus and evidence, then closes in turns."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from r3 import buyer as fast
from r3.market_files import Order, ShopEntry, read_order, read_shops
from tests.r3_stock import KIMCHI, LAMP, MILK, write_market

if TYPE_CHECKING:
    from pathlib import Path


class SellerPeer:
    """A2A peer with a fixed ask that accepts offers at or above an accept line."""

    def __init__(self, ask: int, accept_at: int, sku: str) -> None:
        """Fix the ask and the lowest accepted offer for one SKU."""
        self.ask, self.accept_at, self.sku = ask, accept_at, sku
        self.offers: list[int] = []
        self.counter_rounds = 0

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer the card, then quote, counter or complete with a receipt."""
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "name": "Peer",
                    "skills": [],
                    "supportedInterfaces": [
                        {
                            "url": str(request.url.copy_with(path="/rpc")),
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        payload = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        message = TypeAdapter(dict[str, JsonValue]).validate_python(
            TypeAdapter(dict[str, JsonValue]).validate_python(payload["params"])[
                "message"
            ]
        )
        parts = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
            message["parts"]
        )
        text = str(parts[0]["text"])
        task: dict[str, JsonValue] = {"id": "t-1", "contextId": "c-1"}
        if "taskId" not in message:
            task["status"] = {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {
                    "parts": [
                        {"text": f"X: asking {self.ask}. Send your offer as a number."}
                    ],
                    "metadata": {"ask": self.ask, "sku": self.sku},
                },
            }
        else:
            offer = int(text)
            self.offers.append(offer)
            if offer >= self.accept_at:
                task["status"] = {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {"parts": [{"text": f"Deal at {offer}."}]},
                }
                task["artifacts"] = [
                    {
                        "parts": [
                            {
                                "data": {
                                    "shop": "Peer",
                                    "item": self.sku,
                                    "price": offer,
                                    "task": "t-1",
                                    "unit": "u-1",
                                    "claim": "A",
                                    "sig_seller": "sig",
                                }
                            }
                        ]
                    }
                ]
            else:
                self.counter_rounds += 1
                new_ask = max(self.accept_at, self.ask - 2000 * self.counter_rounds)
                task["status"] = {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {
                        "parts": [{"text": f"Counteroffer: {new_ask}."}],
                        "metadata": {"ask": new_ask},
                    },
                }
        return httpx2.Response(200, json={"result": {"task": task}})


@pytest.fixture
def market(tmp_path: Path) -> Path:
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "o-1",
                "item": "김치",
                "category": "식품>김치/반찬>김치",
                "budget": 60000,
                "acceptable": [KIMCHI, MILK],
                "kind": "food",
                "use_within_hours": 24,
            }
        ),
        encoding="utf-8",
    )
    _ = (folder / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "Milk Shop",
                    "url": "http://milk.test",
                    "reviews": [],
                    "offers": [{"sku": MILK, "name": "milk", "qty": 3}],
                },
                {
                    "name": "Kimchi Shop",
                    "url": "http://kimchi.test",
                    "reviews": [],
                    "offers": [{"sku": KIMCHI, "name": "kimchi", "qty": 1}],
                },
                {
                    "name": "Lamp Shop",
                    "url": "http://lamp.test",
                    "reviews": [],
                    "offers": [{"sku": LAMP, "name": "lamp", "qty": 1}],
                },
            ]
        ),
        encoding="utf-8",
    )
    return folder


def test_buyer_prefers_the_highest_list_price_acceptable_sku_and_closes_in_turns(
    market: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the kimchi shop asks 49,400 and accepts 34,000+ (cost 33,800 * ~1.0).
    peer = SellerPeer(ask=49400, accept_at=34000, sku=KIMCHI)
    monkeypatch.setattr(
        fast,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    logs: list[str] = []
    outcome = anyio.run(
        partial(
            fast.buy,
            market / "shops.json",
            "김치",
            60000,
            market / "order.json",
            logs.append,
            folder=market,
            policy_file=market.parent / "p.json",
            events_file=market.parent / "e.sqlite",
        )
    )
    # Then: the lamp shop (not acceptable) and milk (lower list price) lose to kimchi.
    assert outcome.shop == "http://kimchi.test"
    assert outcome.receipt is not None
    assert outcome.agreed_price == peer.offers[-1]
    assert 34000 <= outcome.agreed_price <= 52000 * 0.95
    assert peer.offers[0] == int(52000 * 0.60)
    assert len(peer.offers) <= 4
    # And: the accept policy protects the surplus; no budget appears in text.
    line = json.loads(fast.result_line(outcome))
    assert line["accept"] == {"cert": True, "min_hours_left": 25.0}
    assert "60000" not in line["review"]
    assert line["stars"] == 4


def test_buyer_quick_accepts_a_cheap_ask_and_records_refusals_against_a_shop(
    market: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the kimchi shop refused payment earlier for a missing certificate.
    _ = (market / "payments.jsonl").write_text(
        json.dumps(
            {
                "order": "o-0",
                "shop": "http://kimchi.test",
                "item": KIMCHI,
                "price": 30000,
                "paid": False,
                "reason": "no certificate, and the policy asks for one",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    peer = SellerPeer(ask=15000, accept_at=15000, sku=MILK)
    monkeypatch.setattr(
        fast,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    outcome = anyio.run(
        partial(
            fast.buy,
            market / "shops.json",
            "김치",
            60000,
            market / "order.json",
            lambda _m: None,
            folder=market,
            policy_file=market.parent / "p.json",
            events_file=market.parent / "e.sqlite",
        )
    )
    # Then: the refused shop is avoided and the cheap ask (65% of list) is taken at
    # once.
    assert outcome.shop == "http://milk.test"
    assert peer.offers == [15000]
    assert outcome.agreed_price == 15000


def test_no_acceptable_stock_returns_no_deal_without_network(
    market: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid() -> httpx2.AsyncClient:
        pytest.fail("No shop should be contacted")

    monkeypatch.setattr(fast, "create_client", forbid)
    order = read_order(market / "order.json").model_copy(
        update={"acceptable": ("nope",)}
    )
    _ = (market / "order.json").write_text(order.model_dump_json(), encoding="utf-8")
    outcome = anyio.run(
        partial(
            fast.buy,
            market / "shops.json",
            "김치",
            60000,
            market / "order.json",
            lambda _m: None,
            folder=market,
            policy_file=market.parent / "p.json",
            events_file=market.parent / "e.sqlite",
        )
    )
    assert outcome.receipt is None
    assert outcome.reason == "no_stock"
    assert (
        json.loads(fast.result_line(outcome))["shop"]
        == read_shops(market / "shops.json")[0].url
    )


def test_receipt_validation_rejects_wrong_item_price_or_missing_signature() -> None:
    order = Order(order="o", item="김치", budget=100, acceptable=(KIMCHI,))
    shop = ShopEntry(name="k", url="http://k.test")
    choice = fast.Choice(shop, KIMCHI, "k", 100, "r", 1.0)
    signed: dict[str, object] = {"item": KIMCHI, "price": 50, "sig_seller": "s"}
    assert fast.valid_receipt({**signed, "item": MILK}, order, choice, 50)
    assert fast.valid_receipt(signed, order, choice, 60)
    assert fast.valid_receipt({**signed, "price": 150}, order, choice, 150)
    assert fast.valid_receipt({"item": KIMCHI, "price": 50}, order, choice, 50)
    assert fast.valid_receipt({**signed, "price": 50.5}, order, choice, 50)
    assert fast.valid_receipt(signed, order, choice, 50) is None


def test_late_in_the_order_budget_any_ask_within_the_cap_is_accepted(
    market: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a seller whose ask stays above the quick-accept line but under the cap,
    # and a clock that says most of the order budget is already gone.
    peer = SellerPeer(ask=45000, accept_at=45000, sku=KIMCHI)
    monkeypatch.setattr(
        fast,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    ticks = iter([0.0, 0.0])  # buy() and negotiate() start; later reads are late

    class LateClock:
        """Only the buyer module sees this clock; the event loop keeps the real one."""

        @staticmethod
        def monotonic() -> float:
            return next(ticks, 100.0)

    monkeypatch.setattr(fast, "time", LateClock)
    outcome = anyio.run(
        partial(
            fast.buy,
            market / "shops.json",
            "김치",
            60000,
            market / "order.json",
            lambda _m: None,
            folder=market,
            policy_file=market.parent / "p.json",
            events_file=market.parent / "e.sqlite",
        )
    )
    # Then: one turn, the ask taken as offered, still under the 0.95*list cap.
    assert peer.offers == [45000]
    assert outcome.agreed_price == 45000


def test_failing_shops_cool_down_and_exploration_spreads_orders(
    tmp_path: Path,
) -> None:
    import random  # noqa: PLC0415 - test only

    from r3.events import EventLedger  # noqa: PLC0415 - test only
    from r3.policy import BuyerPolicy  # noqa: PLC0415 - test only

    events = EventLedger(tmp_path / "events.sqlite")
    # Given: a shop whose last three attempts all failed (unsigned receipts).
    for n in range(3):
        events.record_attempt(
            order=f"o{n}",
            shop="http://willow.test",
            shop_name="willow",
            sku=LAMP,
            list_price=100,
            price=None,
            outcome="no_deal",
            reason="receipt is not signed",
            elapsed=1.0,
        )
    streaks = events.failure_streaks(24)
    policy = BuyerPolicy()
    # Then: it sits out the cooldown; with no cooldown it gets its retry.
    assert fast.cooling_shops(streaks, policy) == {"http://willow.test"}
    no_wait = policy.model_copy(update={"fail_cooldown_minutes": 0.0})
    assert fast.cooling_shops(streaks, no_wait) == frozenset()
    # And: a paid R3 success resets the streak; a mere agreement does not.
    events.record_attempt(
        order="o9",
        shop="http://willow.test",
        shop_name="willow",
        sku=LAMP,
        list_price=100,
        price=80,
        outcome="agreed",
        reason="",
        elapsed=1.0,
    )
    from r3.market_files import PaymentLine  # noqa: PLC0415 - test only

    assert fast.cooling_shops(events.failure_streaks(24), policy)
    events.sync_payments(
        [PaymentLine(order="o9", shop="http://willow.test", price=80, paid=True)]
    )
    assert events.failure_streaks(24) == {}
    assert events.price_ratios(24) == {"http://willow.test": 0.8}
    # Given: three good shops in Round 2 (no stock lists); one sold to us cheaper.
    shops = tuple(ShopEntry(name=n, url=f"http://{n}.test") for n in "abc")
    order = Order(item="lamp", budget=150)
    # When: exploration is off, the top-ranked shop always wins.
    picks = {
        fast.choose(order, shops, {}, {}, {}).shop.name  # pyright: ignore[reportOptionalMemberAccess]
        for _ in range(20)
    }
    assert picks == {"a"}
    # When: every order explores, all good shops get visited.
    rng = random.Random(7)  # noqa: S311 - reproducible test draws
    explored = [
        fast.choose(order, shops, {}, {}, {}, explore=1.0, rng=rng) for _ in range(60)
    ]
    assert {c.shop.name for c in explored if c is not None} == {"a", "b", "c"}
    assert all(c is not None and c.explored for c in explored)
    # And: a shop that failed us is never an exploration target.
    hurt = {"http://c.test": {"no_deal": 4}}
    rng = random.Random(7)  # noqa: S311 - reproducible test draws
    names = {
        c.shop.name
        for _ in range(60)
        if (c := fast.choose(order, shops, {}, hurt, {}, explore=1.0, rng=rng))
    }
    assert names == {"a", "b"}
    # And: price memory moves a shop by at most a fifth either way.
    ratios = {"http://a.test": 0.9, "http://b.test": 0.7, "http://c.test": 0.8}
    assert fast.price_factor("http://b.test", ratios) == pytest.approx(1.1)
    assert fast.price_factor("http://a.test", ratios) == pytest.approx(0.9)
    assert fast.price_factor("http://z.test", ratios) == 1.0


def test_food_unit_too_short_for_the_order_is_seen_in_the_first_reply() -> None:
    from market_a2a.remote import Task  # noqa: PLC0415 - test only

    now = 1_793_766_400.0

    def reply(data: dict[str, object]) -> Task:
        return Task.model_validate(
            {
                "id": "t",
                "contextId": "c",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {
                        "messageId": "m",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "ask 26505"}, {"data": data}],
                    },
                },
            }
        )

    signed = reply(
        {
            "ask": 26505,
            "cert": {
                "cert": {"unit": "u-1", "kind": "food", "expires_at": now + 256 * 3600},
                "sig": "x",
            },
        }
    )
    left = fast.stated_hours_left(signed, now)
    assert left is not None
    assert round(left) == 256  # 256 h left: a 360 h order walks away at once
    assert fast.stated_hours_left(reply({"hours_left": 12.5}), now) == 12.5
    assert fast.stated_hours_left(reply({"ask": 1}), now) is None


def test_short_food_unit_raises_so_the_order_moves_to_another_shop() -> None:
    from market_a2a.remote import Task  # noqa: PLC0415 - test only
    from r3.policy import BuyerPolicy  # noqa: PLC0415 - test only

    task = Task.model_validate(
        {
            "id": "t",
            "contextId": "c",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {
                    "messageId": "m",
                    "role": "ROLE_AGENT",
                    "parts": [{"data": {"hours_left": 20.0}}],
                },
            },
        }
    )
    food = Order(item="김치", kind="food", use_within_hours=24, acceptable=(KIMCHI,))
    with pytest.raises(fast.ShortShelfLifeError):
        fast.check_shelf_life(task, food, BuyerPolicy())
    fast.check_shelf_life(
        task, food.model_copy(update={"use_within_hours": 12}), BuyerPolicy()
    )


def test_unfit_units_are_skipped_early_and_remembered_per_shop_and_sku() -> None:
    from market_a2a.remote import Task  # noqa: PLC0415 - test only
    from r3.policy import BuyerPolicy  # noqa: PLC0415 - test only

    policy = BuyerPolicy()
    goods = Order(item="스탠드", kind="goods", acceptable=(LAMP,))

    def says(text: str) -> Task:
        return Task.model_validate(
            {
                "id": "t",
                "contextId": "c",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {
                        "messageId": "m",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": text}],
                    },
                },
            }
        )

    # A C unit stated in the first reply ends the talk; a card phrase does not.
    with pytest.raises(fast.UnacceptedGradeError):
        fast.check_shelf_life(
            says("Lamp (G3), certified grade C: asking 9000."), goods, policy
        )
    fast.check_shelf_life(
        says("grade A goods carry a warranty; asking 9000"), goods, policy
    )
    # Refused payments are remembered per (shop, SKU) for the next order.
    now = 10_000.0
    unfit = [
        (
            now - 600,
            "http://c-shop",
            LAMP,
            "not paying: grade C, the policy accepts ['A', 'B']",
        ),
        (
            now - 3600,
            "http://old-food",
            KIMCHI,
            "not paying: 30.0 h of shelf life left, the policy asks for 25",
        ),
    ]
    skip = fast.unfit_skipper(unfit, goods, policy, now)
    assert skip("http://c-shop/", LAMP)
    assert not skip("http://other", LAMP)
    food = Order(item="김치", kind="food", use_within_hours=24, acceptable=(KIMCHI,))
    # 30 h left an hour ago is 29 h now: enough for 24 + 1, so not skipped...
    assert not fast.unfit_skipper(unfit, food, policy, now)("http://old-food", KIMCHI)
    # ...but short of a 40 h order.
    long_food = food.model_copy(update={"use_within_hours": 40})
    assert fast.unfit_skipper(unfit, long_food, policy, now)("http://old-food", KIMCHI)


def test_an_immediate_out_of_stock_refusal_is_recognised_for_a_retry() -> None:
    from market_a2a.remote import Task  # noqa: PLC0415 - test only

    def ended(state: str, text: str) -> Task:
        return Task.model_validate(
            {
                "id": "t",
                "contextId": "c",
                "status": {
                    "state": state,
                    "message": {
                        "messageId": "m",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": text}],
                    },
                },
            }
        )

    assert fast.stock_refusal(
        ended("TASK_STATE_REJECTED", "Sorry, we have none of that in stock.")
    )
    assert fast.stock_refusal(
        ended("TASK_STATE_REJECTED", "Sorry, no matching unit is in stock.")
    )
    assert not fast.stock_refusal(ended("TASK_STATE_REJECTED", "No deal below 24998."))
    assert not fast.stock_refusal(ended("TASK_STATE_COMPLETED", "none in stock"))


def test_food_requests_state_the_shelf_life_needed_and_goods_requests_do_not() -> None:
    shops = (
        ShopEntry.model_validate(
            {
                "name": "a",
                "url": "http://a.test",
                "offers": [{"sku": KIMCHI, "name": "김치", "qty": 2}],
            }
        ),
    )
    food = Order(item="김치", kind="food", use_within_hours=24, acceptable=(KIMCHI,))
    choice = fast.choose(food, shops, {}, {}, {})
    assert choice is not None
    assert choice.request.endswith(f"({KIMCHI}), need 25 h")
    goods = Order(item="김치", kind="goods", acceptable=(KIMCHI,))
    plain = fast.choose(goods, shops, {}, {}, {})
    assert plain is not None
    assert plain.request.endswith(f"({KIMCHI})")


def test_shops_that_keep_saying_none_in_stock_lose_weight_and_can_recover() -> None:
    new = fast.stock_factor("http://new", {})
    assert new == 0.8  # Beta(4, 1) prior: an unanswered shop is not punished
    always_out = fast.stock_factor("http://out/", {"http://out": (10, 10)})
    reliable = fast.stock_factor("http://ok", {"http://ok": (0, 20)})
    assert always_out < 0.3 < new < reliable
    # And: answers with stock pull a shop back up.
    assert fast.stock_factor("http://out", {"http://out": (10, 30)}) > always_out
