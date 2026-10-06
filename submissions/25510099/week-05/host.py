"""One turn of one party: the week-01 loop turned into an MCP host.

What changed from week 01: the tool list comes from the server (tools/list)
instead of a hardcoded TOOLS table, each call goes to the server (tools/call)
instead of TOOLS_IMPL, and the HTTP client carries this party's bearer token
in an Authorization header. The loop itself is the week-01 loop.

A host run is one turn. The model gets the role's system prompt and one user
message naming the negotiation, reads the state with get_negotiation and
makes one move. The run stops as soon as the market accepts a move (propose,
accept_proposal, reject_proposal or refuse). A refused move comes back as a
tool error the model can read, so it can try again inside the same run; a
run that ends without an accepted move returns moved=False and the runner
passes the turn. Every tool call the host makes is counted, errors included.

Model: OpenAI-compatible chat completions with tool calling. OPENAI_API_KEY,
optional OPENAI_BASE_URL (OpenRouter), AGENT_MODEL. Retries carried over
from week 04: an empty-choices response is retried, a 429 is retried with a
growing wait. temperature 0.
"""
import json
import os
import time
from dataclasses import dataclass, field

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

MODEL = os.environ.get("AGENT_MODEL", "gpt-4.1-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "300"))
TIMEOUT_S = float(os.environ.get("AGENT_TIMEOUT", "60"))
REASONING_OFF = os.environ.get("AGENT_REASONING_OFF", "") not in ("", "0", "false")
TRANSPORT_RETRIES = int(os.environ.get("AGENT_TRANSPORT_RETRIES", "3"))
RATE_LIMIT_RETRIES = int(os.environ.get("AGENT_RATE_LIMIT_RETRIES", "4"))

MOVE_TOOLS = ("propose", "accept_proposal", "reject_proposal", "refuse")
REFUSED = "refused by the market"


class Meter:
    def __init__(self):
        self.calls = 0
        self.tokens = 0
        self.model_reported = None

    def add(self, resp):
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u:
            self.tokens += int(getattr(u, "prompt_tokens", 0) or 0) + int(getattr(u, "completion_tokens", 0) or 0)
        if not self.model_reported:
            self.model_reported = getattr(resp, "model", None)


@dataclass
class TurnResult:
    moved: bool = False            # the market accepted one move in this run
    move: tuple | None = None      # (act, price or None)
    tool_calls: int = 0            # calls this host sent to the server, errors included
    refused: int = 0               # tool errors starting with "refused by the market"
    refused_then_valid: bool = False   # a refusal and then an accepted move, same run
    steps: int = 0                 # model calls
    texts: list = field(default_factory=list)
    error: str | None = None       # a crash of the run (not a tool error)


# ---------------------------------------------------------------- model

_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(timeout=TIMEOUT_S)
    return _client


def _chat(messages, tools, meter: Meter):
    from openai import RateLimitError
    kwargs = dict(model=MODEL, messages=messages, tools=tools, tool_choice="auto",
                  temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
    if REASONING_OFF:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    last, rate_hits, attempt = "empty choices", 0, 0
    while attempt < TRANSPORT_RETRIES:
        try:
            resp = _openai().chat.completions.create(**kwargs)
        except RateLimitError:
            rate_hits += 1
            if rate_hits > RATE_LIMIT_RETRIES:
                raise
            time.sleep(10 * 2 ** (rate_hits - 1))
            continue
        if getattr(resp, "choices", None):
            meter.add(resp)
            return resp.choices[0].message
        last = getattr(resp, "error", None) or last
        time.sleep(2 ** attempt)
        attempt += 1
    raise RuntimeError(f"provider returned no choices after {TRANSPORT_RETRIES} attempts: {last}")


def to_openai(tool) -> dict:
    """MCP tool -> OpenAI function schema. The description is the server's docstring."""
    return {"type": "function", "function": {"name": tool.name, "description": tool.description,
                                             "parameters": tool.input_schema}}


# ----------------------------------------------------------------- turn

def _text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


async def run_turn(server_url: str, token: str, role: str, system: str, user: str,
                   meter: Meter, log=print, max_steps: int = 6) -> TurnResult:
    res = TurnResult()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
            async with Client(streamable_http_client(server_url, http_client=http)) as mcp:
                tools = [to_openai(t) for t in (await mcp.list_tools()).tools]
                for _ in range(max_steps):
                    msg = _chat(messages, tools, meter)
                    res.steps += 1
                    messages.append(msg)
                    if msg.content:
                        res.texts.append(msg.content.strip())
                        log(f"  [{role} text] {msg.content.strip()}")
                    if not msg.tool_calls:
                        break                                    # final answer without a move
                    for call in msg.tool_calls:
                        name = call.function.name
                        try:
                            args = json.loads(call.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        if res.moved:                            # the turn is over; do not send more
                            out = "not executed: your turn ended with the previous move"
                            log(f"  [{role} call] {name}({args}) -> {out}")
                            messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
                            continue
                        result = await mcp.call_tool(name, args)
                        res.tool_calls += 1
                        out = _text(result)
                        shown = {k: v for k, v in args.items() if k != "negotiation_id"}
                        if result.is_error:
                            log(f"  [{role} call] {name}({shown})\n    [error] {out}")
                            if REFUSED in out:
                                res.refused += 1
                        else:
                            log(f"  [{role} call] {name}({shown})\n    [result] {out[:600]}")
                            if name in MOVE_TOOLS:
                                res.moved = True
                                res.move = (name, args.get("price"))
                                if res.refused:
                                    res.refused_then_valid = True
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
                    if res.moved:
                        break
    except Exception as e:                                       # a crash of the run, recorded by the runner
        res.error = f"{type(e).__name__}: {e}"
    return res
