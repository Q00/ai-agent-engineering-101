from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Final

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from market_a2a.receipts import SignedReceipt
from market_a2a.remote import Checkout, Remote, purchase
from tests.manual_public_smoke import BASE_URL, Registry, VersionedCard

EVIDENCE: Final = (
    Path(__file__).resolve().parents[1] / ".omo/evidence/seller-strategy-20261021"
)


async def main() -> None:
    with (EVIDENCE / "public-trades.log").open("x", encoding="utf-8") as trace:
        async with httpx2.AsyncClient(
            timeout=60, follow_redirects=True, trust_env=False
        ) as client:

            def emit(line: str) -> None:
                _ = trace.write(line + "\n")

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
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(shop.pubkey)
            )
            records: list[dict[str, str | int | bool]] = []
            for floor_offer in (True, False):
                opening = await remote.send(
                    card.endpoint(), "I want to buy a bookshelf"
                )
                ask = opening.asking_price()
                assert ask is not None
                assert 260 <= ask <= 286
                price = 260 if floor_offer else ask
                closed = await remote.send(card.endpoint(), str(price), opening)
                assert closed.status.state == "TASK_STATE_COMPLETED"
                result = purchase(
                    closed,
                    Checkout(
                        item="bookshelf",
                        sku="bookshelf",
                        price=price,
                        budget=286,
                        shop=BASE_URL,
                    ),
                )
                receipt = result.receipt
                assert receipt is not None
                signed = SignedReceipt.model_validate(
                    {
                        key: receipt[key]
                        for key in ("shop", "item", "price", "task", "sig_seller")
                    }
                )
                public_key.verify(
                    base64.b64decode(signed.sig_seller), signed.canonical()
                )
                assert signed.price == price
                assert signed.task == opening.id
                records.append(
                    {
                        "scenario": "first_floor_counter" if floor_offer else "pay_ask",
                        "ask": ask,
                        "actual_offer": price,
                        "receipt_price": signed.price,
                        "signed_with_registered_key": True,
                        "first_counter_completed": True,
                        "market_result_submitted": False,
                        "public_review_submitted": False,
                    }
                )
            evidence = {
                "version": card.version,
                "public_url": BASE_URL,
                "virtual_trades": records,
            }
            _ = (EVIDENCE / "public-trades.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(evidence))


if __name__ == "__main__":
    anyio.run(main)
