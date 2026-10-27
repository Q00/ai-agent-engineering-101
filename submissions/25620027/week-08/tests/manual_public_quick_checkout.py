from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from time import monotonic

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from market_a2a.receipts import SignedReceipt
from market_a2a.remote import Checkout, Remote, Wire, purchase
from tests.manual_public_smoke import BASE_URL, Registry, VersionedCard
from tests.test_quick_checkout import SERVICE_REQUEST

EVIDENCE = Path(__file__).resolve().parents[1] / (
    ".omo/evidence/dragonash-upgrade-20261022"
)
MARKET = "https://market.ourolabs.ai"


class _ListedCard(Wire):
    version: str


class _Owner(Wire):
    login: str


class _ListedShop(Wire):
    name: str
    card: _ListedCard
    owner: _Owner | None = None
    deals: int
    visits: int
    revenue: int
    stars: float | None = None


class _Board(Wire):
    round: int
    shops: tuple[_ListedShop, ...]


async def refresh_listing(
    client: httpx2.AsyncClient, registry: Registry,
) -> None:
    response = await client.get(MARKET + "/api/board")
    _ = response.raise_for_status()
    board = _Board.model_validate_json(response.content)
    before = next(x for x in board.shops if x.name == "25620027")
    after = before
    assert before.owner is not None
    assert before.owner.login == "SungJinho"
    shop = next(x for x in registry.shops if x.name == "25620027")
    assert shop.url.rstrip("/") == BASE_URL
    joined = await client.post(MARKET + "/api/join", json={
        "role": "shop", "name": shop.name, "url": shop.url,
        "pubkey": shop.pubkey,
    })
    _ = joined.raise_for_status()
    for _attempt in range(12):
        response = await client.get(MARKET + "/api/board")
        _ = response.raise_for_status()
        board = _Board.model_validate_json(response.content)
        after = next(x for x in board.shops if x.name == shop.name)
        if after.card.version == "1.6.0":
            break
        await anyio.sleep(1)
    assert after.card.version == "1.6.0"
    assert after.owner == before.owner
    assert after.deals >= before.deals
    response = await client.get(MARKET + "/api/shops")
    _ = response.raise_for_status()
    registered = next(x for x in Registry.model_validate_json(response.content).shops
                      if x.name == shop.name)
    assert registered == shop
    result = {"before": before.model_dump(), "after": after.model_dump(),
              "round": board.round, "same_owner_url_key": True,
              "join_posts": 1, "buyer_started": False, "token_saved": False}
    _ = (EVIDENCE / "listing.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


async def main() -> None:
    async with httpx2.AsyncClient(timeout=90, trust_env=False) as client:
        response = await client.get(MARKET + "/api/shops")
        _ = response.raise_for_status()
        registry = Registry.model_validate_json(response.content)
        if "--refresh-listing" in sys.argv:
            await refresh_listing(client, registry)
            return
        shop = next(x for x in registry.shops if x.name == "25620027")
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(shop.pubkey))
        response = await client.get(BASE_URL + "/.well-known/agent-card.json")
        _ = response.raise_for_status()
        card = VersionedCard.model_validate_json(response.content)
        assert card.version == "1.6.0"
        records: list[dict[str, str | int | float | bool]] = []
        scenarios = [
            ("basic_service", SERVICE_REQUEST, "lamp", "I accept", 100, 110),
            ("basic_chair", "Hello, I'd like to buy a chair, please.",
             "chair", "OK", 180, 198),
            ("descriptive",
             "A black dimmable lamp with flexible neck, no taller than 30 cm.",
             "lamp-nocturne", "I accept", 120, 132),
        ]
        with (EVIDENCE / "public-trades.log").open("x") as trace:
            def emit(line: str) -> None:
                _ = trace.write(line + "\n")
                trace.flush()

            remote = Remote(client, emit)
            for name, buyer_text, sku, assent, reserve, cap in scenarios:
                start = monotonic()
                opening = await remote.send(card.endpoint(), buyer_text)
                open_seconds = monotonic() - start
                ask = opening.asking_price()
                assert ask is not None
                assert reserve <= ask <= cap
                start = monotonic()
                closed = await remote.send(card.endpoint(), assent, opening)
                close_seconds = monotonic() - start
                assert closed.status.state == "TASK_STATE_COMPLETED"
                result = purchase(closed, Checkout(
                    item="chair" if sku == "chair" else "lamp", sku=sku,
                    price=ask, budget=cap, shop=BASE_URL,
                ))
                raw = result.receipt
                assert raw is not None
                signed = SignedReceipt.model_validate({
                    **{field: raw[field] for field in
                       ("shop", "item", "task", "sig_seller")}, "price": ask,
                })
                key.verify(base64.b64decode(signed.sig_seller), signed.canonical())
                assert signed.task == opening.id
                assert raw["sku"] == sku
                assert "Completed in 1 buyer offer(s)." in closed.observation()
                records.append({
                    "scenario": name, "task": opening.id, "price": ask, "sku": sku,
                    "opening_seconds": open_seconds, "closing_seconds": close_seconds,
                    "registered_signature_verified": True,
                    "opening_text": opening.observation(),
                    "market_result_submitted": False, "public_review_submitted": False,
                })
                _ = (EVIDENCE / "public-trades.json").write_text(
                    json.dumps(records, indent=2),
                )
        print(json.dumps(records))


if __name__ == "__main__":
    anyio.run(main)
