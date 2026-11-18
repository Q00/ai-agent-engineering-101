"""Four auth checks against a running market server. No model calls.

Run:  MARKET_ADMIN_KEY=<key> python auth_check.py   (writes auth_checks.txt)
"""
import asyncio
import json
from pathlib import Path

import httpx2

from mcp_client import admin_open, market_url, party_client, result_text

SCENARIO = {"id": 1, "item": "a used bicycle", "reserve": 80, "budget": 120}


async def call(token, tool, args):
    async with party_client(token) as c:
        r = await c.call_tool(tool, args)
        return ("tool error: " if r.is_error else "ok: ") + result_text(r)


async def main():
    lines = []

    async with httpx2.AsyncClient() as http:
        r = await http.post(f"{market_url()}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    lines.append(f"(1) no token: POST /mcp -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = await admin_open(SCENARIO, "server_inject")
    b = await admin_open(SCENARIO, "server_inject")
    out = await call(a["tokens"]["buyer"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} calls get_negotiation({b['negotiation_id']}) -> {out}")

    out = await call(a["tokens"]["seller"], "propose", {"negotiation_id": a["negotiation_id"], "price": 100})
    lines.append(f"(3) seller moves first in {a['negotiation_id']} (buyer's turn): propose(100) -> {out}")

    out = await call(a["tokens"]["buyer"], "propose", {"negotiation_id": a["negotiation_id"], "price": 150})
    lines.append(f"(4) server_inject, buyer budget 120: propose(150) -> {out}")

    text = "\n".join(lines) + "\n"
    print(text, end="")
    Path(__file__).with_name("auth_checks.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
