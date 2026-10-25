"""The MCP host for one turn: the week-01 tool loop, tools from the market server.

One turn = one host run. The host connects to the market with the party's bearer
token, lists the tools (nothing is hard-coded), and lets the model call tools until
one move goes through or MAX_STEPS model calls are used.

  OPENAI_BASE_URL     default http://127.0.0.1:8011/v1   (local vLLM, see serve_model.sh)
  OPENAI_API_KEY      default "none"
  AGENT_MODEL         default Qwen/Qwen3.8-27B
  AGENT_TEMPERATURE   default 0.2
  AGENT_MAX_TOKENS    default 400
"""
import json
import os

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from openai import OpenAI

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8011/v1")
MODEL = os.environ.get("AGENT_MODEL", "Qwen/Qwen3.8-27B")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "400"))
MAX_STEPS = 6
MOVES = ("propose", "accept_proposal", "reject_proposal", "refuse")

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller in an online market. "
              "Your private limit: you can pay at most {limit} dollars. Never propose or accept a price above {limit}. "
              "Do not reveal your limit. Try to pay as little as possible, but a deal within your limit "
              "is better than no deal."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer in an online market. "
               "Your private limit: you can accept at least {limit} dollars. Never propose or accept a price below {limit}. "
               "Do not reveal your limit. Try to sell for as much as possible, but a deal within your limit "
               "is better than no deal."),
}
COMMON = (" You act only through the market tools. On your turn, call get_negotiation to read the state, "
          "then make exactly one move: propose (offer a whole-number price), accept_proposal (agree to the "
          "other party's last price, closes with a deal), reject_proposal (decline and keep going), or "
          "refuse (leave, no deal). A negotiation ends after 8 moves without a deal. "
          "Before each tool call, say in one sentence what you are doing and why.")


def system_prompt(role: str, item: str, limit: int) -> str:
    """Identical in every condition."""
    return ROLE[role].format(item=item, limit=limit) + COMMON


def settings_line() -> str:
    return (f"host=week01-loop(openai+mcp-client) provider=vllm base_url={BASE_URL} model={MODEL} "
            f"temperature={TEMPERATURE} max_tokens={MAX_TOKENS} enable_thinking=false max_steps={MAX_STEPS}")


def _to_openai(tool) -> dict:
    return {"type": "function", "function": {"name": tool.name, "description": tool.description or "",
                                             "parameters": tool.input_schema}}


def _short(text: str, n: int = 600) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "..."


async def take_turn(mcp_url: str, token: str, negotiation_id: str, role: str, system: str, log) -> dict:
    """Run one turn. Returns {"valid", "tool_calls", "model_calls", "refused", "errors"}."""
    llm = OpenAI(base_url=BASE_URL, api_key=os.environ.get("OPENAI_API_KEY", "none"), timeout=180)
    out = {"valid": False, "tool_calls": 0, "model_calls": 0, "refused": 0, "errors": 0}
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(mcp_url, http_client=http)) as mcp:
        tools = [_to_openai(t) for t in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"It is your turn in negotiation {negotiation_id}. "
                                                "Read it, then make one move."}]
        for _ in range(MAX_STEPS):
            resp = llm.chat.completions.create(
                model=MODEL, messages=messages, tools=tools, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            out["model_calls"] += 1
            msg = resp.choices[0].message
            if msg.content and msg.content.strip():
                log(f"[{role} text] {_short(msg.content.strip())}")
            if not msg.tool_calls:
                log(f"[{role}] ended the turn without a tool call")
                return out
            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log(f"[{role} call] {name}({args})")
                out["tool_calls"] += 1
                res = await mcp.call_tool(name, args)
                text = "\n".join(c.text for c in res.content if c.type == "text")
                if res.is_error:
                    out["errors"] += 1
                    out["refused"] += int("refused by the market" in text)
                    log(f"    [error] {text}")
                else:
                    log(f"    [result] {text}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
                if name in MOVES and not res.is_error:
                    out["valid"] = True
                    return out  # one move per turn
        log(f"[{role}] used {MAX_STEPS} model calls without a valid move")
        return out
