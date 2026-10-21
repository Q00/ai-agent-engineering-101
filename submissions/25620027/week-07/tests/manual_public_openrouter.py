from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Final

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from market_a2a.receipts import SignedReceipt
from market_a2a.remote import Checkout, Remote, purchase
from tests.manual_public_smoke import BASE_URL, Registry, VersionedCard

EVIDENCE: Final = (
    Path(__file__).resolve().parents[1] / (
        ".omo/evidence/openai-fallback-20261021" if "--fallback" in sys.argv
        else ".omo/evidence/openrouter-week-20261021"
    )
)


async def main() -> None:
    with (EVIDENCE / "public-trade.log").open("x", encoding="utf-8") as trace:
        def emit(line: str) -> None:
            _ = trace.write(line + "\n")
            trace.flush()

        async with httpx2.AsyncClient(timeout=90, follow_redirects=True) as client:
            remote = Remote(client, emit)
            response = await client.get(BASE_URL + "/.well-known/agent-card.json")
            _ = response.raise_for_status()
            card = VersionedCard.model_validate_json(response.content)
            assert card.version == "1.4.0"
            response = await client.get("https://market.ourolabs.ai/api/shops")
            _ = response.raise_for_status()
            registry = Registry.model_validate_json(response.content)
            shop = next(entry for entry in registry.shops if entry.name == "25620027")
            assert shop.url.rstrip("/") == BASE_URL
            opening = await remote.send(card.endpoint(), "I want to buy a lamp")
            ask = opening.asking_price()
            assert ask is not None
            assert 100 <= ask <= 110
            closed = await remote.send(card.endpoint(), "100", opening)
            assert closed.status.state == "TASK_STATE_COMPLETED"
            result = purchase(
                closed, Checkout(item="lamp", sku="lamp", price=100,
                                 budget=110, shop=BASE_URL),
            )
            receipt = result.receipt
            assert receipt is not None
            assert receipt["price"] == 100
            signed = SignedReceipt.model_validate({
                **{key: receipt[key] for key in
                   ("shop", "item", "task", "sig_seller")}, "price": 100,
            })
            assert signed.task == opening.id
            assert signed.item == "lamp"
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(shop.pubkey),
            )
            public_key.verify(base64.b64decode(signed.sig_seller), signed.canonical())
            summary = {"url": BASE_URL, "version": card.version,
                       "registry_name": shop.name, "ask": ask, "offer": 100,
                       "receipt": signed.model_dump(),
                       "registered_key_signature_verified": True,
                       "market_result_submitted": False,
                       "public_review_submitted": False}
            _ = (EVIDENCE / "public-trade.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8",
            )
            print(json.dumps(summary))


if __name__ == "__main__":
    anyio.run(main)
