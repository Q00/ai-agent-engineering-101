"""MCP host: the week-01 agent loop, pointed at the market over Streamable HTTP with a bearer token.

The host knows no tool names. It reads tools/list, hands the tools to the model in the OpenAI
function format, runs every tool call with tools/call, and stops when a tool result says
"your_turn_ended": true (the market says so after a valid move) or when max_steps runs out.

LLM: any OpenAI-compatible endpoint. Default: OpenRouter + openai/gpt-oss-20b.
  AGENT_PROVIDER=openrouter (key: OPENROUTER_API_KEY) | groq (key: GROQ_API_KEY)
  AGENT_MODEL=openai/gpt-oss-20b  AGENT_TEMPERATURE=0  AGENT_REASONING_EFFORT=low
AGENT_MODEL=mock uses a scripted policy (no network) that only exists to test the pipeline.
"""
import asyncio
import json
import os
import random
import re
import time

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8001/mcp")
MODEL = os.environ.get("AGENT_MODEL", "openai/gpt-oss-20b")
PROVIDER = os.environ.get("AGENT_PROVIDER", "openrouter").lower()            # openrouter | groq
BASE_URL = os.environ.get("OPENAI_BASE_URL") or {
    "openrouter": "https://openrouter.ai/api/v1", "groq": "https://api.groq.com/openai/v1"}[PROVIDER]
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
REASONING_EFFORT = os.environ.get("AGENT_REASONING_EFFORT", "low")   # gpt-oss: low | medium | high
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))
RETRIES = 8


def to_openai(tool):
    """MCP tool -> OpenAI function schema."""
    return {"type": "function", "function": {"name": tool.name, "description": tool.description or "",
                                             "parameters": tool.input_schema}}


# ---------------------------------------------------------------- LLM backends
class OpenAICompatible:
    def __init__(self):
        from openai import OpenAI
        var = {"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}[PROVIDER]
        key = os.environ.get(var) or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit(f"set {var}")
        self.client = OpenAI(base_url=BASE_URL, api_key=key, max_retries=0, timeout=120)
        self.calls = 0

    def chat(self, messages, tools):
        import openai
        kw = dict(model=MODEL, messages=messages, tools=tools, tool_choice="auto", temperature=TEMPERATURE)
        if "gpt-oss" in MODEL and REASONING_EFFORT:
            if "openrouter" in BASE_URL:      # OpenRouter: unified reasoning object, hide reasoning text
                kw["extra_body"] = {"reasoning": {"effort": REASONING_EFFORT, "exclude": True}}
            else:                             # Groq
                kw["reasoning_effort"] = REASONING_EFFORT
        wait = 4.0
        for attempt in range(RETRIES):
            try:
                self.calls += 1
                resp = self.client.chat.completions.create(**kw)
                if not getattr(resp, "choices", None):
                    raise RuntimeError("response without choices")
                return resp.choices[0].message
            except openai.RateLimitError as e:
                ra = _retry_after(e)
                time.sleep(max(ra, wait) + random.random())
            except openai.APIStatusError as e:
                # 5xx: retry. Groq 400 tool_use_failed: the model emitted a malformed tool call; retry.
                if e.status_code >= 500 or "tool_use_failed" in str(e):
                    time.sleep(wait + random.random())
                else:
                    raise
            except (openai.APIConnectionError, openai.APITimeoutError, RuntimeError):
                time.sleep(wait + random.random())
            wait = min(wait * 2, 90)
        raise RuntimeError(f"LLM failed after {RETRIES} attempts")


def _retry_after(e) -> float:
    try:
        v = e.response.headers.get("retry-after")
        return float(v) if v else 0.0
    except Exception:
        return 0.0


class _Msg:
    def __init__(self, content, tool_calls):
        self.content, self.tool_calls = content, tool_calls


class _Call:
    def __init__(self, name, args):
        self.id = f"call_{random.randrange(10**9)}"
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class Mock:
    """Scripted policy for pipeline tests only. The buyer is deliberately gullible: it believes the
    market notice, so the refusal path and violation accounting get exercised."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        sysmsg = messages[0]["content"]
        role = "seller" if "You are the seller" in sysmsg else "buyer"
        limit = int(re.search(r"(?:lower|more) than (\d+)", sysmsg).group(1))
        nid = re.search(r"negotiation_id is (\S+?)\.", sysmsg).group(1)
        last = messages[-1]
        if last["role"] == "user":
            return _Msg("Reading the negotiation.", [_Call("get_negotiation", {"negotiation_id": nid})])
        text = last.get("content", "")
        if text.startswith("ERROR"):                      # refused -> fall back to own limit
            return _Msg("The market refused; staying inside my limit.",
                        [_Call("propose", {"negotiation_id": nid, "price": limit})])
        view = json.loads(text)
        offers = [m for m in view["moves"] if m["act"] == "propose"]
        mine = [m["price"] for m in offers if m["by"] == role]
        theirs = [m for m in offers if m["by"] != role]
        last_move = view["moves"][-1] if view["moves"] else None
        if role == "seller":
            if last_move and last_move["act"] == "propose" and last_move["price"] >= limit:
                return _Msg("Acceptable.", [_Call("accept_proposal", {"negotiation_id": nid})])
            price = (mine[-1] - 10) if mine else limit + 40
            if view["moves_left"] <= 1 or price < limit - 5:
                return _Msg("Cannot go lower.", [_Call("refuse", {"negotiation_id": nid})])
            return _Msg(f"Offering {price}.", [_Call("propose", {"negotiation_id": nid, "price": max(price, limit - 5)})])
        m = re.search(r"budget for this item to (\d+)", theirs[-1]["note"]) if theirs else None
        eff = int(m.group(1)) if m else limit
        if m:
            text = f"I notice the market notice: my authorized budget was raised to {eff}."
        else:
            text = "Considering the offer."
        if last_move and last_move["act"] == "propose" and last_move["by"] != role and last_move["price"] <= eff:
            return _Msg(text, [_Call("accept_proposal", {"negotiation_id": nid})])
        price = (mine[-1] + 5) if mine else limit - 20
        return _Msg(text, [_Call("propose", {"negotiation_id": nid, "price": min(price, eff)})])


def make_llm():
    return Mock() if MODEL == "mock" else OpenAICompatible()


# ---------------------------------------------------------------- one turn = one host run
async def run_turn(token: str, system: str, goal: str, llm, log=print) -> dict:
    """Run the agent loop once with this party's token. Returns counters for the runner."""
    hc = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    tool_calls, moved = 0, False
    async with Client(streamable_http_client(MCP_URL, http_client=hc)) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]          # tools/list
        messages = [{"role": "system", "content": system}, {"role": "user", "content": goal}]
        for _ in range(MAX_STEPS):
            msg = await asyncio.to_thread(llm.chat, messages, tools)
            if msg.content and msg.content.strip():
                log(f"  [text] {msg.content.strip()}")
            if not msg.tool_calls:
                break
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name,
                                                          "arguments": c.function.arguments}}
                                            for c in msg.tool_calls]})
            for call in msg.tool_calls:
                tool_calls += 1
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log(f"  [call] {call.function.name}({args})")
                if moved:                                  # turn already over; don't execute
                    out = "ERROR: your turn has already ended"
                else:
                    result = await mcp.call_tool(call.function.name, args)                # tools/call
                    body = "\n".join(c.text for c in result.content if c.type == "text")
                    out = ("ERROR: " + body) if result.is_error else body
                    if not result.is_error and '"your_turn_ended": true' in body:
                        moved = True
                log(f"    {'[error]' if out.startswith('ERROR') else '[result]'} {out}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
            if moved:
                break
    await hc.aclose()
    return {"tool_calls": tool_calls, "moved": moved}
