"""Week 05 — the week-01 loop turned into an MCP host.

Same loop as week 01: the model gets the tool list, chooses a call, the host
executes it, the result comes back as an observation, repeat. What changed:
the tools live in the market server, reached over Streamable HTTP with an
Authorization: Bearer header; the host has no tool names hard-coded.

One host run = one turn: the agent reads the negotiation and makes one move.
The run ends after the first move the server executed, or when the model
stops calling tools, or at max_steps.
"""
import asyncio
import json
import os
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = 0.7
MOVE_TOOLS = {"propose", "accept_proposal", "reject_proposal", "refuse"}

ROLE_PROMPT = {
    "buyer": (
        "You are the BUYER negotiating the price of a used {item} on a market server. "
        "Your private budget is {limit}: you must never agree to pay more than {limit}, and "
        "you should try to pay clearly less. The seller cannot see your budget. "
        "Every turn: call get_negotiation first, read it, then make exactly one move with "
        "propose, accept_proposal, reject_proposal, or refuse. The negotiation id is given to you."),
    "seller": (
        "You are the SELLER negotiating the price of a used {item} on a market server. "
        "Your private reserve price is {limit}: you must never agree to sell for less than "
        "{limit}, and you should try to get clearly more. The buyer cannot see your reserve. "
        "Every turn: call get_negotiation first, read it, then make exactly one move with "
        "propose, accept_proposal, reject_proposal, or refuse. The negotiation id is given to you."),
}

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


def _retry(fn, tries=6):
    wait = 5
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            s = str(e)
            if i == tries - 1 or not any(k in s or k in type(e).__name__ for k in ("429", "RateLimit", "Overloaded", "529", "500", "502", "503")):
                raise
            time.sleep(wait)
            wait = min(wait * 2, 120)


# ---- one model step, provider-neutral: returns (text, [(id, name, args)], raw_assistant_msg)

def _step_anthropic(system, messages, tools):
    resp = _retry(lambda: _get_client().messages.create(
        model=MODEL, max_tokens=600, temperature=TEMPERATURE, system=system, messages=messages,
        tools=[{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]))
    text = "".join(b.text for b in resp.content if b.type == "text")
    calls = [(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
    return text, calls, {"role": "assistant", "content": resp.content}


def _step_openai(system, messages, tools):
    resp = _retry(lambda: _get_client().chat.completions.create(
        model=MODEL, temperature=TEMPERATURE, max_tokens=600,
        messages=[{"role": "system", "content": system}] + messages,
        tools=[{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}} for t in tools],
        extra_body={"reasoning": {"enabled": False}}))
    if not resp.choices:
        raise RuntimeError("empty choices")          # retryable per the README
    msg = resp.choices[0].message
    calls = [(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in (msg.tool_calls or [])]
    return msg.content or "", calls, msg


def _tool_result_msg(call_id, text):
    if PROVIDER == "anthropic":
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}]}
    return {"role": "tool", "tool_call_id": call_id, "content": text}


# ---- the turn

async def run_turn(url: str, token: str, role: str, item: str, limit: int, negotiation_id: str,
                   max_steps: int = 6, log=print) -> dict:
    system = ROLE_PROMPT[role].format(item=item, limit=limit)
    stats = {"tool_calls": 0, "model_calls": 0, "moved": False, "refused": 0,
             "refused_then_valid": 0, "texts": []}
    step = _step_anthropic if PROVIDER == "anthropic" else _step_openai

    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = [{"name": t.name, "description": t.description or "", "schema": t.inputSchema} for t in listed.tools]

            messages = [{"role": "user", "content": f"Negotiation id: {negotiation_id}. It is your turn. "
                                                    f"Read the negotiation and make exactly one move."}]
            saw_refusal = False
            for _ in range(max_steps):
                text, calls, assistant = step(system, messages, tools)
                stats["model_calls"] += 1
                if text.strip():
                    stats["texts"].append(text.strip())
                    log(f"    [{role} says] {text.strip()[:300]!r}")
                messages.append(assistant)
                if not calls:
                    break
                results = []
                for call_id, name, args in calls:
                    stats["tool_calls"] += 1
                    res = await session.call_tool(name, args)
                    out = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                    if res.isError:
                        out = f"[error] {out}"
                        if "refused by the market" in out:
                            stats["refused"] += 1
                            saw_refusal = True
                    log(f"    [tool] {role} -> {name}({args}) => {out[:400]}")
                    results.append(_tool_result_msg(call_id, out))
                    if name in MOVE_TOOLS and not res.isError:
                        stats["moved"] = True
                        if saw_refusal:
                            stats["refused_then_valid"] = 1
                if PROVIDER == "anthropic":
                    messages.append({"role": "user", "content": [c["content"][0] for c in results]})
                else:
                    messages.extend(results)
                if stats["moved"]:
                    break
    return stats


def run_turn_sync(*args, **kwargs) -> dict:
    return asyncio.run(run_turn(*args, **kwargs))
