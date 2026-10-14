"""The four checks against a running market server; writes auth_checks.txt.

  MARKET_ADMIN_TOKEN=<secret> python auth_checks.py
"""
import asyncio
import os
from pathlib import Path

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

MARKET = os.environ.get("MARKET_URL", "http://127.0.0.1:8001")
ADMIN = {"x-admin-token": os.environ.get("MARKET_ADMIN_TOKEN", "")}
SC = {"id": 1, "item": "a used bicycle", "reserve": 120, "budget": 150}


def open_neg(condition: str) -> dict:
    return httpx.post(f"{MARKET}/admin/open", headers=ADMIN, json={"scenario": SC, "condition": condition}).json()


async def call(token: str, name: str, args: dict) -> str:
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(f"{MARKET}/mcp", http_client=http)) as mcp:
        res = await mcp.call_tool(name, args)
        text = " ".join(c.text for c in res.content if c.type == "text")
        return f"isError={str(res.is_error).lower()} {text!r}"


async def main():
    lines = []
    r = httpx.post(f"{MARKET}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"content-type": "application/json", "accept": "application/json, text/event-stream"})
    lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a, b = open_neg("server_inject"), open_neg("server_inject")
    out = await call(a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} calls get_negotiation({b['negotiation_id']}) -> {out}")

    out = await call(a["seller_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 140})
    lines.append(f"(3) seller moves on the buyer's turn: propose({a['negotiation_id']}, 140) -> {out}")

    out = await call(a["buyer_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 180})
    lines.append(f"(4) server_inject, buyer budget 150 in the token: propose({a['negotiation_id']}, 180) -> {out}")

    Path(__file__).with_name("auth_checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
