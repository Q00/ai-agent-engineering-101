"""Describe our shop's ACP checkout interface."""

from starlette.responses import JSONResponse
import base64
import json
from pathlib import Path
import time
import uuid

from cryptography.hazmat.primitives import serialization

from seller_strategy import available_units


async def checkout_card(request):
    base_url = str(request.base_url).rstrip("/")

    tools = [
        {
            "name": "create_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions",
            "input": {
                "line_items": "[{id: item, quantity: 1}]",
                "receipt": "The receipt signed by our seller",
            },
        },
        {
            "name": "get_checkout_session",
            "method": "GET",
            "path": "/checkout_sessions/{id}",
            "input": {},
        },
        {
            "name": "complete_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions/{id}/complete",
            "input": {
                "payment_data": (
                    "{order, checkout_mandate, payment_mandate}"
                ),
            },
        },
        {
            "name": "cancel_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions/{id}/cancel",
            "input": {},
        },
    ]

    return JSONResponse({
        "protocol": "acp",
        "version": "2026-04-17",
        "endpoint": f"{base_url}/acp",
        "capabilities": {
            "services": ["checkout"],
            "payment_methods": ["ap2-mandate"],
        },
        "tools": tools,
    })
def encode_part(data):
    """Encode one JSON part for a signed checkout token."""
    raw = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_checkout(payload):
    """Sign the checkout using our existing shop identity."""
    key_path = Path(__file__).resolve().parent / "shop_key.pem"
    key = serialization.load_pem_private_key(
        key_path.read_bytes(),
        password=None,
    )

    header = encode_part({"alg": "EdDSA", "typ": "JWT"})
    body = encode_part(payload)
    message = f"{header}.{body}"

    signature = base64.urlsafe_b64encode(
        key.sign(message.encode("ascii"))
    ).rstrip(b"=").decode("ascii")

    return f"{message}.{signature}"


async def create_checkout_session(request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "Body must contain JSON."},
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "Body must be a JSON object."},
            status_code=400,
        )

    receipt = body.get("receipt")
    if not isinstance(receipt, dict):
        return JSONResponse(
            {"error": "A seller receipt is required."},
            status_code=400,
        )

    task_id = receipt.get("task")
    if not isinstance(task_id, str):
        return JSONResponse(
            {"error": "Receipt task must be a string."},
            status_code=400,
        )

    seller = request.app.state.seller
    original = seller.receipts.get(task_id)

    fields = ("shop", "item", "price", "task", "sig_seller")
    if original is None or any(
        receipt.get(field) != original.get(field)
        for field in fields
    ):
        return JSONResponse(
            {"error": "Receipt does not match one issued by this shop."},
            status_code=400,
        )

    expected_items = [{"id": original["item"], "quantity": 1}]
    if body.get("line_items") != expected_items:
        return JSONResponse(
            {"error": "Line items must match the agreed purchase."},
            status_code=400,
        )

    sessions = request.app.state.checkout_sessions
    if any(session["task"] == task_id for session in sessions.values()):
        return JSONResponse(
            {"error": "This receipt already has a checkout session."},
            status_code=409,
        )

    units = available_units(original["item"])
    unit = next(
        (
            candidate
            for candidate in units
            if candidate["unit"] == original.get("unit")
        ),
        None,
    )

    if unit is None:
        return JSONResponse(
            {"error": "The agreed unit is unavailable or expired."},
            status_code=409,
        )

    if any(
        session["unit"] == unit["unit"]
        and session["status"] == "ready_for_payment"
        for session in sessions.values()
    ):
        return JSONResponse(
            {"error": "This unit already has an open checkout."},
            status_code=409,
        )

    public_url = str(request.base_url).rstrip("/")
    session_id = "cs_" + uuid.uuid4().hex

    payload = {
        "id": session_id,
        "shop": original["shop"],
        "shop_url": public_url,
        "item": original["item"],
        "price": original["price"],
        "task": task_id,
        "currency": "credits",
        "iat": int(time.time()),
        "unit": unit["unit"],
        "cert": unit["cert"],
    }

    session = {
        "id": session_id,
        "status": "ready_for_payment",
        "task": task_id,
        "unit": unit["unit"],
        "line_items": expected_items,
        "totals": [
            {"type": "total", "amount": original["price"]},
        ],
        "capabilities": {
            "payment_methods": ["ap2-mandate"],
        },
        "fulfillment": {
            "unit": unit["unit"],
            "cert": unit["cert"],
        },
        "checkout_jwt": sign_checkout(payload),
    }

    sessions[session_id] = session
    return JSONResponse(session, status_code=201)