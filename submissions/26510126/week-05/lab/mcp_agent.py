"""Week 05 lab — the week-01 loop as an MCP host.

What changed from week-01 first_agent.py: TOOLS and TOOLS_IMPL are gone.
The tool list comes from the server (tools/list) and every call goes to the
server (tools/call). The loop itself is unchanged.

Requires, in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; overrides the default below
  MCP_SERVER       optional; empty = spawn tools_server.py over stdio,
                   http://127.0.0.1:8000/mcp = connect over Streamable HTTP

Run:
  python mcp_agent.py "Read notes.txt and sum the numbers in it."
"""
import os
import sys
import json
import time
import asyncio

import openai
from openai import OpenAI
from mcp import Client, StdioServerParameters

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable, args=[os.path.join(HERE, "tools_server.py")], cwd=HERE)

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")


def chat(client, **kw):
    """One model call. OpenRouter free models sometimes answer 200 with no
    choices, or 429/5xx; retry those with a growing wait."""
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(
                model=MODEL, extra_body={"reasoning": {"enabled": False}}, **kw)
            if resp.choices:
                return resp
            why = f"no choices: {resp.model_extra}"
        except (openai.RateLimitError, openai.InternalServerError) as e:
            why = f"{type(e).__name__}: {e}"
        wait = 5 * 2 ** attempt
        print(f"  [retry] {why} -> waiting {wait}s")
        time.sleep(wait)
    raise RuntimeError("model call failed 5 times")


def to_openai(tool):                        # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}}


async def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    async with Client(SERVER) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]   # tools/list
        transport = "http" if isinstance(SERVER, str) else "stdio"
        print(f"  [host] {transport}, {len(tools)} tools: "
              f"{[t['function']['name'] for t in tools]}")

        for step in range(max_steps):   # <- this loop is what makes it an agent
            resp = chat(client, tools=tools, messages=messages)
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:               # final answer -> stop
                return msg.content or ""

            for call in msg.tool_calls:          # execute tool calls -> observe
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)  # tools/call
                out = "\n".join(c.text for c in result.content if c.type == "text")
                print(f"  [tool] {call.function.name}({args}) -> {out}")
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": out})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
