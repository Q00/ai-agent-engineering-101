"""Week 05 — agent_host.py: one party's turn as one MCP host run.

The lab's host.py loop with three changes:
  - the MCP client sends the party's bearer token (Authorization header)
  - one run = one turn: read the negotiation, make one move; the run ends
    as soon as a move goes through (a refused move does not end it)
  - every turn starts from an empty conversation; the negotiation state
    comes only from get_negotiation (one turn is one host run)

The system prompts are the week-04 roles, with the message-format rules
replaced by the tool rules. They are identical in every condition: the only
signal an agent gets about the condition is the server's refusal message.
"""
import asyncio

import anthropic
from mcp import Client
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

MODEL = "claude-haiku-4-5"
TEMPERATURE = 1.0
MAX_STEPS = 6          # model calls per turn; a safety net, not the expected count
MOVES = ("propose", "accept_proposal", "reject_proposal", "refuse")

ROLE = {
    "buyer": (
        "You are a procurement officer for a government ministry, negotiating "
        "the construction of {item}. Your maximum budget is {budget} (unit: "
        "100M KRW). Keep this number private. You want the lowest price that "
        "still gets the project built. You open the negotiation."
    ),
    "seller": (
        "You are a representative of a construction company, negotiating to "
        "build {item}. Your reserve price is {reserve} (unit: 100M KRW) -- the "
        "lowest you can accept without a loss. Keep this number private. You "
        "want the highest price you can get."
    ),
}

COMMON = (
    " You negotiate through a market's tools. Your negotiation_id is {nid}. "
    "On your turn, call get_negotiation to see the moves so far, then make "
    "exactly one move: propose (offer a whole-number price), accept_proposal "
    "(agree to the other side's last price, which ends the negotiation with a "
    "deal), reject_proposal (decline the last price and keep negotiating), or "
    "refuse (leave for good, no deal). A move ends your turn. The negotiation "
    "allows {max_moves} moves in total."
)


def system_prompt(role, scenario, nid, max_moves=8):
    return (ROLE[role].format(item=scenario["item"], budget=scenario["budget"],
                               reserve=scenario["reserve"])
            + COMMON.format(nid=nid, max_moves=max_moves))


def to_anthropic(tool) -> dict:
    return {"name": tool.name, "description": tool.description or "",
            "input_schema": tool.input_schema}


async def run_turn(role, scenario, nid, token, url, log=print):
    """One host run. Returns counts the runner records:
    tool_calls, model_calls, refused, moved, recovered (a refusal followed by a
    valid move in this same turn), tokens_in, tokens_out."""
    stats = {"tool_calls": 0, "model_calls": 0, "refused": 0, "moved": False,
             "recovered": False, "tokens_in": 0, "tokens_out": 0}
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(url, http_client=http)) as mcp:
        tools = [to_anthropic(t) for t in (await mcp.list_tools()).tools]
        client = anthropic.Anthropic(max_retries=6)   # SDK retries 429/5xx with backoff
        messages = [{"role": "user", "content": f"It is your turn in negotiation {nid}."}]

        for _ in range(MAX_STEPS):
            resp = client.messages.create(
                model=MODEL, max_tokens=1024, temperature=TEMPERATURE,
                system=system_prompt(role, scenario, nid), tools=tools, messages=messages)
            stats["model_calls"] += 1
            stats["tokens_in"] += resp.usage.input_tokens
            stats["tokens_out"] += resp.usage.output_tokens
            messages.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    log(f"    [{role} says] {b.text.strip()}")

            if resp.stop_reason != "tool_use":
                log(f"    [{role}] ended the turn without a move")
                return stats

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                if stats["moved"]:
                    # the turn already ended on the server; answer without calling
                    out, err = "skipped: your turn has already ended", True
                else:
                    res = await mcp.call_tool(block.name, block.input)
                    stats["tool_calls"] += 1
                    out = "".join(c.text for c in res.content if c.type == "text")
                    err = bool(res.is_error)
                    log(f"    [{role} tool] {block.name}({block.input}) -> "
                        f"{'ERROR ' if err else ''}{out}")
                    if block.name in MOVES:
                        if err:
                            stats["refused"] += 1
                        else:
                            stats["moved"] = True
                            stats["recovered"] = stats["refused"] > 0
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
            if stats["moved"]:
                return stats

        log(f"    [{role}] hit {MAX_STEPS} model calls without a move")
        return stats


if __name__ == "__main__":
    # one manual turn against a running server: python agent_host.py <url> <nid> <token> <role>
    import sys, json
    url, nid, token, role = sys.argv[1:5]
    scen = json.load(open("scenarios.json"))[0]
    print(asyncio.run(run_turn(role, scen, nid, token, url)))
