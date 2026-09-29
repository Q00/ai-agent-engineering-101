"""Week 05 HW: the four auth checks against a running market (python market_server.py).

Usage: MARKET_ADMIN_TOKEN=<secret> python auth_check.py > auth_checks.txt
No model is involved: the checks call the server directly with minted tokens.
"""
import asyncio
import os
import sys

import httpx2
import requests
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = f"http://127.0.0.1:{int(os.environ.get('MARKET_PORT', '8001'))}"
ADMIN = {"x-admin-token": os.environ["MARKET_ADMIN_TOKEN"]}


def open_neg(item, reserve, budget, server_limit):
    r = requests.post(f"{URL}/admin/open", headers=ADMIN, json={
        "item": item, "reserve": reserve, "budget": budget,
        "server_limit": server_limit, "inject": False})
    return r.json()


async def call(token, tool, args):
    transport = streamable_http_client(f"{URL}/mcp", http_client=httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}))
    async with Client(transport) as mcp:
        res = await mcp.call_tool(tool, args)
        text = " ".join(getattr(c, "text", "") for c in res.content)
        return f"isError={res.is_error} {text}"


async def main():
    # (1) no token
    r = requests.post(f"{URL}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                      headers={"Accept": "application/json, text/event-stream"})
    print(f"(1) no token: HTTP {r.status_code}, WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = open_neg("used bicycle", 300, 320, True)
    b = open_neg("used laptop", 800, 1000, True)

    # (2) party token of negotiation A used on negotiation B
    out = await call(a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    print(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: {out}")

    # (3) move out of turn: the buyer opens, so the seller moving first is out of turn
    out = await call(a["seller_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 310})
    print(f"(3) seller proposes before the buyer has moved: {out}")

    # (4) server condition, move outside the token's limit: buyer budget is 320
    out = await call(a["buyer_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 350})
    print(f"(4) server condition, buyer (token max 320) proposes 350: {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
