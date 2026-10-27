"""Own the local settlement HTTP connection used by the course checkout."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import httpx2

from http_client import LIMITS
from market_a2a.models import HarnessError
from r3.gateway import require_local

if TYPE_CHECKING:
    from pydantic import JsonValue

SETTLE: Final = "/api/settle"


def create_settlement_client(base: str) -> httpx2.Client:
    """Caller owns this loopback-only client with a context manager."""
    require_local(base)
    return httpx2.Client(
        base_url=base,
        http2=True,
        limits=LIMITS,
        timeout=httpx2.Timeout(connect=5, read=30, write=10, pool=10),
        # Never replay an uncertain settlement; the local verifier owns replay checks.
        transport=httpx2.HTTPTransport(
            http2=True,
            retries=0,
            limits=LIMITS,
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        ),
        follow_redirects=False,
        trust_env=False,
    )


@dataclass(frozen=True, slots=True)
class SettlementTransport:
    """Only the checkout settlement operation can cross this local boundary."""

    client: httpx2.Client = field(repr=False)
    token: str = field(repr=False)

    # Instructor Market.call ABI requires all four operation arguments.
    def call(
        self,
        method: str,
        path: str,
        role: str = "shop",
        *,
        json: dict[str, JsonValue],
    ) -> httpx2.Response:
        """Forward one settlement; reject registration, orders and other roles."""
        if method != "POST" or path != SETTLE or role != "shop":
            message = "Preparation transport permits only shop POST /api/settle"
            raise HarnessError(message)
        return self.client.post(
            SETTLE,
            json=json,
            headers={"Authorization": f"Bearer {self.token}"},
        )
