"""Week 05 — one turn of one party, played by an MCP host.

A "turn" is one host execution: connect to the market with THIS party's token, let the model
read the negotiation and make one move. If the market refuses a move (tool error), the model
sees the reason and may try again inside the same turn; that is the "same-turn recovery" the
report counts. If the model stops without a valid move, the runner passes the turn.

The system prompt is the same in all four conditions, character for character except the
role, item and limit. The only thing that tells the agent which condition it is in is the
market's refusal message.

Settings (env): ANTHROPIC_API_KEY (never committed), AGENT_MODEL (default claude-haiku-4-5-20251001),
AGENT_TEMPERATURE (default 0; "" to omit).
"""
import os
import time

import anthropic
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

PROVIDER = "Anthropic Messages API"
HOST_NAME = "custom loop (week-01 loop over MCP Streamable HTTP)"
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
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
