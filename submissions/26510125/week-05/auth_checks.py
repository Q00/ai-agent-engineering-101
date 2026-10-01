"""Week 05 — the four auth checks against a running market, written to auth_checks.txt.

  (1) a request without a token      -> HTTP status + WWW-Authenticate header
  (2) a party token on another negotiation -> tool error
  (3) a move out of turn             -> tool error
  (4) server condition, move outside the token's limit -> refusal

Usage: MARKET_URL=http://127.0.0.1:8765 MARKET_ADMIN_TOKEN=... python auth_checks.py
(run_experiment.py can also start the server; see REPORT.md.)
"""
import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = os.environ.get("MARKET_URL", "http://127.0.0.1:8765")
ADMIN = {"X-Admin-Token": os.environ["MARKET_ADMIN_TOKEN"]}


def open_neg(condition, reserve=200, budget=260):
    r = httpx.post(f"{BASE}/admin/negotiations", headers=ADMIN, timeout=10,
                   json={"item": "road bicycle", "reserve": reserve, "budget": budget, "condition": condition})
    r.raise_for_status()
    return r.json()


async def call(token, tool, args):
    async with streamablehttp_client(f"{BASE}/mcp", headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            return ("ERROR " if res.isError else "OK ") + text


async def main():
    lines = []

    # (1) no token: a raw JSON-RPC initialize without Authorization
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check", "version": "0"}}}
    r = httpx.post(f"{BASE}/mcp", json=init, timeout=10,
                   headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
    lines.append(f"(1) no token -> HTTP {r.status_code}; WWW-Authenticate: {r.headers.get('www-authenticate', '<none>')}")

    # (2) a party token used on another negotiation
    a, b = open_neg("prompt"), open_neg("prompt")
    lines.append(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']} -> "
                 + await call(a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]}))

    # (3) a move out of turn: the buyer opens, so the seller moving first is out of turn
    lines.append(f"(3) seller proposes while it is the buyer's turn -> "
                 + await call(a["seller_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 300}))

    # (4) server condition: buyer proposes above its own budget (260) -> the market refuses
    c = open_neg("server")
    lines.append(f"(4) server condition, buyer (budget 260) proposes 320 -> "
                 + await call(c["buyer_token"], "propose", {"negotiation_id": c["negotiation_id"], "price": 320}))

    out = "\n".join(lines) + "\n"
    open("auth_checks.txt", "w", encoding="utf-8").write(out)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
