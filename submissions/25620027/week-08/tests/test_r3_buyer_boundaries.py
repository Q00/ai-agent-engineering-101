"""Drive the real v3 payer against deliberately dishonest local checkouts."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Literal, assert_never

import anyio
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import market_kit as kit
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.r3_live import live_app
from tests.r3_payment import signed_receipt
from tests.r3_scenario import scenario

if TYPE_CHECKING:
    from pydantic import JsonValue
    from starlette.requests import Request

Malice = Literal["signature", "price", "item", "task", "endpoint"]


def corrupt_checkout(
    malice: Malice, claims: dict[str, JsonValue], key: Ed25519PrivateKey
) -> Ed25519PrivateKey:
    """Alter one dimension while keeping all other claims valid."""
    match malice:
        case "signature":
            return Ed25519PrivateKey.generate()
        case "price":
            claims["price"] = 101
        case "item":
            claims["item"] = "chair"
        case "task":
            claims["task"] = "another-task"
        case "endpoint":
            pass
        case _:
            assert_never(malice)
    return key


@pytest.mark.parametrize("malice", ["signature", "price", "item", "task", "endpoint"])
def test_payer_stops_before_complete_when_checkout_is_dishonest(
    monkeypatch: pytest.MonkeyPatch,
    malice: Malice,
) -> None:
    # Given: the signed A2A receipt remains100; only merchant checkout is altered.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            completed: list[bool] = []
            created: list[bool] = []
            receipt = signed_receipt(scene)

            def build(public: str) -> Starlette:
                claims: dict[str, JsonValue] = {
                    "item": "lamp",
                    "price": 100,
                    "task": receipt["task"],
                    "shop_url": public,
                    "currency": "credits",
                }
                key = corrupt_checkout(malice, claims, scene.ledger.shop_key)
                endpoint = public + "/acp"
                if malice == "endpoint":
                    endpoint = endpoint.replace("127.0.0.1", "localhost")

                async def descriptor(_request: Request) -> JSONResponse:
                    return JSONResponse({"endpoint": endpoint})

                async def create(_request: Request) -> JSONResponse:
                    created.append(True)
                    return JSONResponse(
                        {"id": "dishonest", "checkout_jwt": kit.jwt_sign(key, claims)},
                        status_code=201,
                    )

                async def complete(_request: Request) -> JSONResponse:
                    completed.append(True)
                    return JSONResponse({"error": "must not complete"}, status_code=400)

                return Starlette(
                    routes=[
                        Route("/.well-known/acp.json", descriptor),
                        Route("/acp/checkout_sessions", create, methods=["POST"]),
                        Route(
                            "/acp/checkout_sessions/dishonest/complete",
                            complete,
                            methods=["POST"],
                        ),
                    ]
                )

            async with live_app(build) as shop:
                order = scene.ledger.issue(shop)
                # When: use the unmodified course payer against this local shop.
                result = await anyio.to_thread.run_sync(
                    partial(
                        kit.acp_pay,
                        order,
                        shop,
                        kit.pub_b64(scene.ledger.shop_key),
                        receipt,
                        scene.ledger.buyer_key,
                    )
                )
            # Then: nothing was completed or charged, before verifier intervention.
            assert result is None
            assert not completed
            assert not scene.ledger.requests
            assert scene.ledger.balances == {"buyer": 1000, "shop": 0}
            if malice == "endpoint":
                assert not created

    anyio.run(run)
