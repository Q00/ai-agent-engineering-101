"""Build correctly signed closed mandates before targeted negative mutations."""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Final

from pydantic import JsonValue, TypeAdapter
from scripts import market_kit as kit

if TYPE_CHECKING:
    from tests.r3_scenario import Scenario

JSON: Final = TypeAdapter(dict[str, JsonValue])


def signed_receipt(scene: Scenario, price: int = 100) -> dict[str, JsonValue]:
    """Provide a merchant-authorized test receipt for checkout failure tests."""
    receipt: dict[str, JsonValue] = {
        "shop": "R3 Local Sungjin Shop",
        "item": "lamp",
        "price": price,
        "task": f"local-task-{len(scene.ledger.orders)}",
    }
    receipt["sig_seller"] = base64.b64encode(
        scene.ledger.shop_key.sign(kit.canon(receipt)),
    ).decode()
    return receipt


async def closed_payment(scene: Scenario, budget: int = 120) -> dict[str, JsonValue]:
    """Open a real gateway session, then sign its exact checkout hash."""
    order = scene.ledger.issue(scene.shop, budget=budget)
    response = await scene.http.post(
        "/acp/checkout_sessions",
        json={
            "receipt": signed_receipt(scene),
            "line_items": [{"id": "lamp", "quantity": 1}],
        },
    )
    _ = response.raise_for_status()
    session = JSON.validate_json(response.content)
    token = TypeAdapter(str).validate_python(session["checkout_jwt"])
    digest = (
        base64.urlsafe_b64encode(hashlib.sha256(token.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    opens = JSON.validate_python(order["mandates"])
    checkout: dict[str, JsonValue] = {
        "vct": "mandate.checkout.1",
        "order": order["order"],
        "open_mandate": hashlib.sha256(
            kit.canon(JSON.validate_python(opens["checkout"])["mandate"])
        ).hexdigest(),
        "checkout_jwt": token,
        "checkout_hash": digest,
    }
    payment: dict[str, JsonValue] = {
        "vct": "mandate.payment.1",
        "order": order["order"],
        "open_mandate": hashlib.sha256(
            kit.canon(JSON.validate_python(opens["payment"])["mandate"])
        ).hexdigest(),
        "payee": scene.shop,
        "amount": 100,
        "currency": "credits",
        "checkout_hash": digest,
    }
    data: dict[str, JsonValue] = {"order": order["order"]}
    for name, mandate in (("checkout_mandate", checkout), ("payment_mandate", payment)):
        data[name] = {
            "mandate": mandate,
            "sig": base64.b64encode(
                scene.ledger.buyer_key.sign(kit.canon(mandate)),
            ).decode(),
        }
    return {"session": session["id"], "payment_data": data}
