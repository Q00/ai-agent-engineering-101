"""Exercise pre-limit filtering and conservative basic-request retrieval."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.catalog_constraints import compatible
from market_a2a.harness import Harness
from market_a2a.models import Product
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app
from tests.test_signed_seller import send, task_from

if TYPE_CHECKING:
    from pathlib import Path

    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    products = [
        Product(sku="lamp", name="lamp", description="lamp", kind="lamp", reserve=100),
        *[
            Product(
                sku=f"lamp-{i}",
                name="lamp",
                description="lamp",
                kind="lamp",
                reserve=100,
            )
            for i in range(6)
        ],
        Product(
            sku="lamp-z",
            name="lamp",
            description="dimmable lamp",
            kind="lamp",
            reserve=100,
        ),
    ]
    path = tmp_path / "catalog.json"
    _ = path.write_text("[" + ",".join(p.model_dump_json() for p in products) + "]")
    return Catalog(path)


def test_request_filter_before_limit_recovers_seventh_candidate(
    catalog: Catalog,
) -> None:
    ordinary = catalog.search("lamp")
    assert len(ordinary) == 6
    assert not any(compatible(p, "dimmable lamp") for p in ordinary)
    found = catalog.search("lamp", constraints=lambda p: compatible(p, "dimmable lamp"))
    assert [p.sku for p in found] == ["lamp-z"]
    assert catalog.search("lamp", constraints=lambda _p: True) == ordinary
    assert catalog.search("lamp", limit=0, constraints=lambda _p: True) == ()


@pytest.mark.parametrize(
    ("buyer_request", "expected_count", "sku"),
    [
        ("I want to buy a lamp", 1, "lamp"),
        ("I want a lamp.", 1, "lamp"),
        ("lamp", 1, "lamp"),
        ("I want to buy a dimmable lamp", 1, "lamp-z"),
        ("I want to buy a lamp under 50 cm", 6, "lamp"),
        ("I want to buy a white lamp", 6, "lamp"),
        ("I want to buy a lamp for two people", 6, "lamp"),
        ("I want to buy 2 lamps", 6, "lamp"),
    ],
)
def test_opening_preserves_model_search_and_decision(
    monkeypatch: pytest.MonkeyPatch,
    catalog: Catalog,
    buyer_request: str,
    expected_count: int,
    sku: str,
) -> None:
    calls: list[str] = []
    feedback: list[JsonValue] = []

    async def complete(
        _self: Harness,
        messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        last = messages[-1]
        if last["role"] == "tool":
            content = last["content"]
            assert isinstance(content, str)
            feedback.append(TypeAdapter(JsonValue).validate_json(content))
        name = "search_catalog" if not calls else "decide"
        arguments = (
            {"query": "lamp"}
            if not calls
            else {"action": "quote", "sku": sku, "price": 107, "reason": "Chosen"}
        )
        calls.append(name)
        return ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "offline",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        )

    monkeypatch.setattr(Harness, "complete", complete)

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/",
                Harness(model, lambda _line: None),
                catalog,
                "Shortlist test",
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://seller"
            ) as client:
                task = task_from(await send(client, buyer_request))
                assert "Asking price: 107" in task.status.message.parts[0].text
                assert not task.artifacts
        assert calls == ["search_catalog", "decide"]
        assert len(feedback) == 1
        products = feedback[0]
        assert isinstance(products, list)
        assert len(products) == expected_count
        for entry in products:
            assert isinstance(entry, dict)
            assert entry["allowed_quote_min"] == 100
            assert entry["allowed_quote_max"] == 110
        print(
            json.dumps(
                {
                    "request": buyer_request,
                    "candidate_count": len(products),
                    "model_calls": calls,
                    "model_quote": 107,
                    "tool_result_chars": len(json.dumps(products)),
                }
            )
        )

    anyio.run(scenario)


@pytest.mark.parametrize("signed", [True, False])
def test_card_frontloads_verified_terms(catalog: Catalog, *, signed: bool) -> None:

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/",
                Harness(model, lambda _line: None),
                catalog,
                "Test",
                signer=ReceiptSigner(Ed25519PrivateKey.generate()) if signed else None,
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://seller"
            ) as client:
                response = await client.get("/.well-known/agent-card.json")
                assert response.status_code == 200
                card = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
                assert card["version"] == "1.4.1"
                description = card["description"]
                assert isinstance(description, str)
                first = description[:300]
                for phrase in (
                    "lamp 100",
                    "chair 180",
                    "bookshelf 260",
                    "numeric offer",
                    "same taskId",
                    "/price-comparison.json",
                    "Dated",
                ):
                    assert phrase in first
                assert ("Ed25519" in first) is signed
                print(json.dumps({"signed": signed, "card_first_300": first}))

    anyio.run(scenario)
