"""Mount the instructor's checkout beside our unchanged A2A seller."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from scripts import market_kit as kit
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from market_a2a.models import HarnessError
from r3.boundary import validate_checkout
from r3.kit_pin import KIT_SHA256

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

LOOPBACK: Final = ("127.0.0.1", "localhost", "::1")


def require_local(url: str) -> None:
    """Fail before network I/O if preparation targets a public endpoint."""
    parsed = urlsplit(url)
    if parsed.hostname not in LOOPBACK or parsed.scheme != "http":
        message = "R3 preparation permits only HTTP loopback endpoints"
        raise HarnessError(message)


@dataclass(frozen=True, slots=True)
class CheckoutBinding:
    """Bind one local public URL to its signing key and settlement transport."""

    public_url: str
    key: Ed25519PrivateKey = field(repr=False)
    market: kit.MarketPort = field(repr=False)


def create_gateway(seller: ASGIApp, binding: CheckoutBinding) -> Starlette:
    """Reuse the exact v3 checkout implementation; keep A2A at its original root."""
    require_local(binding.public_url)
    if (
        kit.KIT_VERSION != "3"
        or hashlib.sha256(Path(kit.__file__).read_bytes()).hexdigest() != KIT_SHA256
    ):
        message = "Instructor kit changed; re-audit before enabling checkout"
        raise HarnessError(message)
    checkout = kit.AcpShop(binding.key, binding.market)

    async def route(request: Request) -> Response:
        rejected = await validate_checkout(request)
        if rejected is not None:
            return rejected
        return await checkout.handle(
            request,
            request.url.path.lstrip("/"),
            await request.body(),
            binding.public_url.rstrip("/"),
        )

    return Starlette(
        routes=[
            Route("/.well-known/acp.json", route, methods=["GET"]),
            Route("/acp/checkout_sessions", route, methods=["POST"]),
            Route("/acp/checkout_sessions/{id}", route, methods=["GET"]),
            Route("/acp/checkout_sessions/{id}/complete", route, methods=["POST"]),
            Route("/acp/checkout_sessions/{id}/cancel", route, methods=["POST"]),
            Mount("/", app=seller),
        ]
    )
