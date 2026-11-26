"""Shared tuned httpx2 client factory for MCP, admin, and model traffic."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import httpx2

if TYPE_CHECKING:
    from collections.abc import Mapping

LIMITS = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
TIMEOUT = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
LONG_READ_TIMEOUT = httpx2.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)
SOCKET_OPTIONS: list[tuple[int, int, int]] = [
    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
]


def create_async_client(
    *,
    base_url: str = "",
    headers: Mapping[str, str] | None = None,
    long_read: bool = False,
) -> httpx2.AsyncClient:
    """Create an observable, bounded, HTTP/2-capable async client."""
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=3,
        limits=LIMITS,
        socket_options=SOCKET_OPTIONS,
    )
    timeout = LONG_READ_TIMEOUT if long_read else TIMEOUT
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        base_url=base_url,
        headers=headers or {},
        follow_redirects=True,
    )
