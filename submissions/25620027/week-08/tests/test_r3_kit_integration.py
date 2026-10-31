"""Our R3 seller and fast buyer through the unmodified kit v4 proxy, checkout and payer.

Real sockets, real Ed25519 signatures, the instructor's ``Proxy``, ``AcpShop`` and
``acp_pay`` exactly as shipped; only the market is a loopback settlement simulator.
"""

from __future__ import annotations

import base64
import json
import time
from functools import partial
from typing import TYPE_CHECKING

import anyio
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import market_kit as kit

from market_a2a.receipts import ReceiptSigner
from r3 import buyer as fast
from r3.seller import create_r3_app
from r3.transport import SettlementTransport, create_settlement_client
from tests.r3_ledger import LocalLedger
from tests.r3_live import live_app
from tests.r3_stock import KIMCHI, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.types import ASGIApp


def test_round3_receipt_settles_through_kit_v4_proxy_checkout_and_accept_policy(
    tmp_path: Path,
) -> None:
    # Given: our seller holds one certified A-grade kimchi unit; the market key
    # signs its certificate.
    shop_key, buyer_key, market_key = (Ed25519PrivateKey.generate() for _ in range(3))
    now = time.time()
    stock = [unit(KIMCHI, 1, grade="A", now=now, shelf_left_ratio=0.9)]
    body = {
        "unit": stock[0]["unit"],
        "sku": KIMCHI,
        "grade": "A",
        "expires_at": stock[0]["expires_at"],
    }
    stock[0]["cert"] = {
        "cert": body,
        "sig": base64.b64encode(market_key.sign(kit.canon(body))).decode(),
    }
    folder = write_market(tmp_path / ".market", stock)
    clock = [int(now)]
    ledger = LocalLedger(shop_key, buyer_key, lambda: clock[0])
    ledger.inventory.update({"round": 3, "units": stock})
    ledger.balances["buyer"] = 10_000_000
    trace: list[str] = []

    async def run() -> None:
        async with live_app(lambda _url: ledger.app()) as market:
            with create_settlement_client(market) as settlement_http:

                def build(url: str) -> ASGIApp:
                    return create_r3_app(
                        url + "/",
                        "R3 Local Shop",
                        signer=ReceiptSigner(shop_key),
                        emit=trace.append,
                        folder=folder,
                        policy_file=tmp_path / "policy.json",
                        events_file=tmp_path / "events.sqlite",
                    )

                async with live_app(build) as seller_url:
                    # And: the instructor's proxy fronts our seller and serves ACP.
                    proxy = kit.Proxy(kit.free_port())
                    proxy.target(seller_url)
                    proxy.acp = kit.AcpShop(
                        shop_key, SettlementTransport(settlement_http, "local-shop")
                    )
                    public = proxy.public
                    try:
                        order = ledger.issue(
                            public,
                            budget=60000,
                            item="김치",
                            acceptable=[KIMCHI],
                            kind="food",
                            use_within_hours=24,
                            shelf_hours=720,
                            market_pubkey=kit.pub_b64(market_key),
                        )
                        _ = (folder / "order.json").write_text(
                            json.dumps(
                                {k: v for k, v in order.items() if k != "mandates"},
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        _ = (folder / "shops.json").write_text(
                            json.dumps(
                                [
                                    {
                                        "name": "R3 Local Shop",
                                        "url": public,
                                        "reviews": [],
                                        "offers": [
                                            {"sku": KIMCHI, "name": "kimchi", "qty": 1}
                                        ],
                                    }
                                ]
                            ),
                            encoding="utf-8",
                        )
                        # When: our fast buyer negotiates through the proxy.
                        outcome = await fast.buy(
                            folder / "shops.json",
                            "김치",
                            60000,
                            folder / "order.json",
                            trace.append,
                            folder=folder,
                            policy_file=tmp_path / "policy.json",
                            events_file=tmp_path / "events.sqlite",
                        )
                        assert outcome.receipt is not None
                        assert outcome.agreed_price is not None
                        assert outcome.accept == {"cert": True, "min_hours_left": 25.0}
                        receipt = dict(outcome.receipt)
                        receipt["price"] = int(float(str(receipt["price"])))
                        # And: the unmodified kit payer checks the checkout and pays.
                        paid = await anyio.to_thread.run_sync(
                            partial(
                                kit.acp_pay,
                                order,
                                public,
                                kit.pub_b64(shop_key),
                                receipt,
                                buyer_key,
                                outcome.accept,
                            )
                        )
                        # Then: settled; the shipped unit, claim and certificate came
                        # from the receipt our seller sent through the proxy.
                        assert paid is not None
                        session = next(iter(proxy.acp.sessions.values()))
                        assert session["status"] == "completed"
                        assert session["unit"] == stock[0]["unit"]
                        assert session["fulfillment"]["claim"] == "A"
                        assert session["fulfillment"]["cert"]["cert"]["sku"] == KIMCHI
                        assert "warranty_hours" not in session["fulfillment"]
                        assert ledger.events[-1]["kind"] == "settle"
                        assert ledger.events[-1]["data"]["amount"] == receipt["price"]
                        assert ledger.balances["shop"] == receipt["price"]
                        assert 38000 <= receipt["price"] <= 49400
                    finally:
                        proxy.server.should_exit = True

    anyio.run(run)


def test_kit_payer_refuses_when_our_accept_policy_is_not_met() -> None:
    # Given: a certified unit with too little shelf life for the order.
    shop_key, buyer_key, market_key = (Ed25519PrivateKey.generate() for _ in range(3))
    now = time.time()
    stock = [unit(KIMCHI, 1, grade="C", now=now, shelf_left_ratio=0.02)]
    body = {
        "unit": stock[0]["unit"],
        "sku": KIMCHI,
        "grade": "C",
        "expires_at": stock[0]["expires_at"],
    }
    stock[0]["cert"] = {
        "cert": body,
        "sig": base64.b64encode(market_key.sign(kit.canon(body))).decode(),
    }
    ledger = LocalLedger(shop_key, buyer_key, lambda: int(now))
    ledger.inventory.update({"round": 3, "units": stock})
    ledger.balances["buyer"] = 10_000_000

    async def run() -> None:
        async with live_app(lambda _url: ledger.app()) as market:
            with create_settlement_client(market) as settlement_http:
                shop = kit.AcpShop(
                    shop_key, SettlementTransport(settlement_http, "local-shop")
                )
                proxy = kit.Proxy(kit.free_port())
                proxy.acp = shop
                public = proxy.public
                try:
                    order = ledger.issue(
                        public,
                        budget=60000,
                        item="김치",
                        acceptable=[KIMCHI],
                        kind="food",
                        use_within_hours=24,
                        shelf_hours=720,
                        market_pubkey=kit.pub_b64(market_key),
                    )
                    receipt = {
                        "shop": "R3 Local Shop",
                        "item": KIMCHI,
                        "price": 20000,
                        "task": "task-c",
                    }
                    receipt["sig_seller"] = base64.b64encode(
                        shop_key.sign(kit.canon(receipt))
                    ).decode()
                    why: list[str] = []
                    paid = await anyio.to_thread.run_sync(
                        partial(
                            kit.acp_pay,
                            order,
                            public,
                            kit.pub_b64(shop_key),
                            receipt,
                            buyer_key,
                            {"cert": True, "min_hours_left": 25.0},
                            why,
                        )
                    )
                    # Then: the kit pays nothing and says why; no credits move.
                    assert paid is None
                    assert any("shelf life" in reason for reason in why)
                    assert not ledger.requests
                finally:
                    proxy.server.should_exit = True

    anyio.run(run)


@pytest.mark.parametrize("acceptable", [None, [KIMCHI]])
def test_local_ledger_fixture_accepts_round3_items(
    acceptable: list[str] | None,
) -> None:
    ledger = LocalLedger(
        Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate(), lambda: 0
    )
    order = ledger.issue("http://shop.test", item="김치", acceptable=acceptable)
    assert order["item"] == "김치"
    assert ("acceptable" in order) is (acceptable is not None)
