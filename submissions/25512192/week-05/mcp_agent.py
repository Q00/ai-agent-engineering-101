"""Week 05 LAB: the week-01 loop as an MCP host.

Tools are no longer defined here. The host asks the server (tools/list) and
runs them through the server (tools/call).
Requires: pip install "mcp>=2" openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  optional; https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; defaults to gpt-4o-mini
  MCP_SERVER       optional; empty = start tools_server.py over stdio,
                   http://127.0.0.1:8000/mcp = connect over Streamable HTTP
"""
import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters
from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp949

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable, args=[os.path.join(HERE, "tools_server.py")], cwd=HERE)

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


def to_openai(tool):                        # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}}


async def run(goal: str, max_steps: int = 8):
    llm = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    async with Client(SERVER) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]   # tools/list
        print(f"  [host] {len(tools)} tools: {[t['function']['name'] for t in tools]}")

        for step in range(max_steps):   # <- this loop is what makes it an agent
            resp = llm.chat.completions.create(
                model=MODEL, tools=tools, messages=messages)
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:               # final answer -> stop
                return msg.content or ""

            for call in msg.tool_calls:          # execute tool calls -> observe
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)      # tools/call
                out = "\n".join(c.text for c in result.content if c.type == "text")
                print(f"  [tool] {call.function.name}({args}) -> {out}")
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": out})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
