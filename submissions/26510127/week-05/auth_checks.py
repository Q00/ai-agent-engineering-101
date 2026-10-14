"""Four authorization checks against a running market server -> auth_checks.txt.

  python auth_checks.py            # starts its own server on :8001 (random admin token), writes auth_checks.txt
"""
import asyncio
import json
import secrets
import sys
from pathlib import Path

import httpx2

if hasattr(sys.stdout, "reconfigure"):          # Windows console (cp949) safety
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

import runner

URL = f"http://127.0.0.1:{runner.PORT}/mcp"
SCENARIO = {"id": "3", "item": "a pair of headphones", "reserve": 60, "budget": 55}


async def call(token: str, tool: str, args: dict) -> str:
    hc = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    async with Client(streamable_http_client(URL, http_client=hc)) as c:
        r = await c.call_tool(tool, args)
    await hc.aclose()
    text = " ".join(x.text for x in r.content if x.type == "text")
    return f"isError={str(r.is_error).lower()} {text}"


async def main():
    adm = "adm_" + secrets.token_urlsafe(24)
    server = runner.start_server(adm)
    lines = []
    try:
        # (1) no token -> HTTP 401 + WWW-Authenticate
        r = httpx2.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                        headers={"content-type": "application/json",
                                 "accept": "application/json, text/event-stream"}, timeout=10)
        lines.append(f"(1) no token: tools/list -> HTTP {r.status_code}; "
                     f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

        a = runner.admin("POST", "open", adm, json={"scenario": SCENARIO, "condition": "server_inject"})
        b = runner.admin("POST", "open", adm, json={"scenario": SCENARIO, "condition": "server_inject"})
        # (2) A's buyer token on negotiation B
        out = await call(a["tokens"]["buyer"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
        lines.append(f"(2) buyer token of {a['negotiation_id']} calls get_negotiation on {b['negotiation_id']} -> {out}")
        # (3) out of turn: the seller moves first, so a buyer propose is out of turn
        out = await call(a["tokens"]["buyer"], "propose", {"negotiation_id": a["negotiation_id"], "price": 50})
        lines.append(f"(3) buyer propose(50) while it is the seller's turn -> {out}")
        # (4) server condition, outside the token's limit: seller reserve 60 proposes 45
        out = await call(a["tokens"]["seller"], "propose", {"negotiation_id": a["negotiation_id"], "price": 45})
        lines.append(f"(4) server_inject, seller (token min 60) propose(45) -> {out}")
    finally:
        server.terminate()
    Path(runner.HERE / "auth_checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
