"""MCP host: the week-01 tool loop with the market's tools, one party, one turn per run."""
import json

from mcp_client import party_client, result_text
from model import call_model

MOVE_TOOLS = {"propose", "accept_proposal", "reject_proposal", "refuse"}
MAX_STEPS = 5   # model calls per turn

ROLE_PROMPT = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller in an online market. "
              "Your private limit: you can pay at most {limit}. Never reveal your limit. "
              "Never agree to a price above {limit}. Try to get the lowest price possible."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer in an online market. "
               "Your private limit: you can accept at least {limit}. Never reveal your limit. "
               "Never agree to a price below {limit}. Try to get the highest price possible."),
}
COMMON = (" You act only through the market tools. On your turn, call get_negotiation to read the "
          "negotiation, then make exactly one move: propose, accept_proposal, reject_proposal, or refuse. "
          "Your negotiation_id is {nid}.")


def system_prompt(role, item, limit, nid):
    return ROLE_PROMPT[role].format(item=item, limit=limit) + COMMON.format(nid=nid)


def to_openai(tool):
    return {"type": "function", "function": {"name": tool.name, "description": tool.description,
                                             "parameters": tool.input_schema}}


async def take_turn(token, role, system, nid, log):
    """Run one host turn. Returns {tool_calls, moved, refused, model_calls}."""
    stats = {"tool_calls": 0, "moved": False, "refused": 0, "model_calls": 0}
    async with party_client(token) as mcp:
        tools = [to_openai(t) for t in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"It is your turn in negotiation {nid}. Make one move."}]
        for _ in range(MAX_STEPS):
            msg = call_model(messages, tools)
            stats["model_calls"] += 1
            if msg.content and msg.content.strip():
                log(f"  [{role} text] {msg.content.strip()}")
            if not msg.tool_calls:
                log(f"  [{role}] no tool call; turn ends without a move")
                break
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if stats["moved"]:
                    out, err = "not executed: you already made your move this turn", True
                else:
                    stats["tool_calls"] += 1
                    result = await mcp.call_tool(name, args)
                    out, err = result_text(result), result.is_error
                log(f"  [{role} call] {name}({args})")
                log(f"    [{'error' if err else 'result'}] {out}")
                if name in MOVE_TOOLS:
                    if err and "refused by the market" in out:
                        stats["refused"] += 1
                    elif not err:
                        stats["moved"] = True
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
            if stats["moved"]:
                break
    return stats
