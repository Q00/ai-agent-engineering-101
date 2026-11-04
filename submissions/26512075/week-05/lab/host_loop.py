"""
week-1 agent loop turned into an MCP host


Tools comes from tools/list
Transport is chosen by the host

MCP_SERVER = http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import asyncio

import json
import csv
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit
import httpx

from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from openai import APIConnectionError, OpenAI
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DEFAULT_QUESTION = "Read notes.txt and sum the numbers in it."
MODEL = os.environ.get("DEFAULT_MODEL", "gpt-6-luna")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "8"))

def _openai() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ["API_KEY"],
        http_client=httpx.Client(timeout=httpx.Timeout(60.0)),
    )


def mcp_tool_to_openai(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {
        "type": "object",
        "properties": {},
    }
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def _text_from_call_result(result: Any) -> str:
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        prefix = "ERROR: "
    else:
        prefix = ""
    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    if not chunks and getattr(result, "structured_content", None):
        chunks.append(json.dumps(result.structured_content))
    return prefix + "\n".join(chunks)


async def _connect():
    """Return (client, closer). Compatible with mcp v2 Client and v1 ClientSession."""
    server = os.environ.get("MCP_SERVER", "").strip()
    headers = {}
    token = os.environ.get("MCP_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        from mcp import Client, StdioServerParameters

        if server:
            if headers:
                import httpx

                from mcp.client.streamable_http import streamable_http_client

                http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0, read=300.0))
                transport = streamable_http_client(server, http_client=http)
                client = Client(transport)
                await client.__aenter__()
                return client, client
            client = Client(server)
            await client.__aenter__()
            return client, client

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tools_server.py")])
        client = Client(params)
        await client.__aenter__()
        return client, client
    except ImportError:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        if server:
            streams = streamable_http_client(server, headers=headers or None)
            read, write, *_ = await streams.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            return session, streams
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "tools_server.py")])
        streams = stdio_client(params)
        read, write = await streams.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return session, streams


async def list_tools(client: Any) -> list[Any]:
    result = await client.list_tools()
    return list(result.tools)


async def call_tool(client: Any, name: str, arguments: dict[str, Any]) -> Any:
    return await client.call_tool(name, arguments)


async def run(question: str) -> str:
    client, closer = await _connect()
    try:
        tools = await list_tools(client)
        names = [t.name for t in tools]
        print(f"[host] {len(names)} tools: {names}")
        openai_tools = [mcp_tool_to_openai(t) for t in tools]

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a tool-using agent. Use tools when they help. "
                    "When you have the final answer, reply with only that answer."
                ),
            },
            {"role": "user", "content": question},
        ]
        llm = _openai()
        final = ""

        for _ in range(MAX_TURNS):
            
            try:
                resp = llm.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0,
                    tools=openai_tools,
                    reasoning_effort="none",
                )

            except APIConnectionError as error:
                print(type(error.__cause__).__name__, error.__cause__, file=sys.stderr)
                raise

            msg = resp.choices[0].message
            tool_calls = msg.tool_calls or []
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ]
                    if tool_calls
                    else None,
                }
            )
            if not tool_calls:
                final = (msg.content or "").strip()
                break
            for tc in tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = await call_tool(client, tc.function.name, args)
                text = _text_from_call_result(result)
                print(f"[tool] {tc.function.name}({args}) -> {text}")
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
        
        out = Path(__file__).with_name("answer.txt")
        out.write_text(final + "\n", encoding = "utf-8")
        print(final)
        print(f"wrote {out}")

        return final

    finally:
        aexit = getattr(closer, "__aexit__", None)
        if aexit:
            await aexit(None, None, None)


def main() -> None:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    asyncio.run(run(question))


if __name__ == "__main__":
    main()