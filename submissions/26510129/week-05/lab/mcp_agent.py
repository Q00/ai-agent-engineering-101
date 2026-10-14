"""Week 05 lab: the week-01 loop as an MCP host.

Changed from first_agent_openai.py: TOOLS / TOOLS_IMPL are gone. The tool list comes from
tools/list and every call goes through tools/call. Set MCP_SERVER=http://127.0.0.1:8000/mcp
for HTTP; leave it empty to spawn tools_server.py as a stdio child process.
"""
import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters
from openai import OpenAI

SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable, args=["tools_server.py"])
MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
# the reasoning switch exists only on OpenRouter; api.openai.com would reject the field
EXTRA = ({"extra_body": {"reasoning": {"enabled": False}}}
         if "openrouter" in os.environ.get("OPENAI_BASE_URL", "") else {})


def to_openai(tool):                        # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}}


async def run(goal: str, max_steps: int = 8):
    client = OpenAI()
    messages = [{"role": "user", "content": goal}]
    async with Client(SERVER) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]   # tools/list
        print(f"[host] {len(tools)} tools: {[t['function']['name'] for t in tools]}")

        for step in range(max_steps):
            resp = client.chat.completions.create(
                model=MODEL, tools=tools, messages=messages, **EXTRA)
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content or ""

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)      # tools/call
                out = "\n".join(c.text for c in result.content if c.type == "text")
                print(f"  [tool] {call.function.name}({args}) -> {out}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})

    return "stopped: max steps exceeded"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
