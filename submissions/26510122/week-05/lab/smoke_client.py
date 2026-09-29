#!/usr/bin/env python3
"""Call the same MCP tools over stdio or Streamable HTTP without an LLM."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters


HERE = Path(__file__).resolve().parent


def connection(url: str | None):
    if url:
        return url
    return StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "tools_server.py")],
        cwd=HERE,
        env={**os.environ, "MCP_WORKSPACE": str(HERE)},
    )


def output(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


async def smoke(url: str | None) -> None:
    async with Client(connection(url)) as client:
        listed = await client.list_tools()
        print(f"[host] {len(listed.tools)} tools: {[tool.name for tool in listed.tools]}")
        read = await client.call_tool("read_file", {"path": "notes.txt"})
        print(f"[tool] read_file -> {output(read)}")
        total = await client.call_tool(
            "calculator", {"expression": "48000 + 9500"}
        )
        print(f"[tool] calculator(total expenses) -> {output(total)}")
        remaining = await client.call_tool(
            "calculator", {"expression": f"{output(total)} - 12000"}
        )
        print(f"[tool] calculator(remaining) -> {output(remaining)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server")
    args = parser.parse_args()
    asyncio.run(smoke(args.server))


if __name__ == "__main__":
    main()
