"""Record the four required checks against a running Week 05 server."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def open_negotiation(client: httpx2.AsyncClient, condition: str) -> dict:
    response = await client.post(
        "/admin/negotiations",
        json={
            "item": "auth-check item",
            "reserve": 40,
            "budget": 55,
            "condition": condition,
        },
    )
    response.raise_for_status()
    return response.json()


async def call_tool(base_url: str, token: str, name: str, arguments: dict):
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30.0) as client:
        async with streamable_http_client(
            f"{base_url}/mcp", http_client=client
        ) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)


def result_text(result) -> str:
    text = " ".join(
        part.text for part in result.content if getattr(part, "type", None) == "text"
    )
    return " ".join(text.split())


async def run(base_url: str, output: Path) -> None:
    async with httpx2.AsyncClient(base_url=base_url, timeout=30.0) as client:
        no_token = await client.post("/mcp", json={})
        first = await open_negotiation(client, "server_inject")
        second = await open_negotiation(client, "server_inject")

    wrong_id = await call_tool(
        base_url,
        first["buyer_token"],
        "get_negotiation",
        {"negotiation_id": second["negotiation_id"]},
    )
    out_of_turn = await call_tool(
        base_url,
        first["seller_token"],
        "propose",
        {"negotiation_id": first["negotiation_id"], "price": 45},
    )
    outside_limit = await call_tool(
        base_url,
        first["buyer_token"],
        "propose",
        {"negotiation_id": first["negotiation_id"], "price": 70},
    )

    lines = [
        "no_token: "
        f"status={no_token.status_code} "
        f"WWW-Authenticate={no_token.headers.get('WWW-Authenticate', '<missing>')}",
        f"wrong_negotiation: tool_error={int(wrong_id.is_error)} result={result_text(wrong_id)}",
        f"out_of_turn: tool_error={int(out_of_turn.is_error)} result={result_text(out_of_turn)}",
        f"outside_limit: tool_error={int(outside_limit.is_error)} result={result_text(outside_limit)}",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "auth_checks.txt",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(arguments.server_url.rstrip("/"), arguments.output))
