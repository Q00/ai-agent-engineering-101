"""One turn of one party: the lab's week-01 loop as an MCP host, plus an
Authorization header. The agent reads the negotiation with get_negotiation and
makes one move. The host stops as soon as a move went through (the server
flipped the turn), or after MAX_STEPS model calls.

Model: Anthropic SDK, AGENT_MODEL from .env (no temperature argument, as in
week 04). Retries 429 and 5xx with a growing wait.
"""
import os
import time

import anthropic
import httpx2
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

load_dotenv()

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1024
MAX_STEPS = 6           # model calls per turn, a safety net
RETRIES, BACKOFF = 5, 5
MOVES = {"propose", "accept_proposal", "reject_proposal", "refuse"}


def to_anthropic(tool):
    return {"name": tool.name, "description": tool.description,
            "input_schema": tool.input_schema}


def _retryable(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    return status == 429 or (status is not None and status >= 500)


def call_model(client, system, tools, messages, log):
    for attempt in range(RETRIES):
        try:
            return client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system,
                                          tools=tools, messages=messages)
        except Exception as e:
            if not _retryable(e) or attempt == RETRIES - 1:
                raise
            wait = BACKOFF * 2 ** attempt
            log(f"    [{getattr(e, 'status_code', '?')}] waiting {wait}s, retry {attempt + 1}/{RETRIES - 1}")
            time.sleep(wait)


async def take_turn(mcp_url: str, token: str, system: str, role: str, log=print) -> dict:
    """Returns {'tool_calls': n, 'moved': bool, 'model_calls': n}."""
    client = anthropic.Anthropic()
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    stats = {"tool_calls": 0, "moved": False, "model_calls": 0}
    messages = [{"role": "user", "content": "It is your turn. Read the negotiation and make your move."}]

    async with Client(streamable_http_client(mcp_url, http_client=http)) as mcp:
        tools = [to_anthropic(t) for t in (await mcp.list_tools()).tools]

        for step in range(MAX_STEPS):
            resp = call_model(client, system, tools, messages, log)
            stats["model_calls"] += 1
            messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    log(f"    [{role} says] {b.text.strip()}")

            if resp.stop_reason != "tool_use":
                return stats

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                result = await mcp.call_tool(block.name, block.input)
                stats["tool_calls"] += 1
                out = "\n".join(c.text for c in result.content if c.type == "text")
                tag = "REFUSED" if result.is_error else "ok"
                log(f"    [{role} tool] {block.name}({block.input}) -> {tag}: {out}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": out, "is_error": bool(result.is_error)})
                if block.name in MOVES and not result.is_error:
                    stats["moved"] = True
            messages.append({"role": "user", "content": results})
            if stats["moved"]:
                return stats            # the move ended the turn; no need for a closing reply

    return stats
