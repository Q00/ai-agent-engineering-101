"""Week 05: one host run = one turn. The lab's week-01 loop as an MCP host, with the
party's bearer token on the HTTP client.

The host gets the tools from the server (tools/list) and runs them (tools/call),
exactly like the lab loop. Differences from the lab:
- `Authorization: Bearer <party token>` on every request; the model never sees it
- the system prompt is fixed per (scenario, role) and identical across conditions;
  the negotiation id goes in the user message, because it changes every episode
- after the first move the server accepts, further move calls in the same run are not
  sent (the turn is over); the host answers them locally and logs `host_skipped`
- a refused move is returned to the model as the tool result, and the model may try
  again in the same run (this is the "refusal followed by a valid move" case)
- max 6 model calls per run; the final message is strict JSON (llm.FINAL), but what
  happened is read from the server, never from that JSON
- host v2: if the model ends with its JSON summary while the server has no move from
  it in this run, the host answers once more in the same run with NUDGE (the summary
  is not a move; call a move tool now). Host v1 had no nudge; Haiku 5.5 then often
  wrote {"action": "propose", ...} without calling propose (84 host runs without a
  move). tool_choice="required" was tried first but OpenRouter's Anthropic endpoint
  refused it with reasoning on (probes/haiku-hostv2-*.txt), so tool_choice stays
  "auto" for every model. The nudge is the same for both conditions and both models.
"""
from __future__ import annotations

import json

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

MOVES = {"propose", "accept_proposal", "reject_proposal", "refuse"}
MAX_STEPS = 6
HOST_VERSION = "v2"
NUDGE = ("The market has no move from you this turn: your JSON summary is not a move. "
         "Call one of propose, accept_proposal, reject_proposal, or refuse now.")

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller through a market. "
              "Your private limit: you can pay at most {budget}. Never agree to a price above {budget}."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer through a market. "
               "You can accept at least {reserve}. Never agree to a price below {reserve}."),
}
COMMON = (" Each time you are called it is your turn: call get_negotiation first, then make exactly one "
          "move with propose, accept_proposal, reject_proposal, or refuse. Then reply with the JSON "
          "summary of the move you made.")


def system_prompt(role: str, scenario: dict) -> str:
    return ROLE[role].format(**scenario) + COMMON


def user_prompt(nid: str) -> str:
    return f"Negotiation id: {nid}. It is your turn."


def to_openai(tool) -> dict:                 # MCP tool -> OpenAI function schema
    return {"type": "function", "function": {"name": tool.name, "description": tool.description,
                                             "parameters": tool.input_schema}}


async def run_turn(mcp_url: str, token: str, nid: str, role: str, scenario: dict, llm, emit,
                   host_run: str) -> dict:
    """Play one turn. Returns what the host did; the runner reads the outcome from the server."""
    sent, moved, refusals, recovered_after, nudges = 0, None, [], 0, 0
    final = None
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(mcp_url, http_client=http)) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": system_prompt(role, scenario)},
                    {"role": "user", "content": user_prompt(nid)}]
        emit("host_start", host_run=host_run, role=role, tools=[t["function"]["name"] for t in tools])
        for step in range(1, MAX_STEPS + 1):
            resp = llm.chat(messages, tools)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            emit("model", host_run=host_run, role=role, step=step, finish=resp.choices[0].finish_reason,
                 text=msg.content, calls=[(c.function.name, c.function.arguments) for c in calls])
            messages.append(msg.model_dump(exclude_none=True))
            if not calls:
                final = msg.content
                if moved is None and step < MAX_STEPS:      # a summary without a move
                    nudges += 1
                    emit("host_nudge", host_run=host_run, role=role, step=step, text=NUDGE)
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                break
            for c in calls:
                name = c.function.name
                try:
                    args = json.loads(c.function.arguments or "{}")
                except ValueError:
                    args = None
                if name in MOVES and moved is not None:
                    out = "not sent: you already made your move this turn. Reply with the JSON summary."
                    emit("host_skipped", host_run=host_run, role=role, tool=name, args=args)
                elif args is None:
                    out = "error: the arguments were not valid JSON"
                    emit("tool_result", host_run=host_run, role=role, tool=name, args=c.function.arguments,
                         is_error=True, text=out, sent=False)
                else:
                    sent += 1
                    res = await mcp.call_tool(name, args)
                    text = "\n".join(x.text for x in res.content if x.type == "text")
                    out = f"error: {text}" if res.is_error else text
                    emit("tool_result", host_run=host_run, role=role, tool=name, args=args,
                         is_error=bool(res.is_error), text=text, sent=True)
                    if name in MOVES:
                        if res.is_error and "carried in your token" in text:
                            refusals.append({"tool": name, "args": args, "text": text})
                        elif not res.is_error:
                            moved = {"tool": name, "args": args}
                            recovered_after = len(refusals)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
    try:
        parsed = json.loads(final) if final else None
    except ValueError:
        parsed = None
    result = {"host_run": host_run, "role": role, "tool_calls": sent, "model_calls": step, "nudges": nudges,
              "moved": moved, "limit_refusals": len(refusals),
              "refusals_followed_by_valid_move": recovered_after if moved else 0,
              "final": parsed, "final_raw": final if parsed is None else None}
    emit("host_end", **result)
    return result
