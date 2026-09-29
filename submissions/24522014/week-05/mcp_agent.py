"""Week 05 LAB: run the week-01 agent loop with tools from an MCP server.

By default the server is launched as a stdio child process. Set MCP_SERVER to
an HTTP URL to connect to an already-running server instead.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from openai import OpenAI


MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable,
    args=[str(Path(__file__).with_name("tools_server.py"))],
)


def to_openai(tool):
    """Translate a discovered MCP tool into an OpenAI function tool."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


async def run(goal: str, max_steps: int = 8) -> str:
    async with Client(SERVER) as mcp:
        tools = [to_openai(tool) for tool in (await mcp.list_tools()).tools]
        print(f"[host] {len(tools)} tools: {[tool['function']['name'] for tool in tools]}")

        client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
        messages = [{"role": "user", "content": goal}]

        for _ in range(max_steps):
            response = client.chat.completions.create(
                model=MODEL, tools=tools, messages=messages
            )
            message = response.choices[0].message
            messages.append(message)

            if not message.tool_calls:
                return message.content or ""

            for call in message.tool_calls:
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)
                output = "\n".join(
                    part.text for part in result.content if part.type == "text"
                )
                if result.is_error:
                    output = f"tool error: {output}"
                print(f"  [tool] {call.function.name}({args}) -> {output[:200]}")
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": output
                })

        return "stopped: max steps exceeded"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(asyncio.run(run(goal)))
