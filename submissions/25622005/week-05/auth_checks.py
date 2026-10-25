"""Four checks against the running market server, one result line each -> auth_checks.txt.
Run: python auth_checks.py   (market_server.py must be up on port 8001)"""
import asyncio
import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BASE = "http://127.0.0.1:8001"
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


async def call(tok, name, **args):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {tok}"})
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as c:
        r = await c.call_tool(name, args)
        return ("isError=true " if r.is_error else "") + " ".join(x.text for x in r.content if x.type == "text")


async def main():
    lines = []
    async with httpx2.AsyncClient() as h:
        # (1) no token at all
        r = await h.post(f"{BASE}/mcp", headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream", "Mcp-Method": "tools/list"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": META}})
        lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                     f"WWW-Authenticate: {r.headers.get('www-authenticate')}")
        # two negotiations, server condition
        a = (await h.post(f"{BASE}/admin/negotiations", json={"item": "a desk lamp", "reserve": 30,
                          "budget": 45, "condition": "server"})).json()
        b = (await h.post(f"{BASE}/admin/negotiations", json={"item": "a used bicycle", "reserve": 120,
                          "budget": 150, "condition": "server"})).json()
    A, B = a["negotiation_id"], b["negotiation_id"]
    # (2) A's buyer token on negotiation B
    out = await call(a["tokens"]["buyer"], "get_negotiation", negotiation_id=B)
    lines.append(f"(2) party token on another negotiation: buyer token of {A} calls get_negotiation({B}) -> {out}")
    # (3) seller moves while it is the buyer's turn
    out = await call(a["tokens"]["seller"], "propose", negotiation_id=A, price=50)
    lines.append(f"(3) move out of turn: seller of {A} calls propose(50) on the buyer's turn -> {out}")
    # (4) server condition: buyer proposes above its token limit (budget 45)
    out = await call(a["tokens"]["buyer"], "propose", negotiation_id=A, price=60)
    lines.append(f"(4) outside the token limit (server condition, budget 45): buyer of {A} calls propose(60) -> {out}")
    with open("auth_checks.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))

asyncio.run(main())
