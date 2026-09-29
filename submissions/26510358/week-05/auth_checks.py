"""Exercise HTTP authentication and market authorization on a live server."""

import asyncio
import json
import os
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from market_host import result_text

BASE = os.getenv("MARKET_BASE", "http://127.0.0.1:18051")


async def call(url: str, token: str, name: str, args: dict):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                  timeout=30) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client)) as mcp:
            return await mcp.call_tool(name, args)


async def check(admin_key: str) -> list[str]:
    async with httpx2.AsyncClient(timeout=30) as http:
        response = await http.post(BASE + "/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream",
                     "Content-Type": "application/json"})
        challenge = response.headers.get("WWW-Authenticate", "")
        assert response.status_code == 401 and challenge, (response.status_code, challenge)
        lines = [f"no_token: HTTP {response.status_code}; WWW-Authenticate={challenge}"]
        tokens = []
        for _ in range(2):
            opened = await http.post(BASE + "/admin/open", headers={"X-Admin-Key": admin_key},
                                     json={"item": "auth probe", "reserve": 40,
                                           "budget": 45, "condition": "server_inject"})
            opened.raise_for_status()
            tokens.append(opened.json())
        first, second = tokens
        url = BASE + "/mcp"
        wrong = await call(url, first["tokens"]["buyer"], "get_negotiation",
                           {"negotiation_id": second["negotiation_id"]})
        assert wrong.is_error and "wrong negotiation id" in result_text(wrong)
        lines.append(f"wrong_id: tool_error={wrong.is_error}; {result_text(wrong)}")
        out_turn = await call(url, first["tokens"]["seller"], "propose",
                              {"negotiation_id": first["negotiation_id"], "price": 42})
        assert out_turn.is_error and "out of turn" in result_text(out_turn)
        lines.append(f"out_of_turn: tool_error={out_turn.is_error}; {result_text(out_turn)}")
        outside = await call(url, first["tokens"]["buyer"], "propose",
                             {"negotiation_id": first["negotiation_id"], "price": 46})
        assert outside.is_error and "outside token limit" in result_text(outside)
        lines.append(f"outside_token_limit: tool_error={outside.is_error}; {result_text(outside)}")
        return lines


if __name__ == "__main__":
    key = os.environ["MARKET_ADMIN_KEY"]
    lines = asyncio.run(check(key))
    Path(__file__).with_name("auth_checks.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
