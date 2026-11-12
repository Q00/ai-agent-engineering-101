"""Week 05 — auth_checks.py: the four checks against a running market server.
No model involved. Writes auth_checks.txt (one line per check).

  MARKET_ADMIN_KEY=<secret> python market_server.py &
  MARKET_ADMIN_KEY=<secret> python auth_checks.py
"""
import os
import json
import asyncio

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

BASE = os.environ.get("MARKET_URL", "http://127.0.0.1:8100")
ADMIN = {"X-Admin-Key": os.environ["MARKET_ADMIN_KEY"]}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_checks.txt")


def open_negotiation(condition, item, reserve, budget):
    r = httpx.post(f"{BASE}/admin/open", headers=ADMIN, json={
        "item": item, "condition": condition, "buyer_limit": budget,
        "seller_limit": reserve, "inject_raised": max(reserve, budget) + 30})
    r.raise_for_status()
    return r.json()


async def call(token, tool, args):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as c:
        res = await c.call_tool(tool, args)
        return res.is_error, " ".join(x.text for x in res.content if x.type == "text")


async def main():
    lines = []

    # (1) no token at all: raw HTTP, so the status line and header are visible
    r = httpx.post(f"{BASE}/mcp", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Mcp-Method": "tools/list", "MCP-Protocol-Version": "2026-07-28"},
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}}}))
    lines.append(f"(1) no token: tools/list -> HTTP {r.status_code}; "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = open_negotiation("prompt_inject", "check item A", 400, 380)
    b = open_negotiation("prompt_inject", "check item B", 400, 380)

    # (2) a's buyer token used on b's negotiation
    err, text = await call(a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
                 f"get_negotiation -> isError={err}; {text}")

    # (3) seller moves first; the buyer opens, so this is out of turn
    err, text = await call(a["seller_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 500})
    lines.append(f"(3) seller propose(500) before the buyer's opening move in {a['negotiation_id']}: "
                 f"isError={err}; {text}")

    # (4) server condition: buyer (budget 380, carried in the token) proposes 430
    s = open_negotiation("server_inject", "check item S", 400, 380)
    err, text = await call(s["buyer_token"], "propose", {"negotiation_id": s["negotiation_id"], "price": 430})
    lines.append(f"(4) server_inject {s['negotiation_id']}, buyer token limit 380: propose(430) -> "
                 f"isError={err}; {text}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
