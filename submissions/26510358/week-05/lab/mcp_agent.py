"""Week 01's model/tool/observation loop with an MCP client as its tool layer."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from openai import AsyncOpenAI

SERVER = Path(__file__).with_name("tools_server.py")
DEFAULT_GOAL = "Read notes.txt and sum the numbers in it."


def to_openai(tool):
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description or "",
        "parameters": tool.input_schema}}


def result_text(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


async def run(goal: str, max_steps: int = 8):
    server = os.getenv("MCP_SERVER") or StdioServerParameters(
        command=sys.executable, args=[str(SERVER)])
    model = os.getenv("AGENT_MODEL", "gpt-5.6-luna")
    ai = AsyncOpenAI(timeout=60, max_retries=2)
    async with Client(server) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]
        print(f"[host] transport={'http' if isinstance(server, str) else 'stdio'} "
              f"tools={[t['function']['name'] for t in tools]}", flush=True)
        messages = [{"role": "user", "content": goal}]
        for step in range(1, max_steps + 1):
            response = await ai.chat.completions.create(
                model=model, messages=messages, tools=tools,
                temperature=0, reasoning_effort="none", max_completion_tokens=256)
            message = response.choices[0].message
            print(f"[model] step={step} text={message.content!r} "
                  f"calls={len(message.tool_calls or [])}", flush=True)
            messages.append(message)
            if not message.tool_calls:
                return message.content or ""
            for call in message.tool_calls:
                args = json.loads(call.function.arguments)
                result = await mcp.call_tool(call.function.name, args)
                output = result_text(result)
                print(f"[tool] {call.function.name}({args}) "
                      f"error={bool(result.is_error)} -> {output}", flush=True)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": output})
    return "stopped: max steps exceeded"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required")
    print("[final]", asyncio.run(run(args.goal)), flush=True)


if __name__ == "__main__":
    main()
