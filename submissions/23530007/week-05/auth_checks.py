"""Produce auth_checks.txt: four lines about how the RUNNING market server behaves.

Start the server first, then:
  MARKET_ADMIN_TOKEN=<secret> python auth_checks.py | tee auth_checks.txt

Each line comes from a real request; nothing is hard-coded except the ids used to ask.
"""
import os
import sys
import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("MARKET_URL", "http://127.0.0.1:8001")
ADMIN = {"Authorization": f"Bearer {os.environ['MARKET_ADMIN_TOKEN']}"}
SC = {"item": "a used bicycle", "reserve": 120, "budget": 150}


def open_negotiation(nid: str, condition: str) -> dict:
    r = httpx2.post(f"{URL}/admin/open", headers=ADMIN, timeout=10,
                    json={**SC, "negotiation_id": nid, "condition": condition})
    r.raise_for_status()
    return r.json()


async def call(token: str, tool: str, args: dict) -> str:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(f"{URL}/mcp", http_client=http)) as c:
        res = await c.call_tool(tool, args)
    text = "".join(b.text for b in res.content if b.type == "text")
    return f"{'error' if res.is_error else 'ok'}: {text}"


async def main():
    # 1. no token at all
    r = httpx2.post(f"{URL}/mcp", timeout=10, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    print(f"1. tokenless request: HTTP {r.status_code}, "
          f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = open_negotiation("check-a", "server")
    open_negotiation("check-b", "server")

    # 2. valid token, but for another negotiation
    out = await call(a["buyer_token"], "get_negotiation", {"negotiation_id": "check-b"})
    print(f"2. buyer token of check-a used on check-b: {out}")

    # 3. valid token and id, but it is the buyer's turn and the seller calls
    out = await call(a["seller_token"], "propose", {"negotiation_id": "check-a", "price": 130})
    print(f"3. seller proposes while it is the buyer's turn: {out}")

    # 4. server condition, buyer's token limit is the budget (150): propose above it
    out = await call(a["buyer_token"], "propose", {"negotiation_id": "check-a", "price": 155})
    print(f"4. buyer proposes 155 with a token limit of {SC['budget']}: {out}")


if __name__ == "__main__":
    asyncio.run(main())
