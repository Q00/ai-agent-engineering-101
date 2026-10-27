"""Drive our harness, real sockets and the unmodified instructor v3 payer."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import anyio
from pydantic import TypeAdapter
from scripts import market_kit as kit

from market_a2a.market_buyer import MarketRequest, market_buy
from tests.market_fakes import ScriptedHarness
from tests.r3_ledger import JSON
from tests.r3_payment import closed_payment, signed_receipt
from tests.r3_scenario import scenario

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_harness_receipt_settles_through_real_kit_v3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: our real seller/buyer with only their external model calls replaced.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            registry = tmp_path / "shops.json"
            _ = registry.write_text(
                json.dumps(
                    [
                        {
                            "name": "Local Shop",
                            "url": scene.shop,
                            "reviews": [],
                        }
                    ]
                )
            )
            order = scene.ledger.issue(scene.shop)
            buyer = ScriptedHarness(
                [
                    (
                        "select_shop",
                        {"index": 0, "reason": "Local contract verification"},
                    ),
                    ("offer", {"price": 100, "reason": "Within authorized budget"}),
                    (
                        "leave_review",
                        {"stars": 3, "review": "Receipt received at 100."},
                    ),
                ]
            )
            # When: negotiate A2A, preserve the raw receipt, then let v3 pay it.
            result = await market_buy(
                buyer,
                MarketRequest(
                    registry,
                    "lamp",
                    120,
                    tmp_path / "memory.json",
                ),
                scene.trace.append,
            )
            assert result.receipt is not None
            receipt = JSON.validate_python(result.receipt)
            # v3 handle_order performs this same protobuf whole-number normalization.
            receipt["price"] = int(TypeAdapter(float).validate_python(receipt["price"]))
            paid = await anyio.to_thread.run_sync(
                partial(
                    kit.acp_pay,
                    order,
                    scene.shop,
                    kit.pub_b64(scene.ledger.shop_key),
                    receipt,
                    scene.ledger.buyer_key,
                )
            )
            # Then: signatures bind the task; the public ledger hides the budget.
            assert paid is not None
            assert scene.ledger.balances == {"buyer": 900, "shop": 100}
            assert len(scene.ledger.requests) == 1
            request = scene.ledger.requests[0]
            co = kit.jwt_payload(
                request.checkout_jwt, kit.pub_b64(scene.ledger.shop_key)
            )
            assert co is not None
            assert co["task"] == receipt["task"]
            assert request.payment_mandate.mandate["amount"] == receipt["price"]
            public = scene.ledger.events[0]["data"]
            data = JSON.validate_python(public)
            assert "budget" not in data
            assert "budget_commitment" in data
            assert scene.ledger.events[-1]["kind"] == "settle"
            print(
                json.dumps(
                    {
                        "flow": "our-A2A -> course-ACP/AP2 -> local-settle",
                        "price": receipt["price"],
                        "paid": paid,
                        "model_api_calls": 0,
                        "public_market_calls": 0,
                    }
                )
            )

    anyio.run(run)


def test_payer_refuses_checkout_above_open_mandate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a genuine seller signature over a price above the private mandate cap.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            order = scene.ledger.issue(scene.shop, budget=90)
            # When: the real course payer receives the signed100 receipt.
            paid = await anyio.to_thread.run_sync(
                partial(
                    kit.acp_pay,
                    order,
                    scene.shop,
                    kit.pub_b64(scene.ledger.shop_key),
                    signed_receipt(scene),
                    scene.ledger.buyer_key,
                )
            )
            # Then: completion/settlement is never attempted and no credits move.
            assert paid is None
            assert not scene.ledger.requests
            assert scene.ledger.balances == {"buyer": 1000, "shop": 0}

    anyio.run(run)


def test_completed_session_and_settled_order_cannot_charge_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a correctly signed checkout and a fresh order.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            payment = await closed_payment(scene)
            route = f"/acp/checkout_sessions/{payment['session']}/complete"
            # When: complete twice, then replay the signed body at the verifier.
            body = {"payment_data": payment["payment_data"]}
            first = await scene.http.post(route, json=body)
            second = await scene.http.post(route, json=body)
            replay = await scene.http.post(
                scene.market + "/api/settle",
                content=scene.ledger.requests[0].model_dump_json(),
                headers={
                    "Authorization": "Bearer local-shop",
                    "content-type": "application/json",
                },
            )
            # Then: both replay layers reject and the wallet changes exactly once.
            assert first.status_code == 200
            assert second.status_code == 409
            assert replay.status_code == 400
            assert not replay.json()["checks"]["not_settled_before"]
            assert scene.ledger.balances == {"buyer": 900, "shop": 100}

    anyio.run(run)
