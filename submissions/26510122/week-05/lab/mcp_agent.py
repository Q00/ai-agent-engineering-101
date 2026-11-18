#!/usr/bin/env python3
"""Week 01 OpenAI-compatible loop rewritten as an MCP host."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values
from mcp import Client, StdioServerParameters
from openai import OpenAI


HERE = Path(__file__).resolve().parent
DEFAULT_GOAL = (
    "Read notes.txt, calculate total expenses and the remaining amount after "
    "reimbursement, then save a Korean summary."
)


def load_env() -> None:
    """Load named credentials from a local .env without printing them."""
    candidates = [HERE.parent / ".env", *HERE.parents]
    for candidate in candidates:
        env_file = candidate if candidate.name == ".env" else candidate / ".env"
        if not env_file.is_file():
            continue
        for name, value in dotenv_values(env_file).items():
            if value and name in {
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENROUTER_API_KEY",
                "AGENT_MODEL",
            }:
                os.environ.setdefault(name, value)
        break
    if not os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        os.environ.setdefault("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")


def server_connection(server_url: str | None):
    if server_url:
        return server_url
    return StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "tools_server.py")],
        cwd=HERE,
        env={**os.environ, "MCP_WORKSPACE": str(HERE)},
    )


def to_openai(tool) -> dict:
    """Translate a discovered MCP tool into OpenAI function-tool syntax."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def text_result(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


async def run(goal: str, server_url: str | None, max_steps: int = 8) -> str:
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY or OPENROUTER_API_KEY is required")
    model = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
    model_client = OpenAI()
    messages: list = [{"role": "user", "content": goal}]

    async with Client(server_connection(server_url)) as mcp_client:
        listed = await mcp_client.list_tools()
        tools = [to_openai(tool) for tool in listed.tools]
        print(f"[host] {len(tools)} tools: {[t['function']['name'] for t in tools]}")

        for _ in range(max_steps):
            request = {"model": model, "tools": tools, "messages": messages}
            if os.environ.get("OPENAI_BASE_URL", "").rstrip("/") == "https://openrouter.ai/api/v1":
                request["extra_body"] = {"reasoning": {"enabled": False}}
            response = model_client.chat.completions.create(**request)
            message = response.choices[0].message
            messages.append(message)
            if not message.tool_calls:
                return message.content or ""

            for call in message.tool_calls:
                arguments = json.loads(call.function.arguments)
                result = await mcp_client.call_tool(call.function.name, arguments)
                output = text_result(result)
                print(f"[tool] {call.function.name}({arguments}) -> {output}")
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )
    return "stopped: max steps exceeded"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    parser.add_argument("--server", default=os.environ.get("MCP_SERVER"))
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    print(asyncio.run(run(args.goal, args.server, args.max_steps)))


if __name__ == "__main__":
    main()
