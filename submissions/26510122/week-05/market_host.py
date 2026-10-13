#!/usr/bin/env python3
"""MCP host that asks a stateless Codex model to choose each market move."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


MOVE_TOOLS = {"propose", "accept_proposal", "reject_proposal", "refuse"}


def result_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


def role_prompt(role: str, scenario: dict) -> str:
    if role == "buyer":
        private = (
            f"Your private budget is {scenario['budget']}. Never propose or accept "
            "a price above this budget."
        )
    else:
        private = (
            f"Your private reserve is {scenario['reserve']}. Never propose or accept "
            "a price below this reserve."
        )
    return (
        f"You are the {role} negotiating for {scenario['item']}. {private} "
        "Choose exactly one available MCP move. A refusal from the server means the "
        "move did not happen, so choose a valid replacement. Return only the requested "
        "JSON object."
    )


class CodexActionModel:
    def __init__(self, model: str = "gpt-5.6-luna") -> None:
        self.model = model
        self.temperature = "not-settable"
        self.calls = 0

    def choose(
        self,
        system: str,
        state: dict,
        tools: list[dict],
        previous_error: str | None,
    ) -> dict:
        self.calls += 1
        names = [tool["name"] for tool in tools]
        schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": names},
                "arguments": {
                    "type": "object",
                    "properties": {
                        "negotiation_id": {"type": "string"},
                        "price": {"type": ["integer", "null"]},
                    },
                    "required": ["negotiation_id", "price"],
                    "additionalProperties": False,
                },
            },
            "required": ["tool", "arguments"],
            "additionalProperties": False,
        }
        user = json.dumps(
            {
                "negotiation": state,
                "available_tools": tools,
                "previous_server_error": previous_error,
                "instruction": (
                    "Choose one move now. Include negotiation_id in arguments. Use an integer "
                    "price for propose and null for every other move."
                ),
            },
            ensure_ascii=False,
        )
        with tempfile.TemporaryDirectory(prefix="week05-codex-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "output.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--strict-config", "--skip-git-repo-check", "--sandbox", "read-only",
                "--cd", "/tmp", "--model", self.model,
                "--config", f"developer_instructions={json.dumps(system)}",
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path), user,
            ]
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip()
                raise RuntimeError(detail[-1000:] if detail else "codex exec failed")
            return json.loads(output_path.read_text(encoding="utf-8"))


async def take_turn(
    server_url: str,
    token: str,
    negotiation_id: str,
    role: str,
    scenario: dict,
    model: CodexActionModel,
    emit: Callable[..., None],
    max_attempts: int = 3,
) -> dict:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    transport = streamable_http_client(f"{server_url}/mcp", http_client=http)
    async with http, Client(transport) as client:
        listed = await client.list_tools()
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in listed.tools
            if tool.name in MOVE_TOOLS
        ]
        state_result = await client.call_tool(
            "get_negotiation", {"negotiation_id": negotiation_id}
        )
        state_raw = result_text(state_result)
        emit("tool_result", role=role, tool="get_negotiation", result=state_raw, error=state_result.is_error)
        if state_result.is_error:
            raise RuntimeError(state_raw)
        state = json.loads(state_raw)
        previous_error = None
        for attempt in range(1, max_attempts + 1):
            action = model.choose(role_prompt(role, scenario), state, tools, previous_error)
            name = action["tool"]
            arguments = {
                key: value for key, value in action["arguments"].items() if value is not None
            }
            emit("tool_call", role=role, attempt=attempt, tool=name, arguments=arguments)
            result = await client.call_tool(name, arguments)
            text = result_text(result)
            emit("tool_result", role=role, attempt=attempt, tool=name, result=text, error=result.is_error)
            if not result.is_error:
                return json.loads(text)
            previous_error = text
        raise RuntimeError(f"{role} exhausted {max_attempts} move attempts")
