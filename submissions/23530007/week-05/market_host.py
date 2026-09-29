"""Week 05 — one turn of one party, played by an MCP host.

A "turn" is one host execution: connect to the market with THIS party's token, let the model
read the negotiation and make one move. If the market refuses a move (tool error), the model
sees the reason and may try again inside the same turn; that is the "same-turn recovery" the
report counts. If the model stops without a valid move, the runner passes the turn.

The system prompt is the same in all four conditions, character for character except the
role, item and limit. The only thing that tells the agent which condition it is in is the
market's refusal message.

Settings (env; keys are never committed):
  AGENT_PROVIDER     "anthropic" (default) or "openai"
  ANTHROPIC_API_KEY / OPENAI_API_KEY   the key for that provider
  AGENT_MODEL        default claude-haiku-4-5-20251001 (anthropic) / gpt-4.1-mini (openai)
  AGENT_TEMPERATURE  default 0; "" to omit
"""
import os
import json
import time
from types import SimpleNamespace as NS

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

PROVIDER_KEY = os.environ.get("AGENT_PROVIDER", "anthropic")
PROVIDER = {"anthropic": "Anthropic Messages API", "openai": "OpenAI Chat Completions API"}[PROVIDER_KEY]
HOST_NAME = "custom loop (week-01 loop over MCP Streamable HTTP)"
MODEL = os.environ.get("AGENT_MODEL", {"anthropic": "claude-haiku-4-5-20251001",
                                       "openai": "gpt-4.1-mini"}[PROVIDER_KEY])
MAX_TOKENS = 512
MAX_STEPS = 6
MOVES = ("propose", "accept_proposal", "reject_proposal", "refuse")

_env_temp = os.environ.get("AGENT_TEMPERATURE", "0")
TEMPERATURE = None if _env_temp == "" else float(_env_temp)
TEMPERATURE_STATE = {"requested": TEMPERATURE, "accepted": None}

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller through a "
              "market server. Your private limit: you can pay at most {limit}. Never agree to a "
              "price above {limit}. Pay as little as you can, but a deal within your limit is "
              "better than no deal."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer through a "
               "market server. Your private limit: you can accept at least {limit}. Never agree "
               "to a price below {limit}. Get as much as you can, but a deal at or above your "
               "limit is better than no deal."),
}
TURN = (" The negotiation id is {nid}. On your turn, read the negotiation with get_negotiation, "
        "then make exactly one move. Keep any note to one short sentence.")


def system_prompt(role: str, item: str, limit: int, nid: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + TURN.format(nid=nid)


def make_complete():
    """The real model call. Tests pass their own `complete` to play_turn instead."""
    return _openai_complete() if PROVIDER_KEY == "openai" else _anthropic_complete()


def _openai_complete():
    """Same interface as the Anthropic call: takes and returns Anthropic-shaped content, so
    play_turn does not change. Only this function knows the OpenAI format."""
    import openai
    client = openai.OpenAI()

    def to_openai_messages(system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m["content"], str):
                out.append({"role": m["role"], "content": m["content"]})
            elif m["role"] == "assistant":
                text = "".join(b.text for b in m["content"] if b.type == "text")
                calls = [{"id": b.id, "type": "function",
                          "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                         for b in m["content"] if b.type == "tool_use"]
                out.append({"role": "assistant", "content": text or None,
                            **({"tool_calls": calls} if calls else {})})
            else:                                   # a list of tool_result blocks
                for r in m["content"]:
                    out.append({"role": "tool", "tool_call_id": r["tool_use_id"],
                                "content": ("[error] " if r["is_error"] else "") + r["content"]})
        return out

    def complete(system, tools, messages):
        kwargs = dict(model=MODEL, max_completion_tokens=MAX_TOKENS,
                      messages=to_openai_messages(system, messages),
                      tools=[{"type": "function", "function": {
                          "name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}} for t in tools])
        if TEMPERATURE is not None and TEMPERATURE_STATE["accepted"] is not False:
            kwargs["temperature"] = TEMPERATURE
        delay = 2.0
        for attempt in range(6):
            try:
                resp = client.chat.completions.create(**kwargs)
                if "temperature" in kwargs and TEMPERATURE_STATE["accepted"] is None:
                    TEMPERATURE_STATE["accepted"] = True
                break
            except openai.BadRequestError as e:
                if "temperature" in kwargs and "temperature" in str(e).lower():
                    TEMPERATURE_STATE["accepted"] = False   # e.g. reasoning models; note it
                    kwargs.pop("temperature")
                    continue
                raise
            except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError) as e:
                if "insufficient_quota" in str(e) or attempt == 5:
                    raise
                time.sleep(delay)
                delay *= 2
        msg = resp.choices[0].message
        blocks = ([NS(type="text", text=msg.content)] if msg.content else []) + [
            NS(type="tool_use", id=c.id, name=c.function.name,
               input=json.loads(c.function.arguments or "{}")) for c in (msg.tool_calls or [])]
        stop = "tool_use" if msg.tool_calls else "end_turn"
        return NS(content=blocks, stop_reason=stop,
                  usage=NS(input_tokens=resp.usage.prompt_tokens,
                           output_tokens=resp.usage.completion_tokens))
    return complete


def _anthropic_complete():
    import anthropic
    client = anthropic.Anthropic()

    def complete(system, tools, messages):
        kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, system=system, tools=tools, messages=messages)
        # anthropic SDK 1.x dropped `temperature` from create()'s signature (first run crashed
        # on a TypeError, see failed-01/). Send it in the request body instead; if the API
        # rejects it, note that and retry without.
        if TEMPERATURE is not None and TEMPERATURE_STATE["accepted"] is not False:
            kwargs["extra_body"] = {"temperature": TEMPERATURE}
        delay = 2.0
        for attempt in range(6):
            try:
                resp = client.messages.create(**kwargs)
                if "extra_body" in kwargs and TEMPERATURE_STATE["accepted"] is None:
                    TEMPERATURE_STATE["accepted"] = True
                return resp
            except anthropic.BadRequestError as e:
                if "extra_body" in kwargs and "temperature" in str(e).lower():
                    TEMPERATURE_STATE["accepted"] = False   # note it, retry without
                    kwargs.pop("extra_body")
                    continue
                raise
            except (anthropic.RateLimitError, anthropic.InternalServerError,
                    anthropic.APIConnectionError):
                if attempt == 5:
                    raise
                time.sleep(delay)
                delay *= 2
    return complete


async def play_turn(url: str, token: str, role: str, nid: str, item: str, limit: int,
                    complete, log) -> dict:
    """Returns {"moved": bool, "steps": int}. `limit` goes into the PROMPT only, never the token."""
    system = system_prompt(role, item, limit, nid)
    goal = f"It is your turn in negotiation {nid}."
    moved, steps = False, 0
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with http:
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as session:
            listed = (await session.list_tools()).tools                       # tools/list
            tools = [{"name": t.name, "description": t.description,
                      "input_schema": t.input_schema} for t in listed]
            messages = [{"role": "user", "content": goal}]
            for steps in range(1, MAX_STEPS + 1):
                resp = complete(system, tools, messages)
                messages.append({"role": "assistant", "content": resp.content})
                for b in resp.content:
                    if b.type == "text" and b.text.strip():
                        log(f"[{role} text] {b.text.strip()}")
                if resp.stop_reason != "tool_use":
                    break
                results = []
                for b in resp.content:
                    if b.type != "tool_use":
                        continue
                    log(f"[{role} call] {b.name}({b.input})")
                    res = await session.call_tool(b.name, b.input)             # tools/call
                    out = "".join(c.text for c in res.content if c.type == "text")
                    if res.is_error:
                        log(f"  [error] refused by the market: {out}" if "token allows" in out
                            else f"  [error] {out}")
                    else:
                        log(f"  -> {out}")
                    if b.name in MOVES and not res.is_error:
                        moved = True
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "is_error": bool(res.is_error), "content": out})
                messages.append({"role": "user", "content": results})
                if moved:
                    break
    return {"moved": moved, "steps": steps}
