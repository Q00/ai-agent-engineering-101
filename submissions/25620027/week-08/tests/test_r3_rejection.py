"""Reject altered payment envelopes without changing the simulated wallet."""

from __future__ import annotations

import base64
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal, assert_never

import anyio
import pytest
from scripts import market_kit as kit

from tests.r3_ledger import JSON
from tests.r3_payment import closed_payment, signed_receipt
from tests.r3_scenario import scenario

if TYPE_CHECKING:
    from pydantic import JsonValue


class Damage(StrEnum):
    """Each case changes one authorization dimension."""

    AMOUNT = "amount_is_checkout_total"
    PAYEE = "payee_is_this_shop"
    HASH = "bound_to_this_checkout"
    OPEN = "bound_to_open_mandates"
    CURRENCY = "amount_is_checkout_total_currency"
    ORDER = "bound_to_this_checkout_order"
    TYPE = "closed_mandates_types"
    SIGNATURE = "payment_mandate_signed_by_buyer"
    EXPIRED = "not_expired"


CHANGES: Final[dict[Damage, tuple[str, JsonValue]]] = {
    Damage.AMOUNT: ("amount", 101),
    Damage.PAYEE: ("payee", "http://127.0.0.1:1/other-shop"),
    Damage.HASH: ("checkout_hash", "different-checkout"),
    Damage.OPEN: ("open_mandate", "different-open-authorization"),
    Damage.CURRENCY: ("currency", "different-currency"),
    Damage.ORDER: ("order", "different-order"),
    Damage.TYPE: ("vct", "mandate.payment.open.1"),
}


@pytest.mark.parametrize("damage", list(Damage))
def test_altered_authorization_does_not_settle(
    monkeypatch: pytest.MonkeyPatch,
    damage: Damage,
) -> None:
    # Given: genuine merchant checkout and two correctly signed closed mandates.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            original = await closed_payment(scene)
            data = JSON.validate_python(original["payment_data"])
            signed = JSON.validate_python(data["payment_mandate"])
            mandate = JSON.validate_python(signed["mandate"])
            signature_invalid = False
            match damage:
                case (
                    Damage.AMOUNT
                    | Damage.PAYEE
                    | Damage.HASH
                    | Damage.OPEN
                    | Damage.CURRENCY
                    | Damage.ORDER
                    | Damage.TYPE
                ):
                    key, value = CHANGES[damage]
                    mandate[key] = value
                case Damage.SIGNATURE:
                    signature_invalid = True
                case Damage.EXPIRED:
                    scene.clock[0] += 61
                case _:
                    assert_never(damage)
            signed["mandate"] = mandate
            signed["sig"] = base64.b64encode(
                scene.ledger.buyer_key.sign(kit.canon(mandate)),
            ).decode()
            if signature_invalid:
                signed["sig"] = base64.b64encode(bytes(64)).decode()
            data["payment_mandate"] = signed
            # When: submit the altered claims through the actual ACP endpoint.
            result = await scene.http.post(
                f"/acp/checkout_sessions/{original['session']}/complete",
                json={"payment_data": data},
            )
            # Then: rejection carries the failing invariant and cannot charge.
            assert result.status_code == 402
            body = JSON.validate_json(result.content)
            market = JSON.validate_python(body["market"])
            checks = JSON.validate_python(market["checks"])
            expected = damage.value.removesuffix("_currency").removesuffix("_order")
            assert checks[expected] is False
            assert scene.ledger.balances == {"buyer": 1000, "shop": 0}
            assert not scene.ledger.used_orders

    anyio.run(run)


@pytest.mark.parametrize(
    "variant", ["empty", "multiple", "quantity", "boolean", "item", "signature", "json"]
)
def test_invalid_checkout_never_reaches_settlement(
    monkeypatch: pytest.MonkeyPatch,
    variant: Literal[
        "empty", "multiple", "quantity", "boolean", "item", "signature", "json"
    ],
) -> None:
    # Given: a signed100 receipt with one altered request dimension.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            receipt = signed_receipt(scene)
            items = [{"id": "lamp", "quantity": 1}]
            match variant:
                case "empty":
                    items = []
                case "multiple":
                    items *= 2
                case "quantity":
                    items[0]["quantity"] = 2
                case "boolean":
                    items[0]["quantity"] = True
                case "item":
                    items[0]["id"] = "chair"
                case "signature":
                    receipt["sig_seller"] = base64.b64encode(bytes(64)).decode()
                case "json":
                    result = await scene.http.post(
                        "/acp/checkout_sessions", content="{"
                    )
                    assert result.status_code == 400
                    return
                case _:
                    assert_never(variant)
            # When: the gateway receives this buyer-supplied checkout.
            result = await scene.http.post(
                "/acp/checkout_sessions",
                json={
                    "line_items": items,
                    "receipt": receipt,
                },
            )
            # Then: invalid signatures or envelopes never become a payment.
            assert result.status_code == 400
            assert not scene.ledger.requests
            assert scene.ledger.balances == {"buyer": 1000, "shop": 0}

    anyio.run(run)


def test_get_cannot_cancel_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a fresh valid checkout session.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            payment = await closed_payment(scene)
            url = f"/acp/checkout_sessions/{payment['session']}"
            # When: try a state-changing GET, then a legitimate POST cancel.
            get = await scene.http.get(url + "/cancel")
            before = await scene.http.get(url)
            post = await scene.http.post(url + "/cancel")
            # Then: only the explicit state-changing method changes the session.
            assert get.status_code in (404, 405)
            assert before.json()["status"] == "ready_for_payment"
            assert post.json()["status"] == "canceled"
            assert not scene.ledger.requests

    anyio.run(run)
