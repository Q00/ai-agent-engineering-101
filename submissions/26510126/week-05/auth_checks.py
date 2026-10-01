"""Week 05 — the four checks against a running market, one line each -> auth_checks.txt.

  (1) a request without a token: HTTP status and WWW-Authenticate
  (2) a party token on another negotiation
  (3) a move out of turn
  (4) in a server condition, a move outside the token's limit

  python auth_checks.py
"""
import json
import asyncio
import urllib.request
import urllib.error
from pathlib import Path

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from runner import Market, NO_PROXY

HERE = Path(__file__).resolve().parent


def no_token(url):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        "mcp-method": "tools/list", "mcp-protocol-version": "2026-07-28"})
    try:
        with NO_PROXY.open(req, timeout=10) as r:
            return r.status, r.headers.get("www-authenticate")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("www-authenticate")


async def call(url, token, tool, args):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(url, http_client=http)) as mcp:
        r = await mcp.call_tool(tool, args)
        return r.is_error, " ".join(c.text for c in r.content if c.type == "text")


def main():
    s = json.loads((HERE / "scenarios.json").read_text())[0]          # s1: reserve 60, budget 140
    lines = []
    with Market(8002) as m:
        a = m.admin_call("POST", "/admin/negotiations", {"scenario": s, "condition": "server"})
        b = m.admin_call("POST", "/admin/negotiations", {"scenario": s, "condition": "server"})
        status, www = no_token(m.url)
        lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {status}, WWW-Authenticate: {www}")
        err, text = asyncio.run(call(m.url, a["tokens"]["buyer"], "get_negotiation",
                                     {"negotiation_id": b["negotiation_id"]}))
        lines.append(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
                     f"get_negotiation -> isError={err}: {text}")
        err, text = asyncio.run(call(m.url, a["tokens"]["seller"], "propose",
                                     {"negotiation_id": a["negotiation_id"], "price": 120}))
        lines.append(f"(3) seller moves first (buyer opens): propose(120) -> isError={err}: {text}")
        err, text = asyncio.run(call(m.url, a["tokens"]["buyer"], "propose",
                                     {"negotiation_id": a["negotiation_id"], "price": 150}))
        lines.append(f"(4) server condition, buyer budget {s['budget']}: propose(150) -> "
                     f"isError={err}: {text}")
    (HERE / "auth_checks.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
