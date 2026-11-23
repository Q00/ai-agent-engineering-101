import json
import os

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def market_url():
    return os.environ.get("MARKET_URL", "http://127.0.0.1:8001")


def _admin_headers():
    return {"X-Admin-Key": os.environ.get("MARKET_ADMIN_KEY", "")}


def party_client(token):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    return Client(streamable_http_client(f"{market_url()}/mcp", http_client=http))


def result_text(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


async def admin_open(scenario, condition):
    async with httpx2.AsyncClient(headers=_admin_headers()) as http:
        r = await http.post(f"{market_url()}/admin/open", json={"scenario": scenario, "condition": condition})
        r.raise_for_status()
        return r.json()


async def admin_state(negotiation_id):
    async with httpx2.AsyncClient(headers=_admin_headers()) as http:
        r = await http.get(f"{market_url()}/admin/state/{negotiation_id}")
        r.raise_for_status()
        return json.loads(r.text)
