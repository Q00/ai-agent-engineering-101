"""Local settlement simulator, not the instructor's production market verifier."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from cryptography.exceptions import InvalidSignature
from pydantic import JsonValue, TypeAdapter
from scripts import market_kit as kit
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from r3.models import PaymentData

if TYPE_CHECKING:
    from collections.abc import Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from starlette.requests import Request

    from r3.models import SignedMandate

JSON: Final = TypeAdapter(dict[str, JsonValue])


class Settlement(PaymentData):
    """The course's settlement request, parsed before any wallet mutation."""

    checkout_jwt: str


@dataclass(frozen=True, slots=True)
class LocalLedger:
    """Intentionally mutate isolated orders, balances and audit events per test."""

    shop_key: Ed25519PrivateKey = field(repr=False)
    buyer_key: Ed25519PrivateKey = field(repr=False)
    now: Callable[[], int]
    orders: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    used_orders: set[str] = field(default_factory=set)
    used_tasks: set[str] = field(default_factory=set)
    requests: list[Settlement] = field(default_factory=list)
    events: list[dict[str, JsonValue]] = field(default_factory=list)
    balances: dict[str, int] = field(default_factory=lambda: {"buyer": 1000, "shop": 0})

    def issue(self, shop: str, budget: int = 120) -> dict[str, JsonValue]:
        """Create private open mandates; expose only a commitment in public events."""
        identifier = f"local-order-{len(self.orders) + 1}"
        checkout: dict[str, JsonValue] = {
            "vct": "mandate.checkout.open.1",
            "order": identifier,
            "constraints": [
                {"type": "checkout.allowed_merchants", "merchants": [shop]},
                {"type": "checkout.line_items", "items": ["lamp"]},
            ],
        }
        payment: dict[str, JsonValue] = {
            "vct": "mandate.payment.open.1",
            "order": identifier,
            "constraints": [{"type": "payment.amount_range", "min": 0, "max": budget}],
        }
        order: dict[str, JsonValue] = {
            "order": identifier,
            "round": 3,
            "item": "lamp",
            "budget": budget,
            "expires": self.now() + 60,
            "shop": shop,
            "mandates": {
                "checkout": {"mandate": checkout},
                "payment": {"mandate": payment},
            },
        }
        self.orders[identifier] = order
        self.events.append(
            {
                "kind": "order",
                "data": {
                    "order": identifier,
                    "round": 3,
                    "item": "lamp",
                    "budget_commitment": hashlib.sha256(kit.canon(payment)).hexdigest(),
                },
            }
        )
        return order

    def signed_by(self, signed: SignedMandate, key: Ed25519PrivateKey) -> bool:
        """Verify a closed mandate without trusting the submitted claims."""
        try:
            key.public_key().verify(
                base64.b64decode(signed.sig, validate=True),
                kit.canon(signed.mandate),
            )
        except (InvalidSignature, ValueError):
            return False
        return True

    def settle(self, body: Settlement) -> JSONResponse:
        """Check the course's displayed invariants before changing balances."""
        self.requests.append(body)
        issued = self.orders[body.order]
        co = kit.jwt_payload(body.checkout_jwt, kit.pub_b64(self.shop_key))
        checkout = body.checkout_mandate.mandate
        payment = body.payment_mandate.mandate
        opens = JSON.validate_python(issued["mandates"])
        expected = {
            name: hashlib.sha256(
                kit.canon(JSON.validate_python(value)["mandate"])
            ).hexdigest()
            for name, value in opens.items()
        }
        digest = (
            base64.urlsafe_b64encode(
                hashlib.sha256(body.checkout_jwt.encode()).digest(),
            )
            .rstrip(b"=")
            .decode()
        )
        amount = TypeAdapter(int).validate_python(payment.get("amount"), strict=True)
        expires = TypeAdapter(int).validate_python(issued["expires"], strict=True)
        budget = TypeAdapter(int).validate_python(issued["budget"], strict=True)
        task = TypeAdapter(str).validate_python(co["task"]) if co else ""
        checks = {
            "round_3_order": issued["round"] == 3,
            "not_expired": self.now() < expires,
            "not_settled_before": body.order not in self.used_orders
            and task not in self.used_tasks,
            "checkout_signed_by_shop": co is not None,
            "checkout_matches_order": bool(co and co.get("item") == issued["item"]),
            "checkout_mandate_signed_by_buyer": self.signed_by(
                body.checkout_mandate, self.buyer_key
            ),
            "payment_mandate_signed_by_buyer": self.signed_by(
                body.payment_mandate, self.buyer_key
            ),
            "closed_mandates_types": checkout.get("vct") == "mandate.checkout.1"
            and payment.get("vct") == "mandate.payment.1",
            "bound_to_open_mandates": checkout.get("open_mandate")
            == expected["checkout"]
            and payment.get("open_mandate") == expected["payment"],
            "bound_to_this_checkout": checkout.get("checkout_hash") == digest
            and payment.get("checkout_hash") == digest
            and checkout.get("checkout_jwt") == body.checkout_jwt
            and checkout.get("order") == body.order
            and payment.get("order") == body.order,
            "payee_is_this_shop": payment.get("payee") == issued["shop"]
            and bool(co and co.get("shop_url") == issued["shop"]),
            "amount_is_checkout_total": bool(
                co
                and amount == co.get("price")
                and payment.get("currency") == co.get("currency") == "credits"
            ),
            "amount_in_range": 0 <= amount <= budget
            and amount <= self.balances["buyer"],
        }
        if not all(checks.values()):
            return JSONResponse({"settled": False, "checks": checks}, status_code=400)
        self.used_orders.add(body.order)
        self.used_tasks.add(task)
        self.balances["buyer"] -= amount
        self.balances["shop"] += amount
        sequence = len(self.used_orders)
        self.events.append(
            {
                "kind": "settle",
                "data": {
                    "order": body.order,
                    "amount": amount,
                    "checkout_hash": digest,
                },
            }
        )
        return JSONResponse(
            {
                "settled": True,
                "checks": checks,
                "ledger": sequence,
                "mandate_receipt": {"order": body.order, "ledger": sequence},
            }
        )

    def app(self) -> Starlette:
        """Expose only loopback test settlement and public ledger reads."""

        async def settle(request: Request) -> JSONResponse:
            if request.headers.get("authorization") != "Bearer local-shop":
                return JSONResponse({"error": "local token required"}, status_code=403)
            return self.settle(Settlement.model_validate_json(await request.body()))

        async def ledger(_request: Request) -> JSONResponse:
            return JSONResponse(self.events)

        return Starlette(
            routes=[
                Route("/api/settle", settle, methods=["POST"]),
                Route("/api/ledger", ledger, methods=["GET"]),
            ]
        )
