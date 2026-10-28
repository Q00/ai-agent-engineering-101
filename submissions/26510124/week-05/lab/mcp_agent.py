"""Week 01 OpenAI loop; tool discovery and execution now go through MCP.

OPENAI_API_KEY is required. OPENAI_BASE_URL and AGENT_MODEL are optional.
Unset MCP_SERVER for stdio, or set it to http://127.0.0.1:8000/mcp.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters
from openai import AsyncOpenAI

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


def server_target():
    return os.environ.get("MCP_SERVER") or StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().with_name("tools_server.py"))],
    )


def to_openai(tool):
    return {"type": "function", "function": {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema,
    }}


async def run(goal: str, max_steps: int = 8):
    async with Client(server_target()) as mcp, AsyncOpenAI() as client:
        tools = [to_openai(tool) for tool in (await mcp.list_tools()).tools]
        print(f"  [host] {len(tools)} tools: {[t['function']['name'] for t in tools]}")
        print(f"  [host] model={MODEL}, protocol={mcp.protocol_version}")
        messages = [{"role": "user", "content": goal}]

        # Same model -> tool calls -> observations loop as the week-01 starter.
        for step in range(max_steps):
            resp = await client.chat.completions.create(
                model=MODEL, tools=tools, messages=messages)
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content or ""

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)
                out = "\n".join(c.text for c in result.content if c.type == "text")
                if result.is_error:
                    out = "tool error: " + out
                print(f"  [tool] {call.function.name}({args}) -> {out}")
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": str(out)})

        return "stopped: max steps exceeded"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
