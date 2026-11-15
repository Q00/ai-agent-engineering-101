"""Describe our shop's ACP checkout interface."""

from starlette.responses import JSONResponse
import base64
import asyncio
import json
import os
from pathlib import Path
import time
import tempfile
import uuid

import httpx

from cryptography.hazmat.primitives import serialization

from seller_strategy import available_units


def load_checkout_state(path):
    if not path.exists():
        return {"version": 1, "receipts": {}, "sessions": {}, "receipt_expiry": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Unsupported checkout state format")
    for field in ("receipts", "sessions"):
        if not isinstance(state.get(field), dict):
            raise ValueError(f"Invalid checkout state: {field}")
    for task, receipt in state["receipts"].items():
        if not isinstance(receipt, dict) or receipt.get("task") != task \
                or type(receipt.get("price")) is not int or receipt["price"] < 0 \
                or not all(isinstance(receipt.get(field), str)
                           for field in ("shop", "item", "sig_seller")):
            raise ValueError("Invalid saved receipt")
    for sid, session in state["sessions"].items():
        if not isinstance(session, dict) or session.get("id") != sid \
                or session.get("status") not in {
                    "ready_for_payment", "payment_pending", "completed", "canceled"} \
                or not all(isinstance(session.get(field), str)
                           for field in ("task", "unit", "checkout_jwt")):
            raise ValueError("Invalid saved checkout session")
    state.setdefault("receipt_expiry", {})
    if not isinstance(state["receipt_expiry"], dict) or any(
            type(expiry) not in (int, float) for expiry in state["receipt_expiry"].values()):
        raise ValueError("Invalid receipt hold expiry")
    return state


def write_checkout_state(path, state):
    """Replace the saved state only after the full JSON has reached disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="checkout-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def checkout_storage_error(request, error):
    return JSONResponse(
        {"error": "Checkout storage failed; recover saved state before continuing."},
        status_code=503)


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
    if request.app.state.seller.checkout_storage_failed:
        return await checkout_storage_error(request, None)
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
    expiry = seller.receipt_expiry.get(task_id)
    if expiry is not None and expiry <= time.time():
        return JSONResponse({"error": "The receipt's checkout hold expired."}, status_code=409)

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
        and session["status"] in {"ready_for_payment", "payment_pending", "completed"}
        for session in sessions.values()
    ):
        return JSONResponse(
            {"error": "This unit already has an open checkout."},
            status_code=409,
        )

    if any(deal.get("unit", {}).get("unit") == unit["unit"]
           for deal in seller.open.values()):
        return JSONResponse(
            {"error": "This unit is being negotiated."}, status_code=409)

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
    seller.save_checkout_state()
    return JSONResponse(session, status_code=201)


async def get_checkout_session(request):
    if request.app.state.seller.checkout_storage_failed:
        return await checkout_storage_error(request, None)
    session_id = request.path_params["session_id"]
    session = request.app.state.checkout_sessions.get(session_id)
    if session is None:
        return JSONResponse(
            {"error": "Checkout session was not found."}, status_code=404)
    return JSONResponse(session)


async def cancel_checkout_session(request):
    if request.app.state.seller.checkout_storage_failed:
        return await checkout_storage_error(request, None)
    session_id = request.path_params["session_id"]
    lock = request.app.state.checkout_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        return cancel_session(request)


def cancel_session(request):
    session_id = request.path_params["session_id"]
    sessions = request.app.state.checkout_sessions
    session = sessions.get(session_id)
    if session is None:
        return JSONResponse(
            {"error": "Checkout session was not found."}, status_code=404)
    if session["status"] == "canceled":
        return JSONResponse(session)
    if session["status"] != "ready_for_payment":
        return JSONResponse(
            {"error": "This session cannot be canceled."}, status_code=409)

    session["status"] = "canceled"
    seller = request.app.state.seller
    seller.save_checkout_state()
    unit_id = session["unit"]
    negotiating = any(
        deal.get("unit", {}).get("unit") == unit_id
        for deal in seller.open.values()
    )
    another_checkout = any(
        other["unit"] == unit_id
        and other["status"] in {"ready_for_payment", "payment_pending", "completed"}
        for other in sessions.values()
    )
    if not negotiating and not another_checkout:
        seller.held.pop(unit_id, None)
    return JSONResponse(session)


async def submit_settlement(payload):
    """Send signed mandates to the market without importing the course kit."""
    market_url = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
    token = os.environ.get("MARKET_SHOP_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            market_url + "/api/settle", json=payload, headers=headers)


async def complete_checkout_session(request):
    if request.app.state.seller.checkout_storage_failed:
        return await checkout_storage_error(request, None)
    session_id = request.path_params["session_id"]
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "Body must contain JSON."}, status_code=400)
    payment = body.get("payment_data") if isinstance(body, dict) else None
    if not isinstance(payment, dict) or not isinstance(payment.get("order"), str) \
            or not payment["order"] or not all(
                isinstance(payment.get(field), dict) and payment[field]
                for field in ("checkout_mandate", "payment_mandate")):
        return JSONResponse(
            {"error": "Order and both signed mandates are required."}, status_code=400)

    lock = request.app.state.checkout_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        session = request.app.state.checkout_sessions.get(session_id)
        if session is None:
            return JSONResponse({"error": "Checkout session was not found."}, status_code=404)
        if session["status"] == "completed" \
                and session.get("settlement_order") == payment["order"]:
            return JSONResponse(session)
        if session["status"] != "ready_for_payment":
            return JSONResponse(
                {"error": "This session cannot accept payment.", "status": session["status"]},
                status_code=409)

        payload = {
            "order": payment["order"],
            "checkout_jwt": session["checkout_jwt"],
            "checkout_mandate": payment["checkout_mandate"],
            "payment_mandate": payment["payment_mandate"],
        }
        session["status"] = "payment_pending"
        session["settlement_order"] = payment["order"]
        request.app.state.seller.save_checkout_state()
        try:
            response = await submit_settlement(payload)
            result = response.json()
        except (httpx.HTTPError, ValueError):
            print("[acp] settlement outcome uncertain; payment remains pending", flush=True)
            return JSONResponse(
                {"error": "Settlement outcome is uncertain; verification is required."},
                status_code=503)

        if not isinstance(result, dict):
            return JSONResponse({"error": "Unexpected market reply; payment remains pending."},
                                status_code=502)
        if response.status_code != 200:
            checks = result.get("checks")
            if 400 <= response.status_code < 500 and isinstance(checks, dict) \
                    and checks and checks.get("not_settled_before") is not False:
                session["status"] = "ready_for_payment"
                session.pop("settlement_order", None)
                request.app.state.seller.save_checkout_state()
                print("[acp] settlement refused by market checks", flush=True)
                return JSONResponse({"error": "Payment refused by the market.",
                                     "market": result}, status_code=402)
            return JSONResponse({"error": "Market outcome needs verification; payment remains pending."},
                                status_code=503)

        receipt = result.get("mandate_receipt")
        if not isinstance(receipt, dict) or not receipt or result.get("settled") is False \
                or result.get("ledger") is None:
            return JSONResponse({"error": "Settlement confirmation is incomplete; payment remains pending."},
                                status_code=502)
        session["order"] = {"status": "confirmed", "mandate_receipt": receipt}
        session["status"] = "completed"
        request.app.state.seller.save_checkout_state()
        print(f"[acp] settled {session['line_items'][0]['id']} "
              f"at {session['totals'][0]['amount']}, ledger #{result['ledger']}, "
              f"unit {session['unit']} via student checkout", flush=True)
        return JSONResponse(session)
