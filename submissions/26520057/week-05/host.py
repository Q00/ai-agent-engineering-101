"""Week 05 host: the week-01 loop as an MCP host with a bearer token.

One call of run_turn = one turn = one host run. The host gets its tool list from the
market server (tools/list), not from code: no tool name is hardcoded here except the
set of moves that end a turn.
Env: OPENAI_API_KEY, optional OPENAI_BASE_URL, AGENT_MODEL (default gpt-4o-mini).
"""
import json
import os
import time

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from openai import OpenAI

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.0
MAX_STEPS = 6                 # model calls per turn
MOVES = {"propose", "accept_proposal", "reject_proposal", "refuse"}   # a successful one ends the turn
_llm = OpenAI()


def to_openai(tool):          # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}}


def _complete(**kw):
    """Chat completion with retry on rate limits, 5xx, and responses without choices."""
    for attempt in range(6):
        try:
            resp = _llm.chat.completions.create(**kw)
            if resp.choices:
                return resp
            err = "response without choices"
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", None)
            if status is not None and status != 429 and status < 500:
                raise
            err = repr(e)
        wait = 2 ** attempt
        print(f"    [retry] {err[:120]} -> waiting {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"model call failed after retries: {err}")


async def run_turn(url, token, role, system, user, log=print):
    """Run one turn. Returns counts for the runner."""
    stats = {"tool_calls": 0, "model_calls": 0, "moved": False, "refused": 0,
             "refused_then_valid": 0}
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(url, http_client=http)) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]      # tools/list
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for _ in range(MAX_STEPS):
            resp = _complete(model=MODEL, temperature=TEMPERATURE, tools=tools, messages=messages)
            stats["model_calls"] += 1
            msg = resp.choices[0].message
            messages.append(msg)
            if msg.content:
                log(f"  [{role} text] {msg.content.strip()}")
            if not msg.tool_calls:
                break
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                stats["tool_calls"] += 1
                result = await mcp.call_tool(call.function.name, args)       # tools/call
                out = "\n".join(c.text for c in result.content if c.type == "text")
                log(f"  [{role} call] {call.function.name}({args})")
                log(f"    [{'error' if result.is_error else 'result'}] {out}")
                if call.function.name in MOVES:
                    if result.is_error:
                        stats["refused"] += 1
                    elif not stats["moved"]:
                        stats["moved"] = True
                        stats["refused_then_valid"] = stats["refused"]   # refusals this turn that a valid move followed
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
            if stats["moved"]:
                break
    return stats
