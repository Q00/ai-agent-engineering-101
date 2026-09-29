"""Week 05 lab: use the Week 01 agent loop as an MCP host."""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import Client, StdioServerParameters
from openai import OpenAI, RateLimitError


SCRIPT_DIR = Path(__file__).resolve().parent
MCP_SERVER = os.environ.get("MCP_SERVER")
SERVER = MCP_SERVER or StdioServerParameters(
    command=sys.executable,
    args=[str(SCRIPT_DIR / "tools_server.py")],
    cwd=SCRIPT_DIR,
)

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
API_KEY = OPENROUTER_KEY or os.environ.get("OPENAI_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL")
if OPENROUTER_KEY and not BASE_URL:
    BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_MODEL = (
    "nvidia/nemotron-3-super-120b-a12b:free"
    if BASE_URL and "openrouter.ai" in BASE_URL
    else "gpt-5.6-luna"
)
MODEL = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
DEFAULT_GOAL = "Read notes.txt and sum the numbers in it."
RETRY_WAITS = (5, 15, 45)


def to_openai(tool) -> dict:
    """Translate one discovered MCP tool into Chat Completions format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def result_text(result) -> str:
    """Flatten textual MCP content into the observation sent to the model."""
    chunks = [item.text for item in result.content if item.type == "text"]
    text = "\n".join(chunks) if chunks else str(result.content)
    return f"error: {text}" if result.is_error else text


def chat(client: OpenAI, messages: list, tools: list[dict]):
    """Run one visible, bounded-retry model turn."""
    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            return client.chat.completions.create(
                model=MODEL,
                tools=tools,
                messages=messages,
            )
        except RateLimitError:
            if wait is None:
                raise
            print(
                f"  [429] waiting {wait}s "
                f"(retry {attempt}/{len(RETRY_WAITS)})",
                flush=True,
            )
            time.sleep(wait)


async def run(goal: str, max_steps: int = 8, list_only: bool = False) -> str:
    """Connect to MCP, discover tools, and run the unchanged Week 01 loop."""
    async with Client(SERVER) as mcp:
        discovered = (await mcp.list_tools()).tools
        tools = [to_openai(tool) for tool in discovered]
        print(f"[host] {len(tools)} tools: {[tool.name for tool in discovered]}")

        if list_only:
            for tool in discovered:
                print(f"[schema] {tool.name}: {json.dumps(tool.input_schema)}")
            return "listed tools only"

        if not API_KEY:
            raise RuntimeError(
                "set OPENAI_API_KEY or OPENROUTER_API_KEY before an agent run"
            )

        client_args = {"api_key": API_KEY, "max_retries": 0}
        if BASE_URL:
            client_args["base_url"] = BASE_URL
        client = OpenAI(**client_args)
        messages = [{"role": "user", "content": goal}]

        for step in range(max_steps):
            response = chat(client, messages, tools)
            message = response.choices[0].message
            messages.append(message)

            if not message.tool_calls:
                print(f"[step {step}] final answer")
                return message.content or ""

            for call in message.tool_calls:
                name = call.function.name
                raw = call.function.arguments or "{}"
                try:
                    arguments = json.loads(raw)
                    result = await mcp.call_tool(name, arguments)
                    output = result_text(result)
                except Exception as exc:
                    output = f"error: {type(exc).__name__}: {exc}"
                print(f"[step {step}] [tool] {name}({raw}) -> {output}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": output,
                    }
                )

        return "stopped: max steps exceeded"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--list-tools", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    transport = MCP_SERVER or "stdio child process"
    print(f"model:     {MODEL}")
    print(f"transport: {transport}")
    print(f"goal:      {args.goal}\n")
    print(asyncio.run(run(args.goal, args.max_steps, args.list_tools)))
