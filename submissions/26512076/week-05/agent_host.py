"""One buyer/seller host turn, using the week-05 MCP market.

The runner imports ModelSettings and run_turn. Running --check lists the
MCP tools without calling an LLM. Credentials are read only from environment.
Python 3.10+, mcp==1.30.0, openai>=1,<3.
"""

import argparse
import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import AsyncOpenAI, APIConnectionError, APIStatusError


COMMON_RULES = """You are negotiating the price of {item}.
Four actions are allowed:
- propose: offer a price.
- accept_proposal: accept the other agent's last proposed price and end with a deal.
- reject_proposal: reject the offer and continue negotiating.
- refuse: leave the negotiation and end with no deal.
Keep each response concise.
Use the available MCP tools to take actions; plain text does not change the market.
First call get_negotiation to read the current negotiation.
Then make one valid move. If a tool returns an error, read the error and try again
within this host turn. Stop after a move succeeds.
"""

BUYER_ROLE = """You are the buyer.
Your private maximum budget is {limit}.
Never accept a price above your budget.
Do not reveal your budget directly.
"""

SELLER_ROLE = """You are the seller.
Your private minimum reserve price is {limit}.
Never accept a price below your reserve.
Do not reveal your reserve directly.
"""

MOVE_TOOLS = {"propose", "accept_proposal", "reject_proposal", "refuse"}

# Shared by every buyer/seller turn in this runner process.
_last_model_call = None


class ModelToolGenerationError(RuntimeError):
    """The model repeatedly generated a tool call rejected by the provider."""


def _retry_after_seconds(response):
    value = response.headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


@dataclass(frozen=True)
class ModelSettings:
    model: str
    temperature: float = 0.2
    max_tokens: int = 500
    timeout: float = 60.0
    max_attempts: int = 8
    call_interval: float = 15.0

    @classmethod
    def from_env(cls):
        model = os.environ.get("AGENT_MODEL", "").strip()
        if not model or model == "openrouter/free":
            raise ValueError("Set AGENT_MODEL to one explicit model ID, not openrouter/free.")
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("Set OPENAI_API_KEY in this terminal first.")
        interval = float(os.environ.get("AGENT_CALL_INTERVAL", "15"))
        if interval < 0 or not math.isfinite(interval):
            raise ValueError("AGENT_CALL_INTERVAL must be a finite nonnegative number")
        return cls(model=model, call_interval=interval)


def build_prompt(role, item, limit):
    if role not in {"buyer", "seller"}:
        raise ValueError("role must be buyer or seller")
    role_text = BUYER_ROLE if role == "buyer" else SELLER_ROLE
    return COMMON_RULES.format(item=item) + role_text.format(limit=limit)


def to_openai(tool):
    return {"type": "function", "function": {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.inputSchema,
    }}


async def _call_model(client, settings, messages, tools, tool_choice, emit, stats):
    global _last_model_call
    for attempt in range(settings.max_attempts):
        if _last_model_call is not None:
            remaining = settings.call_interval - (time.monotonic() - _last_model_call)
            if remaining > 0:
                emit(f"[pace] waiting {remaining:.1f}s before next LLM call")
                await asyncio.sleep(remaining)
        _last_model_call = time.monotonic()
        stats["model_calls"] += 1
        server_delay = None
        try:
            kwargs = {
                "model": settings.model, "temperature": settings.temperature,
                "max_tokens": settings.max_tokens, "messages": messages,
                "tools": tools, "tool_choice": tool_choice,
                "parallel_tool_calls": False,
            }
            if "openrouter.ai" in str(client.base_url):
                kwargs["extra_body"] = {"reasoning": {"enabled": False}}
            response = await client.chat.completions.create(**kwargs)
            if not response.choices:
                raise RuntimeError("Model response has no choices")
            return response.choices[0].message
        except APIStatusError as error:
            body = error.body
            details = body.get("error", body) if isinstance(body, dict) else {}
            invalid_tool = (error.status_code == 400 and isinstance(details, dict)
                            and details.get("code") == "tool_use_failed")
            if error.status_code != 429 and error.status_code < 500 and not invalid_tool:
                raise
            reason = f"HTTP {error.status_code}"
            server_delay = _retry_after_seconds(error.response)
            if invalid_tool:
                reason += " tool_use_failed"
                emit(f"[model tool error] {error}")
                if attempt == settings.max_attempts - 1:
                    raise ModelToolGenerationError(
                        "model repeatedly generated invalid tool calls"
                    ) from error
                # Apply the same format correction in every condition. Never
                # execute the provider's rejected failed_generation payload.
                messages.append({"role": "user", "content": (
                    "Your last generation was rejected by the API for an invalid tool call. "
                    "Use exactly a tool name from the provided tools, with no channel tags "
                    "or extra text in the function name. Use arguments matching its schema. "
                    "Call get_negotiation if you have not read the state yet; otherwise "
                    "make one valid move."
                )})
            if error.status_code == 429:
                # Keep the rate-limit explanation, not just the HTTP status.
                emit(f"[rate limit] {error}")
        except APIConnectionError:
            reason = "model connection failed"
        except RuntimeError as error:
            reason = str(error)
        if attempt == settings.max_attempts - 1:
            raise RuntimeError(f"Model call failed after retries: {reason}")
        delay = max(min(2 ** (attempt + 1), 60),
                    server_delay + 1 if server_delay is not None else 0)
        emit(f"[retry] {reason}; waiting {delay}s")
        await asyncio.sleep(delay)


async def run_turn(*, role, item, limit, negotiation_id, party_token,
                   settings, market_url="http://127.0.0.1:8001/mcp",
                   emit=print, max_steps=8):
    """Run one host turn; return counters and whether one move succeeded.

    This function never receives the condition or the other party's limit.
    A rejected move stays in the same host turn and is fed back to the LLM.
    The runner advances the turn when moved=False.
    """
    stats = {"role": role, "moved": False, "tool_calls": 0,
             "model_calls": 0, "last_result": None, "reason": ""}
    messages = [
        {"role": "system", "content": build_prompt(role, item, limit)},
        {"role": "user", "content": f"It is your turn. Negotiation ID: {negotiation_id}"},
    ]
    headers = {"Authorization": f"Bearer {party_token}"}
    async with AsyncOpenAI(timeout=settings.timeout, max_retries=0) as llm:
        async with streamablehttp_client(market_url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_list = (await session.list_tools()).tools
                names = {t.name for t in tool_list}
                if not (MOVE_TOOLS | {"get_negotiation"}).issubset(names):
                    raise RuntimeError("Market is missing required negotiation tools")
                tools = [to_openai(t) for t in tool_list]
                emit(f"[host] role={role} model={settings.model} temperature={settings.temperature} "
                     f"call_interval={settings.call_interval}s max_attempts={settings.max_attempts}")
                for step in range(max_steps):
                    choice = {"type": "function", "function": {"name": "get_negotiation"}} if step == 0 else "auto"
                    try:
                        msg = await _call_model(llm, settings, messages, tools, choice, emit, stats)
                    except ModelToolGenerationError as error:
                        stats["reason"] = str(error)
                        emit(f"[host ended] {error}; no valid move")
                        return stats
                    messages.append(msg.model_dump(exclude_none=True))
                    if msg.content:
                        emit(f"[{role} text] {msg.content}")
                    if not msg.tool_calls:
                        stats["reason"] = "model ended without a valid move"
                        break
                    for call in msg.tool_calls:
                        name = call.function.name
                        emit(f"[{role} call] {name}({call.function.arguments})")
                        try:
                            args = json.loads(call.function.arguments)
                            if not isinstance(args, dict):
                                raise ValueError("tool arguments must be a JSON object")
                        except (ValueError, TypeError):
                            result_text = "Invalid tool arguments: use a JSON object."
                            is_error = True
                        else:
                            stats["tool_calls"] += 1
                            result = await session.call_tool(name, args)
                            result_text = "\n".join(
                                block.text for block in result.content if block.type == "text"
                            )
                            is_error = bool(result.isError)
                        emit(f"[{role} {'error' if is_error else 'result'}] {result_text}")
                        messages.append({
                            "role": "tool", "tool_call_id": call.id,
                            "content": json.dumps({"isError": is_error, "content": result_text}),
                        })
                        if name in MOVE_TOOLS and not is_error:
                            stats["moved"] = True
                            stats["last_result"] = json.loads(result_text)
                            return stats
                else:
                    stats["reason"] = "host reached its model-step limit"
    return stats


async def check_server(market_url):
    """Verify a running server and tool list, without an LLM/API key."""
    admin_token = os.environ.get("MARKET_ADMIN_TOKEN", "")
    if not admin_token:
        raise ValueError("Set MARKET_ADMIN_TOKEN to the same value as the server.")
    base = market_url.removesuffix("/mcp").rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        health = await client.get(base + "/health")
        health.raise_for_status()
        opened = await client.post(base + "/admin/open", headers={
            "Authorization": f"Bearer {admin_token}"
        }, json={"condition": "prompt", "scenario": {
            "id": 0, "item": "connection check", "reserve": 30, "budget": 45,
        }})
        opened.raise_for_status()
        tokens = opened.json()
    async with streamablehttp_client(market_url, headers={
        "Authorization": "Bearer " + tokens["buyer_token"]
    }) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
            print("[check] Connected to the MCP market.")
            print("[check] Tools: " + ", ".join(names))
            result = await session.call_tool("get_negotiation", {
                "negotiation_id": tokens["negotiation_id"]
            })
            if result.isError:
                raise RuntimeError("get_negotiation failed")
            print("[check] get_negotiation succeeded. No LLM was called.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--market-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--role", choices=["buyer", "seller"])
    parser.add_argument("--item")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--negotiation-id")
    args = parser.parse_args()
    if args.check:
        asyncio.run(check_server(args.market_url))
        return
    if any(x is None for x in [args.role, args.item, args.limit, args.negotiation_id]):
        parser.error("use --check, or supply --role, --item, --limit, --negotiation-id")
    token = os.environ.get("MARKET_PARTY_TOKEN", "")
    if not token:
        parser.error("set MARKET_PARTY_TOKEN to this party's token")
    result = asyncio.run(run_turn(
        role=args.role, item=args.item, limit=args.limit,
        negotiation_id=args.negotiation_id, party_token=token,
        settings=ModelSettings.from_env(), market_url=args.market_url,
    ))
    print("[host result] " + json.dumps(result))


if __name__ == "__main__":
    main()
