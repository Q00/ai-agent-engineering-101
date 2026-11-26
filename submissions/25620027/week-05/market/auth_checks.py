"""Capture the four required authorization checks against a running server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent

from market.api_models import OpenNegotiation
from market.http_client import create_async_client
from market.models import Condition, FrozenModel, Scenario

if TYPE_CHECKING:
    from pathlib import Path

    from market.admin_client import AdminClient


class ToolProbe(FrozenModel):
    """One direct MCP call for an authorization assertion."""

    server_url: str
    token: str
    name: str
    arguments: dict[str, str | int]


async def _probe(request: ToolProbe) -> str:
    headers = {"Authorization": f"Bearer {request.token}"}
    async with create_async_client(headers=headers, long_read=True) as http_client:
        transport = streamable_http_client(request.server_url, http_client=http_client)
        async with Client(transport) as market:
            result = await market.call_tool(request.name, request.arguments)
    text = " ".join(item.text for item in result.content if isinstance(item, TextContent))
    return f"is_error={int(result.is_error)} detail={text}"


async def capture_auth_checks(
    server_base: str,
    admin: AdminClient,
    output: Path,
) -> None:
    """Write no-token, wrong-handle, turn, and limit evidence as four lines."""
    async with create_async_client(base_url=server_base) as client:
        response = await client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
                "Content-Type": "application/json",
            },
            content=(
                '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":'
                '{"io.modelcontextprotocol/clientInfo":{"name":"auth-probe","version":"1.0"},'
                '"io.modelcontextprotocol/clientCapabilities":{}}}}'
            ),
        )
    challenge = response.headers.get("www-authenticate", "missing")
    scenario = Scenario(id="auth", item="auth check item", reserve=120, budget=150)
    first = await admin.open(OpenNegotiation(scenario=scenario, condition=Condition.SERVER_INJECT))
    second = await admin.open(OpenNegotiation(scenario=scenario, condition=Condition.SERVER_INJECT))
    wrong = await _probe(
        ToolProbe(
            server_url=f"{server_base}/mcp",
            token=first.buyer_token,
            name="get_negotiation",
            arguments={"negotiation_id": str(second.negotiation_id)},
        )
    )
    turn = await _probe(
        ToolProbe(
            server_url=f"{server_base}/mcp",
            token=first.seller_token,
            name="propose",
            arguments={"negotiation_id": str(first.negotiation_id), "price": 130},
        )
    )
    limit = await _probe(
        ToolProbe(
            server_url=f"{server_base}/mcp",
            token=first.buyer_token,
            name="propose",
            arguments={"negotiation_id": str(first.negotiation_id), "price": 180},
        )
    )
    lines = (
        f"no token: HTTP {response.status_code}; WWW-Authenticate: {challenge}",
        f"party token on another negotiation: {wrong}",
        f"move out of turn: {turn}",
        f"server condition outside token limit: {limit}",
    )
    await anyio.Path(output).write_text("\n".join(lines) + "\n", encoding="utf-8")
