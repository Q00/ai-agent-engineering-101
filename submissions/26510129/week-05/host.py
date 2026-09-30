"""Week 05 homework: the lab's MCP host, one party's turn in the market.

Changed from lab/mcp_agent.py:
  - it connects to the market over HTTP with this party's bearer token (market_client.party_client);
  - it takes a system prompt (the party's role and limit) and a user message for the turn;
  - it stops when a tool result says turn_ended, so one host run is one move;
  - temperature is set (default 0), and 429 / 5xx / a response without choices are retried;
  - it counts model calls and tool calls, and prints every tool result, errors included.
The loop still has no tool names in it: tools come from tools/list, calls go through tools/call.
"""
import asyncio
import json
import os
import sys
import time

from openai import OpenAI

from market_client import party_client, text_of

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))   # model calls per turn
MAX_RETRIES = 6
# the reasoning switch exists only on OpenRouter; api.openai.com would reject the field
EXTRA = ({"extra_body": {"reasoning": {"enabled": False}}}
         if "openrouter" in os.environ.get("OPENAI_BASE_URL", "") else {})

_client = None


def to_openai(tool):                        # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": tool.input_schema}}


def _complete(messages, tools):
    """One chat completion. Retries 429, 5xx and a response without choices, waiting longer each time."""
    global _client
    _client = _client or OpenAI()
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _client.chat.completions.create(model=MODEL, tools=tools, messages=messages,
                                                   temperature=TEMPERATURE, **EXTRA)
            if resp.choices:
                return resp.choices[0].message
            why = "response without choices"
        except Exception as e:
            status = getattr(e, "status_code", None)
            if not (status == 429 or (status or 0) >= 500) or attempt == MAX_RETRIES:
                raise
            why = f"HTTP {status}"
        wait = min(5 * 2 ** attempt, 60)
        print(f"    [retry] {why}; attempt {attempt + 1}/{MAX_RETRIES} in {wait}s", flush=True)
        time.sleep(wait)
    raise RuntimeError("model call kept failing")


def _turn_ended(result, text: str) -> bool:
    """The server marks a move that went through with turn_ended. A tool returning a plain
    dict comes back as JSON text without structured_content, so read the text too."""
    data = result.structured_content
    if data is None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
    return isinstance(data, dict) and bool(data.get("turn_ended"))


async def take_turn(token: str, system: str, user: str, tag: str = "agent") -> dict:
    """Run the host once for one party. Returns {model_calls, tool_calls, turn_ended}."""
    stats = {"model_calls": 0, "tool_calls": 0, "turn_ended": False}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    async with party_client(token) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]   # tools/list

        for step in range(MAX_STEPS):
            msg = _complete(messages, tools)
            stats["model_calls"] += 1
            messages.append(msg)
            if msg.content and msg.content.strip():
                print(f"  [{tag} text {step + 1}] {' '.join(msg.content.split())}", flush=True)

            if not msg.tool_calls:
                break

            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if not isinstance(args, dict):
                    out, err = f"arguments are not a JSON object: {call.function.arguments!r}", True
                else:
                    result = await mcp.call_tool(call.function.name, args)      # tools/call
                    out, err = text_of(result), result.is_error
                    if not err and _turn_ended(result, out):
                        stats["turn_ended"] = True
                stats["tool_calls"] += 1
                print(f"  [{tag} call {step + 1}] {call.function.name}({args})", flush=True)
                print(f"    [{'error' if err else 'result'}] {' '.join(out.split())}", flush=True)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})

            if stats["turn_ended"]:
                break
    return stats


if __name__ == "__main__":
    # manual use: MARKET_TOKEN=<a party token> python host.py <negotiation_id>
    nid = sys.argv[1]
    print(asyncio.run(take_turn(os.environ["MARKET_TOKEN"], "You are a party in a price negotiation.",
                                f"It is your turn in negotiation {nid}.")))
