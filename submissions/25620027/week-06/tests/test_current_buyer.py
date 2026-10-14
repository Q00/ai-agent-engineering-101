"""Offline buyer trust-boundary scenarios, without model calls."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import ValidationError

from market_a2a.buyer import Negotiation
from market_a2a.models import (
    HarnessError,
    Purchase,
    ShopCard,
    ShoppingItem,
    ToolResult,
    ToolSpec,
)
from market_a2a.remote import Checkout, Receipt, Remote, Task, purchase
from market_a2a.shopping import Planner, PlanningRound, shop

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from market_a2a.buyer import BuyerHarness
    from market_a2a.models import ToolDispatch


def task_json(*, price: JsonValue = 100, sku: str = "lamp-1") -> str:
    return json.dumps(
        {
            "id": "task-1",
            "contextId": "context-1",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {"parts": [{"data": {"item": "lamp", "sku": sku, "price": price}}]}
            ],
        }
    )


def test_exact_receipt() -> None:
    authorization = Checkout(
        item="lamp", sku="lamp-1", price=100, budget=120, shop="shop"
    )
    result = purchase(Task.model_validate_json(task_json()), authorization)
    assert result.completed
    assert result.price == 100
    assert result.sku == "lamp-1"


@pytest.mark.parametrize(("price", "sku"), [(101, "lamp-1"), (100, "other")])
def test_receipt_rejects_changed_authorization(price: int, sku: str) -> None:
    authorization = Checkout(
        item="lamp", sku="lamp-1", price=100, budget=120, shop="shop"
    )
    with pytest.raises(HarnessError, match="differs"):
        _ = purchase(
            Task.model_validate_json(task_json(price=price, sku=sku)), authorization
        )


@pytest.mark.parametrize("price", [True, "100", 100.5])
def test_receipt_strict_price(price: JsonValue) -> None:
    with pytest.raises(ValidationError):
        _ = Task.model_validate_json(task_json(price=price))


def test_remote_header_endpoint_continuation_and_budget_guard() -> None:
    async def scenario() -> None:
        seen: list[httpx2.Request] = []
        lines: list[str] = []

        def response(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            assert request.headers["A2A-Version"] == "1.0"
            if request.method == "GET":
                return httpx2.Response(
                    200,
                    json={
                        "name": "Shop",
                        "skills": [{"name": "lamp"}],
                        "supportedInterfaces": [
                            {
                                "url": "https://shop/rpc",
                                "protocolBinding": "JSONRPC",
                                "protocolVersion": "1.0",
                            }
                        ],
                    },
                )
            assert request.url.path == "/rpc"
            return httpx2.Response(
                200,
                json={
                    "result": {
                        "task": {
                            "id": "task-1",
                            "contextId": "context-1",
                            "status": {
                                "state": "TASK_STATE_INPUT_REQUIRED",
                                "message": {
                                    "parts": [{"text": "lamp asking 130"}],
                                    "metadata": {"sku": "lamp-1", "item": "lamp"},
                                },
                            },
                        }
                    }
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(response)
        ) as client:
            remote = Remote(client, lines.append)
            card = await remote.card("https://shop")
            task = await remote.send(card.endpoint(), "lamp")
            negotiation = Negotiation(
                remote,
                Checkout(
                    item="lamp", sku="lamp-1", price=0, budget=120, shop=card.endpoint()
                ),
            )
            negotiation.task = task
            blocked = await negotiation.dispatch(
                "offer", '{"price":121,"reason":"bad"}'
            )
            assert not blocked.terminal
            assert len(seen) == 2
            accepted = await negotiation.dispatch(
                "offer", '{"price":100,"reason":"counter"}'
            )
            assert accepted.terminal
            assert len(seen) == 3
            assert b'"taskId":"task-1"' in seen[-1].content
            assert b'"contextId":"context-1"' in seen[-1].content
            assert task.status.message.metadata.sku == "lamp-1"
            repeated = await negotiation.dispatch(
                "offer", '{"price":100,"reason":"again"}'
            )
            assert repeated.terminal
            assert len(seen) == 4
            walked = await negotiation.dispatch("walk_away", '{"reason":"too costly"}')
            assert walked.terminal
            assert len(seen) == 4
            assert any("A2A REQUEST" in line for line in lines)

    anyio.run(scenario)


def test_shop_replans_only_failed_item_with_observed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one paid item and one shop whose price exceeds its reserved cap.
    async def scenario() -> None:
        cards = tuple(
            ShopCard(url=name, name=name, skills=("lamp", "chair"))
            for name in ("paid-shop", "expensive-shop", "replacement-shop")
        )
        items = (
            ShoppingItem(request_id="light", description="lamp"),
            ShoppingItem(request_id="seat", description="chair"),
        )
        calls: list[str] = []
        states: list[PlanningRound] = []
        lines: list[str] = []

        class Harness:
            async def run(
                self,
                prompt: str,
                state: JsonValue,
                tools: tuple[ToolSpec, ...],
                dispatch: ToolDispatch,
            ) -> ToolResult:
                _ = prompt, tools
                parsed = PlanningRound.model_validate(state)
                states.append(parsed)
                reservations = [
                    {"request_id": "seat", "shop_url": "replacement-shop", "cap": 200}
                ]
                if parsed.planning_round == 0:
                    reservations = [
                        {"request_id": "light", "shop_url": "paid-shop", "cap": 150},
                        {
                            "request_id": "seat",
                            "shop_url": "expensive-shop",
                            "cap": 200,
                        },
                    ]
                return await dispatch(
                    "plan_purchases",
                    json.dumps({"reservations": reservations, "reason": "fixture"}),
                )

        async def external_checkout(
            _harness: BuyerHarness,
            url: str,
            item: str,
            _budget: int,
            _emit: Callable[[str], None],
        ) -> Purchase:
            calls.append(url)
            completed = not url.startswith("expensive")
            return Purchase(
                completed=completed,
                item=item,
                price=(120 if item == "lamp" else 180) if completed else None,
                task_id=url,
                shop=url,
                failure_observation="Price exceeds 200" if not completed else "",
            )

        monkeypatch.setattr("market_a2a.shopping.buy", external_checkout)
        # When the real shopping loop handles that unpaid checkout.
        purchases = await shop(Harness(), cards, items, 400, lines.append)
        # Then only the unpaid item is replanned with its failure evidence.
        assert len(states) == 2
        retry = states[1]
        assert [item.request_id for item in retry.items] == ["seat"]
        assert retry.total_budget == 280
        assert len(retry.already_bought) == 1
        failure = retry.failed_attempts[0]
        assert failure.request_id == "seat"
        assert failure.shop_url == "expensive-shop"
        assert failure.cap == 200
        assert failure.outcome.failure_observation == "Price exceeds 200"
        assert calls.count("paid-shop") == 1
        assert calls.count("expensive-shop") == 1
        assert calls.count("replacement-shop") == 1
        assert all(purchase.completed for purchase in purchases)
        assert sum(purchase.price or 0 for purchase in purchases) == 300
        assert '"reserved": 0' in lines[-1]

    anyio.run(scenario)


def test_plan_guards_unique_coverage_and_reserved_total() -> None:
    async def scenario() -> None:
        planner = Planner(
            (ShopCard(url="shop", name="Shop", skills=("lamp",)),),
            (ShoppingItem(request_id="a", description="lamp"),),
            120,
        )
        for reservations in (
            [{"request_id": "a", "shop_url": "shop", "cap": 121}],
            [{"request_id": "a", "shop_url": "missing", "cap": 100}],
            [],
            [{"request_id": "a", "shop_url": "shop", "cap": 50}] * 2,
        ):
            rejected = await planner.dispatch(
                "plan_purchases",
                json.dumps(
                    {
                        "reservations": reservations,
                        "reason": "boundary test",
                    }
                ),
            )
            assert not rejected.terminal
            assert planner.plan is None
        accepted = await planner.dispatch(
            "plan_purchases",
            json.dumps(
                {
                    "reservations": [
                        {"request_id": "a", "shop_url": "shop", "cap": 120}
                    ],
                    "reason": "valid",
                }
            ),
        )
        assert accepted.terminal
        duplicate = await planner.dispatch("plan_purchases", "{}")
        assert not duplicate.terminal

    anyio.run(scenario)


def test_integral_protobuf_price() -> None:
    assert Task.model_validate_json(task_json(price=100.0)).artifacts[0].parts[
        0
    ].data == Receipt(
        price=100,
        item="lamp",
        sku="lamp-1",
    )


def test_parallel_checkout_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        active = 0
        peak = 0
        calls: list[str] = []
        lines: list[str] = []
        cards = tuple(
            ShopCard(url=f"shop-{n}", name=f"Shop {n}", skills=("lamp",))
            for n in range(6)
        )
        items = tuple(
            ShoppingItem(request_id=str(n), description=f"lamp {n}") for n in range(6)
        )

        class Harness:
            async def run(
                self,
                prompt: str,
                state: JsonValue,
                tools: tuple[ToolSpec, ...],
                dispatch: ToolDispatch,
            ) -> ToolResult:
                assert prompt
                assert state
                assert tools
                return await dispatch(
                    "plan_purchases",
                    json.dumps(
                        {
                            "reservations": [
                                {
                                    "request_id": str(n),
                                    "shop_url": f"shop-{n}",
                                    "cap": 100,
                                }
                                for n in range(6)
                            ],
                            "reason": "public skills",
                        }
                    ),
                )

        async def fake_buy(
            harness: BuyerHarness,
            url: str,
            item: str,
            budget: int,
            emit: Callable[[str], None],
        ) -> Purchase:
            nonlocal active, peak
            assert harness
            assert url
            assert budget == 100
            emit("fake transport boundary")
            active += 1
            peak = max(peak, active)
            calls.append(item)
            await anyio.sleep(0.01)
            active -= 1
            return Purchase(completed=True, item=item, price=90, task_id=item, shop=url)

        monkeypatch.setattr("market_a2a.shopping.buy", fake_buy)
        result = await shop(Harness(), cards, items, 600, lines.append)
        assert len(result) == len(set(calls)) == 6
        assert peak == 5
        assert sum(entry.price or 0 for entry in result) == 540
        assert '"spent": 540' in lines[-1]
        assert '"reserved": 0' in lines[-1]

    anyio.run(scenario)
