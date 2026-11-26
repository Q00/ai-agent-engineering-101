"""Exercise the exact guarded proxy handler selected by the deployment runner."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import anyio
import httpx2
from pydantic import TypeAdapter
from scripts import market_kit as kit
from starlette.applications import Starlette
from starlette.routing import Route

from r3.kit_entry import GuardedProxy
from r3.transport import SettlementTransport, create_settlement_client
from tests.r3_ledger import JSON
from tests.r3_live import live_app
from tests.r3_payment import signed_receipt
from tests.r3_scenario import scenario

if TYPE_CHECKING:
    import pytest


def test_deployment_proxy_rejects_get_cancel_and_settles_real_v3_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same Proxy.handle override used at deployment, with real AcpShop.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            with create_settlement_client(scene.market) as client:
                proxy = object.__new__(GuardedProxy)
                proxy.acp = kit.AcpShop(
                    scene.ledger.shop_key, SettlementTransport(client, "local-shop")
                )
                app = Starlette(
                    routes=[
                        Route(
                            "/{path:path}",
                            proxy.handle,
                            methods=["GET", "POST", "DELETE"],
                        )
                    ]
                )
                async with live_app(lambda _url: app) as shop:
                    # Proxy's ACP branch reads its advertised public origin.
                    proxy.public = shop
                    async with httpx2.AsyncClient(base_url=shop) as http:
                        receipt = signed_receipt(scene)
                        created = await http.post(
                            "/acp/checkout_sessions",
                            json={
                                "line_items": [{"id": "lamp", "quantity": 1}],
                                "receipt": receipt,
                            },
                        )
                        session = TypeAdapter(str).validate_python(
                            JSON.validate_json(created.content)["id"]
                        )
                        url = f"/acp/checkout_sessions/{session}"
                        # When: issue a GET cancel against the real proxy adapter.
                        refused = await http.get(url + "/cancel")
                        still_open = await http.get(url)
                        assert refused.status_code == 405
                        assert still_open.json()["status"] == "ready_for_payment"
                        assert not scene.ledger.requests
                        _ = await http.post(url + "/cancel")
                    # Then: fresh correctly signed payment still works on this path.
                    order = scene.ledger.issue(shop)
                    paid = await anyio.to_thread.run_sync(
                        partial(
                            kit.acp_pay,
                            order,
                            shop,
                            kit.pub_b64(scene.ledger.shop_key),
                            signed_receipt(scene),
                            scene.ledger.buyer_key,
                        )
                    )
                    assert paid is not None
                    assert scene.ledger.balances == {"buyer": 900, "shop": 100}

    anyio.run(run)
