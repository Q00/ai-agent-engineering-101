"""Week 05 lab — the week-01 loop turned into an MCP host (Anthropic API version).

Changed from week-01: the hardcoded TOOLS list comes from tools/list, and
TOOLS_IMPL[name](**args) became tools/call. The loop itself is unchanged.

  MCP_SERVER unset                     -> spawn tools_server.py as a stdio child
  MCP_SERVER=http://127.0.0.1:8000/mcp -> connect over Streamable HTTP
Requires ANTHROPIC_API_KEY (and optionally AGENT_MODEL) in .env or the environment.
"""
import os
import sys
import asyncio

import anthropic
from dotenv import load_dotenv
from mcp import Client, StdioServerParameters

load_dotenv()

SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable, args=["tools_server.py"])
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")


def to_anthropic(tool):                     # MCP tool -> Anthropic tool schema
    return {"name": tool.name, "description": tool.description,
            "input_schema": tool.input_schema}


async def run(goal: str, max_steps: int = 8):
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": goal}]

    async with Client(SERVER) as mcp:
        tools = [to_anthropic(t) for t in (await mcp.list_tools()).tools]   # tools/list
        print(f"[host] {len(tools)} tools: {[t['name'] for t in tools]}")

        for step in range(max_steps):   # <- this loop is what makes it an agent
            resp = client.messages.create(
                model=MODEL, max_tokens=1024,
                tools=tools, messages=messages)
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":   # final answer -> stop
                return "".join(b.text for b in resp.content if b.type == "text")

            results = []
            for block in resp.content:           # execute tool calls -> observe
                if block.type == "tool_use":
                    result = await mcp.call_tool(block.name, block.input)      # tools/call
                    out = "\n".join(c.text for c in result.content if c.type == "text")
                    print(f"  [tool] {block.name}({block.input}) -> {out}")
                    results.append({"type": "tool_result",
                                    "tool_use_id": block.id, "content": str(out)})
            messages.append({"role": "user", "content": results})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
