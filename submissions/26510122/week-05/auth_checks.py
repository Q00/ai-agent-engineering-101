#!/usr/bin/env python3
"""Run and record the four authentication/authorization checks required in Week 05."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import urllib.error
import urllib.request

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from market_host import result_text
from run import local_server, request_json


ROOT = Path(__file__).resolve().parent
PORT = 8766
SCENARIO = {"id": "auth", "item": "test item", "reserve": 80, "budget": 120}


async def call(server_url: str, token: str, tool: str, arguments: dict) -> tuple[bool, str]:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    transport = streamable_http_client(f"{server_url}/mcp", http_client=http)
    async with http, Client(transport) as client:
        result = await client.call_tool(tool, arguments)
        return bool(result.is_error), " ".join(result_text(result).split())


def open_negotiation(server_url: str, admin_key: str) -> dict:
    return request_json(
        f"{server_url}/admin/open",
        admin_key=admin_key,
        payload={"scenario": SCENARIO, "condition": "server_inject"},
    )


def main() -> int:
    lines: list[str] = []
    with local_server(PORT) as (server_url, admin_key):
        request = urllib.request.Request(
            f"{server_url}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("request without token unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            challenge = exc.headers.get("WWW-Authenticate", "")
            assert exc.code == 401 and challenge
            lines.append(f"no token: HTTP {exc.code}; WWW-Authenticate: {challenge}")

        first = open_negotiation(server_url, admin_key)
        second = open_negotiation(server_url, admin_key)
        error, message = asyncio.run(
            call(
                server_url,
                first["tokens"]["buyer"],
                "get_negotiation",
                {"negotiation_id": second["negotiation_id"]},
            )
        )
        assert error and "another negotiation" in message
        lines.append(f"wrong negotiation: tool error; {message}")

        turn_case = open_negotiation(server_url, admin_key)
        error, message = asyncio.run(
            call(
                server_url,
                turn_case["tokens"]["seller"],
                "propose",
                {"negotiation_id": turn_case["negotiation_id"], "price": 100},
            )
        )
        assert error and "buyer's turn" in message
        lines.append(f"out of turn: tool error; {message}")

        limit_case = open_negotiation(server_url, admin_key)
        error, message = asyncio.run(
            call(
                server_url,
                limit_case["tokens"]["buyer"],
                "propose",
                {"negotiation_id": limit_case["negotiation_id"], "price": 150},
            )
        )
        assert error and "token limit 120" in message
        lines.append(f"outside token limit: tool error; {message}")

    output = "\n".join(lines) + "\n"
    (ROOT / "auth_checks.txt").write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
