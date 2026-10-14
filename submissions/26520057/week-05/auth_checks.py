"""Run the four auth checks against a running market server and write auth_checks.txt.

No model is involved: the MCP client calls the tools directly with the runner's tokens.
Usage (server already running, same MARKET_ADMIN_TOKEN):  python auth_checks.py
"""
import asyncio
import json
import os

import httpx2 as httpx   # the HTTP client the MCP SDK ships with
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

URL = os.environ.get("MARKET_URL", "http://127.0.0.1:8001")
ADMIN = {"x-admin-token": os.environ["MARKET_ADMIN_TOKEN"]}
SCENARIO = json.load(open("scenarios.json"))[0]


def open_negotiation(condition):
    r = httpx.post(f"{URL}/admin/open", headers=ADMIN,
                   json={"condition": condition, "scenario": SCENARIO})
    r.raise_for_status()
    return r.json()


async def call(token, tool, args):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(f"{URL}/mcp", http_client=http)) as c:
        res = await c.call_tool(tool, args)
        text = " ".join(x.text for x in res.content if x.type == "text")
        return f"isError={res.is_error} {text}"


async def main():
    lines = []
    # (1) no token
    r = httpx.post(f"{URL}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                   headers={"Mcp-Method": "tools/list"})
    lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a, b = open_negotiation("server_inject"), open_negotiation("server_inject")
    buyer_a, seller_a = a["tokens"]["buyer"], a["tokens"]["seller"]

    # (2) party token on another negotiation
    out = await call(buyer_a, "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} calls get_negotiation"
                 f"({b['negotiation_id']}) -> {out}")

    # (3) out of turn: the buyer moves first, so the seller proposing now is out of turn
    out = await call(seller_a, "propose", {"negotiation_id": a["negotiation_id"], "price": SCENARIO["reserve"] + 100})
    lines.append(f"(3) seller proposes {SCENARIO['reserve'] + 100} on the buyer's turn -> {out}")

    # (4) server condition: buyer proposes above the budget carried in its token
    over = SCENARIO["budget"] + 50
    out = await call(buyer_a, "propose", {"negotiation_id": a["negotiation_id"], "price": over})
    lines.append(f"(4) server_inject, buyer (token limit {SCENARIO['budget']}) proposes {over} -> {out}")

    text = "\n".join(lines) + "\n"
    print(text, end="")
    open("auth_checks.txt", "w").write(text)


if __name__ == "__main__":
    asyncio.run(main())
