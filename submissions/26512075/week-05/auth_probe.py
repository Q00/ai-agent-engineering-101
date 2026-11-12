"""Auth boundary probe against a running market server.



Creates two negotiations through /admin/open so the tokens match the server,
then checks the four boundaries with the MCP client.

  python auth_probe.py
  python auth_probe.py --base http://127.0.0.1:8001
"""
from __future__ import annotations
import argparse
import asyncio

import json
from pathlib import Path
import httpx
import httpx2

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

def open_game(base: str, item: str, seller_reserve: int, buyer_budget: int) -> dict:
    response = httpx.post(
        f"{base}/admin/open",
        json={
            "item": item,
            "seller_reserve": seller_reserve,
            "buyer_budget": buyer_budget,
            "condition": "server",
            "inject": False,
        },
        timeout=15.0,
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError(f"/admin/open returned an empty body: HTTP {response.status_code}")
    try:
        opened = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"/admin/open did not return JSON: HTTP {response.status_code} {response.text[:200]}"
        ) from exc
    return {
        "run": "checks",
        "negotiation_id": opened["negotiation_id"],
        "turn": opened.get("turn", "seller"),
        "buyer_limit": buyer_budget,
        "seller_limit": seller_reserve,
    }


def prepare_state(base: str) -> list[dict]:
    games = [
        open_game(base, "probe-own", seller_reserve=70, buyer_budget=90),
        open_game(base, "probe-foreign", seller_reserve=10, buyer_budget=20),
    ]

    return games


async def probe(url: str, games: list[dict], output: Path) -> None:
    checks = [game for game in games if game["run"] == "checks"]
    if len(checks) != 2:
        raise RuntimeError("expected two checks sessions")
    own, foreign = checks

    async with httpx.AsyncClient() as raw:
        response = await raw.post(
            url,
            headers={
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/list",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": {"io.modelcontextprotocol/clientCapabilities": {}}},
            },
        )
    lines = [
        f"(1) tokenless: HTTP {response.status_code}; "
        f"WWW-Authenticate: {response.headers.get('WWW-Authenticate', '<missing>')}"
    ]
    if response.status_code != 401 or not response.headers.get("WWW-Authenticate"):
        raise RuntimeError(lines[0])

    async def client(token: str):
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx2.Timeout(30.0, read=300.0),
        )
        return http_client, Client(streamable_http_client(url, http_client=http_client))

    buyer_http, buyer = await client(own["tokens"]["buyer"])
    seller_http, seller = await client(own["tokens"]["seller"])
    async with buyer_http, seller_http, buyer, seller:
        # r2
        foreign_result = await seller.call_tool(
            "propose",
            {"nid": foreign["negotiation_id"], "price": 15},
        )
        lines.append(
            "(2) foreign negotiation_id: "
            f"tool error={foreign_result.is_error} { _text(foreign_result) }"
        )

        # r3
        wrong_result = await seller.call_tool(
            "propose",
            {"nid": own["negotiation_id"], "price": 70},
        )
        lines.append(
            "(3) wrong turn: "
            f"tool error={wrong_result.is_error} { _text(wrong_result) }"
        )

        # r4
        price_result = await buyer.call_tool(
            "propose",
            {"nid": own["negotiation_id"], "price": 91},
        )
        lines.append(
            "(4) price outside buyer limit: "
            f"tool error={price_result.is_error} { _text(price_result) }"
        )
        if not all((foreign_result.is_error, wrong_result.is_error, price_result.is_error)):
            raise RuntimeError("a boundary call was accepted\n" + "\n".join(lines))

        recovery = await buyer.call_tool(
            "propose",
            {"nid": own["negotiation_id"], "price": 90},
        )
        lines.append(
            "(5) same buyer after refusal: "
            f"tool error={recovery.is_error} { _text(recovery) }"
        )
        if recovery.is_error:
            raise RuntimeError("valid proposal after refusal was rejected\n" + "\n".join(lines))

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"), end="")


def _text(result) -> str:
    chunks = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return " ".join(chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("auth_checks_probe.txt"))
    args = parser.parse_args()
    base = args.base.rstrip("/")
    games = prepare_state(base)
    asyncio.run(probe(base + "/mcp", games, args.out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()