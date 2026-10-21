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
from market_a2a.remote import Checkout, Remote, Wire, purchase
from tests.manual_public_smoke import BASE_URL, Registry, VersionedCard

EVIDENCE: Final = Path(__file__).resolve().parents[1] / (
    ".omo/evidence/top5-upgrade-20261021"
)
MARKET: Final = "https://market.ourolabs.ai"


class _ListedCard(Wire):
    version: str
    description: str


class _Owner(Wire):
    login: str


class _ListedShop(Wire):
    name: str
    card: _ListedCard
    owner: _Owner | None = None
    deals: int


class _Board(Wire):
    shops: tuple[_ListedShop, ...]


class _Joined(Wire):
    name: str
    round: int


async def refresh_listing(client: httpx2.AsyncClient, registry: Registry) -> None:
    response = await client.get(MARKET + "/api/board")
    _ = response.raise_for_status()
    before = next(row for row in _Board.model_validate_json(response.content).shops
                  if row.name == "25620027")
    assert before.owner is not None
    assert before.owner.login == "SungJinho"
    shop = next(row for row in registry.shops if row.name == "25620027")
    assert shop.url.rstrip("/") == BASE_URL
    response = await client.post(MARKET + "/api/join", json={
        "role": "shop", "name": shop.name, "url": shop.url,
        "pubkey": shop.pubkey,
    })
    _ = response.raise_for_status()
    joined = _Joined.model_validate_json(response.content)
    assert joined.name == shop.name
    response = await client.get(MARKET + "/api/board")
    _ = response.raise_for_status()
    after = next(row for row in _Board.model_validate_json(response.content).shops
                 if row.name == shop.name)
    assert after.owner == before.owner
    assert after.card.version == "1.5.0"
    assert after.deals >= before.deals
    response = await client.get(MARKET + "/api/shops")
    _ = response.raise_for_status()
    current = next(row for row in Registry.model_validate_json(response.content).shops
                   if row.name == shop.name)
    assert current == shop
    record = {"name": shop.name, "round": joined.round,
              "before": before.model_dump(), "after": after.model_dump(),
              "same_url_public_key_and_owner": True,
              "credential_read": False, "join_token_saved": False,
              "automatic_rejoin_started": False, "buyer_started": False}
    _ = (EVIDENCE / "listing-refresh.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


async def main() -> None:
    async with httpx2.AsyncClient(timeout=90, trust_env=False) as client:
        response = await client.get(MARKET + "/api/shops")
        _ = response.raise_for_status()
        registry = Registry.model_validate_json(response.content)
        if "--refresh-listing" in sys.argv:
            await refresh_listing(client, registry)
            return
        shop = next(row for row in registry.shops if row.name == "25620027")
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(shop.pubkey))
        response = await client.get(BASE_URL + "/.well-known/agent-card.json")
        _ = response.raise_for_status()
        card = VersionedCard.model_validate_json(response.content)
        assert card.version == "1.5.0"
        records: list[dict[str, str | int | bool]] = []
        with (EVIDENCE / "public-trades.log").open("x", encoding="utf-8") as trace:
            def emit(line: str) -> None:
                _ = trace.write(line + "\n")
                trace.flush()

            remote = Remote(client, emit)
            for first_offer in (True, False):
                opening = await remote.send(
                    card.endpoint(),
                    "I want to buy a lamp for $100.00." if first_offer else "lamp",
                )
                closed = opening if first_offer else await remote.send(
                    card.endpoint(), "I can pay 100", opening,
                )
                assert closed.status.state == "TASK_STATE_COMPLETED"
                result = purchase(closed, Checkout(
                    item="lamp", sku="lamp", price=100, budget=110, shop=BASE_URL,
                ))
                raw = result.receipt
                assert raw is not None
                signed = SignedReceipt.model_validate({
                    **{field: raw[field] for field in
                       ("shop", "item", "task", "sig_seller")}, "price": 100,
                })
                key.verify(base64.b64decode(signed.sig_seller), signed.canonical())
                assert signed.task == opening.id
                assert raw["price"] == 100
                assert raw["sku"] == "lamp"
                records.append({
                    "scenario": "natural_opening" if first_offer else "natural_counter",
                    "price": 100, "registered_key_signature_verified": True,
                    "opening_text": opening.observation(),
                    "market_result_submitted": False, "public_review_submitted": False,
                })
        _ = (EVIDENCE / "public-trades.json").write_text(json.dumps(records, indent=2))
        print(json.dumps(records))


if __name__ == "__main__":
    anyio.run(main)
