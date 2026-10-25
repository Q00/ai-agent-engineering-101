"""Check the running week-05 market without any LLM calls."""

import argparse
import asyncio
import os
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def rejected(url, token, tool, arguments, expected):
    async with streamablehttp_client(
        url, headers={"Authorization": "Bearer " + token}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            message = " ".join(
                getattr(block, "text", "") for block in result.content
            )
            message = " ".join(message.split())
            if not result.isError or expected not in message:
                raise RuntimeError(f"Unexpected response: isError={result.isError}; {message}")
            return f"isError=true; {message}"


async def check(args):
    token = os.environ.get("MARKET_ADMIN_TOKEN", "")
    if not token:
        raise ValueError("Set MARKET_ADMIN_TOKEN to the server's admin token")
    url = args.market_url.rstrip("/")
    base = url.removesuffix("/mcp")
    lines = []
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "auth-check", "version": "1.0"}},
        }, headers={"Accept": "application/json, text/event-stream"})
        challenge = response.headers.get("WWW-Authenticate", "")
        if response.status_code != 401 or not challenge:
            raise RuntimeError(f"Expected HTTP 401 and WWW-Authenticate; got {response.status_code}")
        lines.append(f"1. No token: HTTP {response.status_code}; WWW-Authenticate: {challenge}")

        async def open_check():
            response = await client.post(base + "/admin/open", headers={
                "Authorization": "Bearer " + token,
            }, json={"condition": "server", "scenario": {
                "id": 0, "item": "authentication check", "reserve": 30, "budget": 45,
            }})
            response.raise_for_status()
            return response.json()

        first = await open_check()
        second = await open_check()

    lines.append("2. Other negotiation_id: " + await rejected(
        url, first["buyer_token"], "get_negotiation",
        {"negotiation_id": second["negotiation_id"]}, "token belongs to another negotiation",
    ))
    lines.append("3. Seller moves during buyer turn: " + await rejected(
        url, first["seller_token"], "propose",
        {"negotiation_id": first["negotiation_id"], "price": 35}, "it is not your turn",
    ))
    lines.append("4. Server condition, buyer budget=45, propose price=46: " + await rejected(
        url, first["buyer_token"], "propose",
        {"negotiation_id": first["negotiation_id"], "price": 46}, "refused by the market",
    ))
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line, flush=True)
    print(f"[done] All 4 checks passed; saved to {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "auth_checks.txt")
    try:
        asyncio.run(check(parser.parse_args()))
    except Exception as error:
        raise SystemExit(f"[auth check error] {type(error).__name__}: {error}") from error
