"""The four auth checks against a running server; writes auth_checks.txt.

    MARKET_ADMIN_KEY=<secret> python server.py &
    MARKET_ADMIN_KEY=<secret> python auth_check.py [--url http://127.0.0.1:8765]

(1) no token -> HTTP 401 and WWW-Authenticate, before MCP sees the request
(2) a party token on another negotiation -> tool error
(3) a move out of turn -> tool error
(4) server condition: a move outside the token's limit -> tool error
"""
import argparse
import asyncio
import json
import os

import httpx2

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

ADMIN_KEY = os.environ["MARKET_ADMIN_KEY"]


def admin_open(base: str, condition: str, reserve=120, budget=150) -> dict:
    r = httpx2.post(f"{base}/admin/open", headers={"X-Admin-Key": ADMIN_KEY},
                    json={"item": "a used bicycle", "reserve": reserve, "budget": budget,
                          "condition": condition})
    r.raise_for_status()
    return r.json()


async def call(base: str, token: str, tool: str, args: dict):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(f"{base}/mcp", http_client=http)) as mcp:
        res = await mcp.call_tool(tool, args)
        text = "\n".join(c.text for c in res.content if c.type == "text")
        return res.is_error, text


async def main(base: str) -> list[str]:
    lines = []

    # (1) no token: plain JSON-RPC POST, no Authorization header
    r = httpx2.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401 and r.headers.get("www-authenticate", "").startswith("Bearer "), r
    lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = admin_open(base, "prompt_inject")
    b = admin_open(base, "prompt_inject")

    # (2) buyer token of negotiation a used on negotiation b
    err, text = await call(base, a["tokens"]["buyer"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    assert err and "not bound to negotiation" in text, text
    lines.append(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
                 f"get_negotiation -> isError={err}, {text}")

    # (3) the buyer opens, so a seller move first is out of turn
    err, text = await call(base, a["tokens"]["seller"], "propose",
                           {"negotiation_id": a["negotiation_id"], "price": 140})
    assert err and "it is the buyer's turn" in text, text
    lines.append(f"(3) seller moves first on {a['negotiation_id']}: propose 140 -> isError={err}, {text}")

    # (4) server condition: buyer token carries limit 150, buyer proposes 170
    s = admin_open(base, "server_inject")
    err, text = await call(base, s["tokens"]["buyer"], "propose",
                           {"negotiation_id": s["negotiation_id"], "price": 170})
    assert err and "above the limit carried in your token" in text and "150" not in text, text
    lines.append(f"(4) server_inject {s['negotiation_id']}, buyer token limit 150: propose 170 -> "
                 f"isError={err}, {text}")
    return lines


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default="auth_checks.txt")
    a = ap.parse_args()
    lines = asyncio.run(main(a.url))      # raises before writing if any check is not as expected
    for line in lines:
        print(line)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
