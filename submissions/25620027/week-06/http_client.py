"""Tuned HTTP clients shared by local A2A and the OpenAI adapter."""

from __future__ import annotations

import logging
import socket
from typing import Final

import httpx2

LOGGER: Final = logging.getLogger(__name__)
LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30,
)


async def _record_status(response: httpx2.Response) -> None:
    """Record method/path/status without credentials or request headers."""
    LOGGER.info(
        "HTTP %s %s -> %s",
        response.request.method,
        response.request.url.path,
        response.status_code,
    )


def create_client(*, model: bool = False) -> httpx2.AsyncClient:
    """Own this client with async with; the model gets a longer read timeout."""
    timeout = httpx2.Timeout(
        connect=5,
        read=90 if model else 30,
        write=10,
        pool=10,
    )
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=3,
        limits=LIMITS,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"response": [_record_status]},
    )
