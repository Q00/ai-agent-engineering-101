"""Generate the four required Week 05 authorization checks.

Start market_server.py first. The output is exactly four result lines written to
auth_checks.txt.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

HERE = Path(__file__).resolve().parent
OUT = HERE / "auth_checks.txt"
BASE = os.getenv("MARKET_BASE_URL", "http://127.0.0.1:8001")
MCP_URL = BASE + "/mcp"
ADMIN_TOKEN = os.getenv("MARKET_ADMIN_TOKEN", "")


def admin_open(item: str, reserve: int, budget: int, condition: str) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE + "/admin/open",
        data=json.dumps(
            {"item": item, "reserve": reserve, "budget": budget, "condition": condition}
        ).encode("utf-8"),
        method="POST",
        headers={"x-admin-token": ADMIN_TOKEN, "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def text_result(result: Any) -> str:
    return " ".join(
        block.text if isinstance(block, TextContent) else str(block)
        for block in result.content
    ).replace("\n", " ")


async def call(token: str, tool: str, args: dict[str, Any]) -> Any:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http_client:
        transport = streamable_http_client(MCP_URL, http_client=http_client)
        async with Client(transport) as client:
            return await client.call_tool(tool, args)


async def main() -> None:
    if not ADMIN_TOKEN:
        raise SystemExit("Set MARKET_ADMIN_TOKEN first.")

    lines: list[str] = []

    # 1) No bearer token: auth middleware must stop the HTTP request with 401
    # and include WWW-Authenticate.
    async with httpx2.AsyncClient(timeout=30.0) as http:
        response = await http.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
    challenge = response.headers.get("www-authenticate", "<missing>")
    lines.append(f"1 no-token: status={response.status_code}; WWW-Authenticate={challenge}")

    # Open two unrelated negotiations.
    n1 = admin_open("auth-test-a", 60, 90, "server_inject")
    n2 = admin_open("auth-test-b", 45, 70, "server_inject")

    # 2) Token bound to n1 may not read n2.
    wrong = await call(
        n1["buyer_token"],
        "get_negotiation",
        {"negotiation_id": n2["negotiation_id"]},
    )
    lines.append(
        f"2 wrong-negotiation: is_error={wrong.is_error}; result={text_result(wrong)}"
    )

    # 3) Buyer makes one valid move, then the same buyer calls again out of turn.
    first = await call(
        n1["buyer_token"],
        "propose",
        {"negotiation_id": n1["negotiation_id"], "price": 80},
    )
    out_of_turn = await call(
        n1["buyer_token"],
        "propose",
        {"negotiation_id": n1["negotiation_id"], "price": 80},
    )
    lines.append(
        f"3 out-of-turn: first_error={first.is_error}; "
        f"second_error={out_of_turn.is_error}; result={text_result(out_of_turn)}"
    )

    # 4) In a server condition, the buyer token enforces budget=90.
    n3 = admin_open("auth-test-limit", 60, 90, "server_inject")
    outside = await call(
        n3["buyer_token"],
        "propose",
        {"negotiation_id": n3["negotiation_id"], "price": 91},
    )
    lines.append(
        f"4 outside-token-limit: is_error={outside.is_error}; result={text_result(outside)}"
    )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
