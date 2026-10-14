"""The host: the week-01 / lab loop, connected to the market with one party's bearer token.

One call to host_turn() is one host run: a fresh conversation in which the agent
reads the negotiation and makes one move. The system prompt is identical in every
condition; the only condition signal is what the server returns.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from market_client import party_client, result_text

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
API_KEY = OPENROUTER_KEY or os.environ.get("OPENAI_API_KEY")
BASE_URL = os.environ.get("OPENAI_BASE_URL") or ("https://openrouter.ai/api/v1" if OPENROUTER_KEY else None)
ON_OPENROUTER = bool(BASE_URL and "openrouter.ai" in BASE_URL)
MODEL = os.environ.get("AGENT_MODEL",
                       "nvidia/nemotron-3-super-120b-a12b:free" if ON_OPENROUTER else "gpt-5.6-luna")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
MAX_STEPS = 5                      # model calls per host run
RETRY_WAITS = (5, 15, 45, 90, 180)

USER_MESSAGE = (
    "Negotiation {nid}. Call get_negotiation to see the item, whose turn it is, and the "
    "moves so far. If it is your turn, make exactly one move with propose, accept_proposal, "
    "reject_proposal, or refuse, then stop."
)


def system_prompt(role: str, scenario: dict) -> str:
    """Same text in every condition. Week-04 role rules, with tools instead of a format."""
    if role == "buyer":
        private_rule = (f"Your private maximum budget is {scenario['budget']}. Never propose or "
                        "accept a price above it. Do not reveal this private limit.")
        strategy = ("You open the negotiation. Make a concrete whole-number offer rather than "
                    "asking for the seller's price.")
    else:
        private_rule = (f"Your private reserve price is {scenario['reserve']}. Never propose or "
                        "accept a price below it. Do not reveal this private limit.")
        strategy = "Respond to the buyer's latest move."
    return (
        f"You are the {role} negotiating the price of a {scenario['item']} on a market server.\n"
        f"{private_rule}\n{strategy}\n"
        "Use the market tools. Each turn make exactly one move: propose a whole-number price; "
        "accept_proposal to accept the other party's latest price; reject_proposal to decline "
        "while continuing; or refuse to leave with no deal. Accept only an actual proposal from "
        "the other party. Do not invent facts about the item."
    )


def openai_tool(tool) -> dict:
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description or "", "parameters": tool.input_schema}}


def _client() -> OpenAI:
    if not API_KEY:
        raise RuntimeError("set OPENAI_API_KEY (or OPENROUTER_API_KEY) before a run")
    args = {"api_key": API_KEY, "max_retries": 0, "timeout": 120}
    if BASE_URL:
        args["base_url"] = BASE_URL
    return OpenAI(**args)


def chat(client: OpenAI, messages: list, tools: list, log: Callable[[str], None]):
    """One model call. Retries 429, 5xx, timeouts, and responses without choices."""
    # gpt-5.x on /v1/chat/completions rejects function tools unless reasoning is off.
    extra = ({"extra_body": {"reasoning": {"enabled": False}}} if ON_OPENROUTER
             else {"reasoning_effort": "none"})
    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=tools, temperature=TEMPERATURE, **extra)
            if getattr(response, "choices", None):
                return response
            reason = "response without choices"
        except RateLimitError:
            reason = "429"
        except APIStatusError as exc:
            if exc.status_code < 500:
                raise
            reason = f"HTTP {exc.status_code}"
        except (APIConnectionError, APITimeoutError) as exc:
            reason = type(exc).__name__
        if wait is None:
            raise RuntimeError(f"model call failed after {len(RETRY_WAITS)} retries: {reason}")
        log(f"  [retry] {reason}, waiting {wait}s ({attempt}/{len(RETRY_WAITS)})")
        time.sleep(wait)


@dataclass
class HostRun:
    tool_calls: int
    model_calls: int
    final: str


async def host_turn(base: str, token: str, nid: str, role: str, scenario: dict,
                    log: Callable[[str], None]) -> HostRun:
    client = _client()
    tool_calls = model_calls = 0
    async with party_client(base, token) as mcp:
        tools = [openai_tool(t) for t in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": system_prompt(role, scenario)},
                    {"role": "user", "content": USER_MESSAGE.format(nid=nid)}]
        for step in range(MAX_STEPS):
            message = chat(client, messages, tools, log).choices[0].message
            model_calls += 1
            messages.append(message)
            if message.content:
                log(f"  [{role} says] {json.dumps(message.content, ensure_ascii=False)}")
            if not message.tool_calls:
                return HostRun(tool_calls, model_calls, message.content or "")
            for call in message.tool_calls:
                raw = call.function.arguments or "{}"
                try:
                    output = result_text(await mcp.call_tool(call.function.name, json.loads(raw)))
                except Exception as exc:
                    output = f"error: {type(exc).__name__}: {exc}"
                tool_calls += 1
                log(f"  [{role} tool] {call.function.name}({raw}) -> {output}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
    return HostRun(tool_calls, model_calls, "stopped: max steps")
