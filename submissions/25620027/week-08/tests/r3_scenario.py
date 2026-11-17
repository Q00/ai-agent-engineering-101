"""Connect our real seller to the pinned kit and a loopback settlement ledger."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app
from r3.gateway import CheckoutBinding, create_gateway
from r3.transport import SettlementTransport, create_settlement_client
from tests.r3_ledger import LocalLedger
from tests.r3_live import live_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
    from starlette.types import ASGIApp


@dataclass(frozen=True, slots=True)
class Scenario:
    """Owned test endpoints, mutable clock and trace storage for one scenario."""

    shop: str
    market: str
    ledger: LocalLedger
    http: httpx2.AsyncClient
    clock: list[int]
    trace: list[str]


@asynccontextmanager
async def scenario(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Scenario]:
    """Replace only model responses; preserve real A2A, checkout and HTTP calls."""
    monkeypatch.setenv("AX_PRICE_COMPARISON", "0")
    monkeypatch.setenv("AX_MODEL_PROVIDER", "openai")
    monkeypatch.delenv("AX_SELLER_STATE_DB", raising=False)
    replies = iter([("quote", 110), ("accept", 100)])

    async def complete(
        _self: Harness,
        _messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        action, price = next(replies)
        return ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "local-model",
                        "type": "function",
                        "function": {
                            "name": "decide",
                            "arguments": json.dumps(
                                {
                                    "action": action,
                                    "sku": "lamp",
                                    "price": price,
                                    "reason": "Offline protocol-path fixture",
                                }
                            ),
                        },
                    }
                ],
            }
        )

    def forbid_api(_request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Local protocol checks may not call an external model API")

    monkeypatch.setattr(Harness, "complete", complete)
    shop_key, buyer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    clock = [1000]
    ledger = LocalLedger(shop_key, buyer_key, lambda: clock[0])
    trace: list[str] = []
    async with live_app(lambda _url: ledger.app()) as market:
        with create_settlement_client(market) as settlement_http:
            async with (
                httpx2.AsyncClient(transport=httpx2.MockTransport(forbid_api)) as api,
                AsyncOpenAI(api_key="offline-placeholder", http_client=api) as model,
            ):
                harness = Harness(model, trace.append)

                def build(public: str) -> ASGIApp:
                    seller = create_app(
                        public + "/",
                        harness,
                        Catalog(Path(__file__).resolve().parents[1] / "catalog.json"),
                        "R3 Local Sungjin Shop",
                        signer=ReceiptSigner(shop_key),
                    )
                    return create_gateway(
                        seller,
                        CheckoutBinding(
                            public,
                            shop_key,
                            SettlementTransport(settlement_http, "local-shop"),
                        ),
                    )

                async with (
                    live_app(build) as shop,
                    httpx2.AsyncClient(base_url=shop, trust_env=False) as http,
                ):
                    yield Scenario(shop, market, ledger, http, clock, trace)
