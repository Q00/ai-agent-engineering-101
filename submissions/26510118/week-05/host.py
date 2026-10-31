"""Week 05 -- the lab's MCP host, with a party token on the HTTP client.

The loop is the week-01 loop the lab turned into an MCP host. Two things are
new, and both are one line each:

    the header   create_mcp_http_client(headers={"Authorization": "Bearer ..."})
                 Nothing else identifies the caller. The model never sees the
                 token, and no tool takes a role argument.
    the stop     a turn ends the moment a move tool succeeds, so the host does
                 not spend a model call on a closing sentence nobody reads.

One call to `take_turn` is one turn: the agent reads the negotiation and makes
one move. A refused move does not end the turn -- the tool error goes back into
the conversation and the model may try again. That is deliberate: how often a
refusal is followed by a valid move in the same turn is one of the numbers the
report has to give, so the host has to give the model the chance.

Model settings come from the environment, the same way week 04 took them:

    OPENAI_API_KEY         required
    OPENAI_BASE_URL        optional; set it for OpenRouter and friends
    AGENT_MODEL            optional; default below
    AGENT_TEMPERATURE      optional; default below
"""
import json
import os
import time

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from openai import OpenAI

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")

# The four tools that use up a move. get_negotiation is free.
MOVES = ("propose", "accept_proposal", "reject_proposal", "refuse")

# Reasoning models on OpenRouter put their thinking into the message text unless
# it is switched off. api.openai.com rejects the unknown field with a 400, so it
# is sent only when a custom base URL says we are not talking to OpenAI.
EXTRA_BODY = ({"reasoning": {"enabled": False}}
              if os.environ.get("OPENAI_BASE_URL") else None)

RETRY_WAITS = (2, 5, 12, 30, 60)

TURN_PROMPT = ("It is your turn in negotiation {nid}. Call get_negotiation to "
               "see where things stand, then make exactly one move.")

_openai = None


def settings_line() -> str:
    """First line of every log, so a run reproduces from the log alone."""
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return (f"host=host.py model={MODEL} temperature={TEMPERATURE} "
            f"base_url={base} mcp_url={MCP_URL}")


def _client() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI()
    return _openai


def _transient(e: Exception) -> bool:
    """429 and friends are worth waiting out. A 400 or a 401 is not."""
    code = getattr(e, "status_code", None)
    if code is None:
        code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (408, 409, 429, 500, 502, 503, 504):
        return True
    return type(e).__name__ in (
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "OverloadedError", "ServiceUnavailableError")


def _complete(messages, tools, stats, log):
    """One model call, with a growing wait on the transient failures."""
    for wait in (*RETRY_WAITS, None):
        try:
            kwargs = {"extra_body": EXTRA_BODY} if EXTRA_BODY else {}
            resp = _client().chat.completions.create(
                model=MODEL, temperature=TEMPERATURE, tools=tools,
                messages=messages, **kwargs)
            if not getattr(resp, "choices", None):
                raise RuntimeError("model returned no choices")
            stats["model_calls"] += 1
            return resp.choices[0].message
        except Exception as e:                           # noqa: BLE001
            if wait is None or not _transient(e):
                raise
            stats["retries"] += 1
            log(f"      [retry] {type(e).__name__}: waiting {wait}s")
            time.sleep(wait)


def to_openai(tool):
    """One MCP tool -> one OpenAI function schema. The whole adapter."""
    return {"type": "function",
            "function": {"name": tool.name,
                         "description": tool.description,
                         "parameters": tool.input_schema}}


async def take_turn(token: str, system: str, nid: str, role: str,
                    log, max_steps: int = 6) -> dict:
    """One turn for one party. Returns what happened, for the runner's row."""
    stats = {"model_calls": 0, "tool_calls": 0, "retries": 0,
             "refused": 0, "act": None, "price": None, "recovered": 0}

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": TURN_PROMPT.format(nid=nid)}]

    async with create_mcp_http_client(
            headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(MCP_URL, http_client=http)) as mcp:
            tools = [to_openai(t) for t in (await mcp.list_tools()).tools]

            for _ in range(max_steps):
                msg = _complete(messages, tools, stats, log)
                messages.append(msg)

                if not msg.tool_calls:
                    log(f"      [{role}] said instead of moving: "
                        f"{(msg.content or '').strip()[:200]}")
                    messages.append({
                        "role": "user",
                        "content": "You did not make a move. Call exactly one "
                                   "of propose, accept_proposal, "
                                   "reject_proposal or refuse now."})
                    continue

                for call in msg.tool_calls:
                    name = call.function.name
                    args = json.loads(call.function.arguments or "{}")
                    result = await mcp.call_tool(name, args)
                    out = "\n".join(c.text for c in result.content
                                    if c.type == "text")
                    stats["tool_calls"] += 1
                    flag = "REFUSED" if result.is_error else "ok"
                    log(f"      [{role}] {name}({args}) -> {flag}")
                    for line in out.splitlines():
                        log(f"          {line}")
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": out})

                    if name in MOVES:
                        if result.is_error:
                            stats["refused"] += 1
                        else:
                            # A move went through: the turn is over. If this
                            # party had already been refused in this same turn,
                            # that is a recovery.
                            stats["act"] = name
                            stats["price"] = args.get("price")
                            stats["recovered"] = 1 if stats["refused"] else 0
                            return stats

    log(f"      [{role}] made no move in {max_steps} steps")
    return stats
