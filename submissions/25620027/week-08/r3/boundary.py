"""Validate incoming checkout envelopes before the instructor implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError
from starlette.responses import JSONResponse

from r3.models import CompleteCheckout, CreateCheckout

if TYPE_CHECKING:
    from starlette.requests import Request


async def validate_checkout(request: Request) -> JSONResponse | None:
    """Accept only the advertised ACP methods and typed one-item requests."""
    path = request.url.path.rstrip("/")
    parts = path.split("/")
    match parts:
        case ["", ".well-known", "acp.json"]:
            method = "GET"
        case ["", "acp", "checkout_sessions"]:
            method = "POST"
        case ["", "acp", "checkout_sessions", str()]:
            method = "GET"
        case ["", "acp", "checkout_sessions", str(), "complete" | "cancel"]:
            method = "POST"
        case _:
            return JSONResponse({"error": "unknown ACP route"}, status_code=404)
    if request.method != method:
        return JSONResponse({"error": "ACP method not allowed"}, status_code=405)
    body = await request.body()
    try:
        if path == "/acp/checkout_sessions":
            purchase = CreateCheckout.model_validate_json(body)
            if purchase.line_items[0].id != purchase.receipt.item:
                return JSONResponse(
                    {"error": "item differs from receipt"}, status_code=400
                )
        if path.endswith("/complete"):
            _ = CompleteCheckout.model_validate_json(body)
    except ValidationError:
        return JSONResponse({"error": "invalid checkout envelope"}, status_code=400)
    return None
