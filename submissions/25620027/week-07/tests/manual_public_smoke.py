"""Verify one public virtual negotiation without submitting a market result."""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path
from typing import Final

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import TypeAdapter

from market_a2a.comparison import ComparisonDocument
from market_a2a.remote import Card, Checkout, Remote, Wire, purchase

BASE_URL: Final = "https://ax-week07.treasuresky.xyz"
EVIDENCE: Final = (
    Path(__file__).resolve().parents[1] / ".omo/evidence/top3-followup-20261021"
)


class VersionedCard(Card):
    """Read the deployed version with the existing protocol contract."""

    version: str


class RegisteredShop(Wire):
    """Parse only registration fields needed for signature verification."""

    name: str
    url: str
    pubkey: str


class Registry(Wire):
    """Ignore unrelated market data at the public JSON boundary."""

    shops: tuple[RegisteredShop, ...]


async def main() -> None:
    """Open, make one infeasible bid, then verify the second feasible bid closes."""
    with (EVIDENCE / "public-smoke-1.log").open("x", encoding="utf-8") as trace:
        async with httpx2.AsyncClient(
            timeout=60, follow_redirects=True, trust_env=False
        ) as client:

            def emit(line: str) -> None:
                _ = trace.write(line + "\n")

            remote = Remote(client, emit)
            response = await client.get(
                BASE_URL + "/.well-known/agent-card.json",
                headers={"A2A-Version": "1.0"},
            )
            _ = response.raise_for_status()
            card = VersionedCard.model_validate_json(response.content)
            assert card.version == "1.2.1"
            compared = await client.get(BASE_URL + "/price-comparison.json")
            _ = compared.raise_for_status()
            comparison = ComparisonDocument.model_validate_json(compared.content)
            assert comparison.enabled
            assert comparison.fresh
            lamp = next(value for value in comparison.own if value.item == "lamp")
            assert lamp.observed_shop_count > 0
            listed = await client.get("https://market.ourolabs.ai/api/shops")
            _ = listed.raise_for_status()
            registry = Registry.model_validate_json(listed.content)
            shop = next(entry for entry in registry.shops if entry.name == "25620027")
            assert shop.url.rstrip("/") == BASE_URL
            opening = await remote.send(card.endpoint(), "I want to buy a lamp")
            ask = opening.asking_price()
            assert ask is not None
            assert 100 <= ask <= lamp.ceiling
            assert opening.status.state == "TASK_STATE_INPUT_REQUIRED"
            low = await remote.send(card.endpoint(), "1", opening)
            low_ask = low.asking_price()
            assert low_ask is not None
            assert 100 <= low_ask <= ask
            assert low.status.state == "TASK_STATE_INPUT_REQUIRED"
            closed = await remote.send(card.endpoint(), "100", low)
            assert closed.status.state == "TASK_STATE_COMPLETED"
            result = purchase(
                closed,
                Checkout(item="lamp", sku="lamp", price=100, budget=110, shop=BASE_URL),
            )
            receipt = result.receipt
            assert receipt is not None
            body = {
                "shop": receipt["shop"],
                "item": "lamp",
                "price": 100,
                "task": opening.id,
            }
            encoded = json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            signature = TypeAdapter(str).validate_python(receipt["sig_seller"])
            Ed25519PublicKey.from_public_bytes(base64.b64decode(shop.pubkey)).verify(
                base64.b64decode(signature, validate=True), encoded
            )
            evidence = {
                "observed_at_utc": dt.datetime.now(dt.UTC).isoformat(),
                "version": card.version,
                "registry_name": shop.name,
                "public_url": BASE_URL,
                "opening_ask": ask,
                "comparison": comparison.model_dump(mode="json"),
                "first_offer": 1,
                "counter_ask": low_ask,
                "second_offer": 100,
                "receipt": body,
                "signature_verified_with_registered_public_key": True,
                "market_result_submitted": False,
                "public_review_submitted": False,
            }
            _ = (EVIDENCE / "public-smoke.json").write_text(
                json.dumps(evidence, indent=2, ensure_ascii=False) + "\n"
            )
            print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    anyio.run(main)
