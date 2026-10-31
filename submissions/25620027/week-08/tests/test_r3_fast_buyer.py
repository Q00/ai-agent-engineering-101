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
