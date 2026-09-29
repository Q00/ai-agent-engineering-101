"""Shared by the host, the runner and auth_checks: the market's address, the admin routes,
and an MCP client that carries one party's bearer token."""
import os

import httpx2 as httpx   # the HTTP client the MCP SDK v2 ships with
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

MARKET = os.environ.get("MARKET_URL", "http://127.0.0.1:8001")
ADMIN = {"X-Admin-Token": os.environ.get("MARKET_ADMIN_TOKEN", "")}


def party_client(token: str | None) -> Client:
    """An MCP client for {MARKET}/mcp. The token goes in the Authorization header, never in a
    tool argument, so the model never sees it."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return Client(streamable_http_client(f"{MARKET}/mcp",
                                         http_client=create_mcp_http_client(headers=headers)))


def open_negotiation(item: str, reserve: int, budget: int, condition: str) -> dict:
    r = httpx.post(f"{MARKET}/admin/negotiations", headers=ADMIN, timeout=10,
                   json={"item": item, "reserve": reserve, "budget": budget, "condition": condition})
    r.raise_for_status()
    return r.json()


def state(nid: str) -> dict:
    r = httpx.get(f"{MARKET}/admin/negotiations/{nid}", headers=ADMIN, timeout=10)
    r.raise_for_status()
    return r.json()


def pass_turn(nid: str) -> dict:
    r = httpx.post(f"{MARKET}/admin/negotiations/{nid}/pass", headers=ADMIN, timeout=10)
    r.raise_for_status()
    return r.json()


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")
