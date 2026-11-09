"""MCP client and one-turn host for the Week 5 market."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.request import Request, urlopen


MOVE_NAMES = {
    "propose",
    "accept_proposal",
    "reject_proposal",
    "refuse",
}


class MarketClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.request_id = 0

    def list_tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list", {})["tools"]

    def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params,
        }).encode()
        request = Request(
            f"{self.base_url}/mcp",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": method,
                "Mcp-Name": "week05-market",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
        if "error" in payload:
            raise RuntimeError(payload["error"]["message"])
        return payload["result"]


def result_text(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    if not content:
        return "tool call failed without a message"
    return str(content[0].get("text", ""))


def run_host_turn(
    client: MarketClient,
    negotiation_id: str,
    chooser: Callable[[dict[str, Any], str | None], dict[str, Any]],
    log: Callable[[dict[str, Any]], None],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Read once, then retry refused moves without yielding the party's turn."""
    get_arguments = {"negotiation_id": negotiation_id}
    log({
        "event": "tool_call",
        "tool": "get_negotiation",
        "arguments": get_arguments,
    })
    read_result = client.call_tool("get_negotiation", get_arguments)
    log({
        "event": "tool_result",
        "tool": "get_negotiation",
        "result": read_result,
    })
    tool_calls = 1
    if read_result.get("isError"):
        return {
            "moved": False,
            "tool_calls": tool_calls,
            "refused_then_valid": 0,
            "refused_in_turn": 1,
        }
    state = read_result["structuredContent"]
    previous_error = None
    refusals = 0
    for _ in range(max_attempts):
        action = chooser(state, previous_error)
        name = action.get("tool")
        if name not in MOVE_NAMES:
            previous_error = f"host rejected unsupported move: {name}"
            refusals += 1
            continue
        arguments = dict(action.get("arguments") or {})
        arguments.pop("role", None)
        arguments["negotiation_id"] = negotiation_id
        log({
            "event": "tool_call",
            "tool": name,
            "arguments": arguments,
        })
        move_result = client.call_tool(name, arguments)
        tool_calls += 1
        log({
            "event": "tool_result",
            "tool": name,
            "result": move_result,
        })
        if not move_result.get("isError"):
            return {
                "moved": True,
                "tool_calls": tool_calls,
                "refused_then_valid": int(refusals > 0),
                "refused_in_turn": refusals,
            }
        refusals += 1
        previous_error = result_text(move_result)
    return {
        "moved": False,
        "tool_calls": tool_calls,
        "refused_then_valid": 0,
        "refused_in_turn": refusals,
    }
