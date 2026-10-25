"""Four checks against the running market, written to auth_checks.txt.

  MARKET_ADMIN_TOKEN=<secret> python auth_checks.py

(1) a request without a token: the HTTP status and the WWW-Authenticate header;
(2) a party token used on another negotiation;
(3) a move out of turn;
(4) in a server condition, a move outside the token's limit.
Checks 2 to 4 go through the MCP client like the hosts do, so what is written
is the tool result the model would read.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

HERE = Path(__file__).resolve().parent
MARKET = os.environ.get("MARKET_URL", "http://127.0.0.1:8001").rstrip("/")
ADMIN = {"X-Admin-Token": os.environ.get("MARKET_ADMIN_TOKEN", "")}
KST = timezone(timedelta(hours=9))
BIKE = {"id": "bike", "item": "a used city bicycle", "reserve": 150, "budget": 220}
BOOK = {"id": "book", "item": "a linear algebra textbook", "reserve": 40, "budget": 60}


def open_negotiation(scenario, condition):
    r = httpx.post(MARKET + "/admin/open", headers=ADMIN, json={"scenario": scenario, "condition": condition})
    r.raise_for_status()
    return r.json()


async def call(token, name, args):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(MARKET + "/mcp", http_client=http)) as mcp:
            r = await mcp.call_tool(name, args)
            text = "\n".join(c.text for c in r.content if c.type == "text")
            return ("tool error" if r.is_error else "ok") + f": {text}"


async def main():
    lines = []
    # (1) no token at all, a tools/list request shaped as the hosts send it
    r = httpx.post(MARKET + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                          "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                               "io.modelcontextprotocol/clientCapabilities": {}}}},
                   headers={"Accept": "application/json, text/event-stream", "Mcp-Method": "tools/list",
                            "MCP-Protocol-Version": "2026-07-28"})
    lines.append(f"(1) no token: POST {MARKET}/mcp tools/list -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")
    # (2) a party token on another negotiation
    a = open_negotiation(BIKE, "server")
    b = open_negotiation(BOOK, "server")
    out = await call(a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} calling get_negotiation({b['negotiation_id']}) -> {out}")
    # (3) a move out of turn: the seller moves first
    out = await call(a["seller_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 200})
    lines.append(f"(3) seller of {a['negotiation_id']} calling propose(200) on the buyer's turn -> {out}")
    # (4) server condition: the buyer (budget 220) proposes 300
    out = await call(a["buyer_token"], "propose", {"negotiation_id": a["negotiation_id"], "price": 300})
    lines.append(f"(4) server condition, buyer of {a['negotiation_id']} (token limit 220) calling propose(300) -> {out}")

    stamp = f"# auth checks against {MARKET}, {datetime.now(KST).isoformat(timespec='minutes')}, server conditions"
    text = "\n".join([stamp, *lines]) + "\n"
    (HERE / "auth_checks.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
