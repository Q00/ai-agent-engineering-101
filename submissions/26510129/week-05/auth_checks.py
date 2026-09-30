"""The four checks against the running market, written to auth_checks.txt.

  (1) a request without a token            -> HTTP status and WWW-Authenticate header
  (2) a party token on another negotiation -> tool error
  (3) a move out of turn                   -> tool error
  (4) server condition, a move outside the token's limit -> tool error (refused)

Usage (market_server.py running, same MARKET_ADMIN_TOKEN):  python auth_checks.py
"""
import asyncio
from pathlib import Path

import httpx2 as httpx   # the HTTP client the MCP SDK v2 ships with

from market_client import MARKET, open_negotiation, party_client, text_of

OUT = Path(__file__).with_name("auth_checks.txt")
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def one_line(s: str) -> str:
    return " ".join(s.split())


async def main():
    lines = []

    r = httpx.post(f"{MARKET}/mcp", timeout=10,
                   headers={"Content-Type": "application/json",
                            "Accept": "application/json, text/event-stream",
                            "Mcp-Method": "tools/list", "MCP-Protocol-Version": "2026-07-28"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": META}})
    lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}; "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = open_negotiation("a desk lamp", 30, 38, "server_inject")
    b = open_negotiation("a desk lamp", 30, 38, "server_inject")
    async with party_client(a["tokens"]["buyer"]) as buyer_a:
        res = await buyer_a.call_tool("get_negotiation", {"negotiation_id": b["negotiation_id"]})
        lines.append(f"(2) buyer token of {a['negotiation_id']} calls get_negotiation"
                     f"({b['negotiation_id']}) -> isError={res.is_error}: {one_line(text_of(res))}")

    async with party_client(a["tokens"]["buyer"]) as buyer_a:
        res = await buyer_a.call_tool("propose", {"negotiation_id": a["negotiation_id"], "price": 32})
        lines.append(f"(3) buyer proposes 32 in {a['negotiation_id']} while it is the seller's turn "
                     f"(the seller opens) -> isError={res.is_error}: {one_line(text_of(res))}")

    async with party_client(a["tokens"]["seller"]) as seller_a:   # setup, not a check: hand the turn over
        await seller_a.call_tool("propose", {"negotiation_id": a["negotiation_id"], "price": 36})

    async with party_client(a["tokens"]["buyer"]) as buyer_a:
        res = await buyer_a.call_tool("propose", {"negotiation_id": a["negotiation_id"], "price": 45})
        lines.append(f"(4) server_inject, buyer (budget 38 in its token) proposes 45 in "
                     f"{a['negotiation_id']} -> isError={res.is_error}: {one_line(text_of(res))}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
