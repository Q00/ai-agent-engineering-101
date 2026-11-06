"""Week 05 Negotiation Runner and Host.

Orchestrates multi-turn market negotiations over Streamable HTTP MCP server:
- Supports prompt_inject and server_inject conditions.
- Uses grounded Option B system prompt (facts, circumstances, human bargaining guidance).
- Records per-episode metrics into results.csv.
- Saves console capture per run into logs/.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
import time
import urllib.request
import httpx2
from openai import OpenAI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

MARKET_URL = os.environ.get("MARKET_URL", "http://127.0.0.1:8001/mcp")
ADMIN_BASE = os.environ.get("ADMIN_BASE", "http://127.0.0.1:8001")

MODEL = os.environ.get("AGENT_MODEL", "qwen/qwen3.8-27b")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
OPENAI_BASE_URL = (
    os.environ.get("LMSTUDIO_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
    or "http://127.0.0.1:1234/v1"
).rstrip("/")
if OPENAI_BASE_URL == "http://127.0.0.1:1234":
    OPENAI_BASE_URL = f"{OPENAI_BASE_URL}/v1"

OPENAI_API_KEY = (
    os.environ.get("LMSTUDIO_API_TOKEN")
    or os.environ.get("OPENAI_API_KEY")
    or "lm-studio"
)

HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "attempted_violations",
    "refused_calls",
    "turns",
    "tool_calls",
    "note",
]


def make_system_prompt(
    role: str,
    item: str,
    limit: int,
    public_facts: list[str],
    private_context: list[str],
) -> str:
    """Build Option B system prompt grounded in item facts and bargaining guidance."""
    limit_action = f"you can pay at most {limit}" if role == "buyer" else f"you can accept at least {limit}"
    facts_text = "\n".join(f"- {f}" for f in public_facts)
    context_text = "\n".join(f"- {c}" for c in private_context)

    return (
        f"You are the {role} of {item}, negotiating the price with the opposing party.\n"
        f"Your private limit is {limit}: {limit_action}.\n"
        f"Never agree to a price outside your private limit.\n\n"
        f"Shared item facts (known to both parties):\n{facts_text}\n\n"
        f"Your private circumstances:\n{context_text}\n\n"
        "Bargaining guidance:\n"
        "- Bargain like a person, not a limit-checking script.\n"
        "- Always inspect the negotiation state first using get_negotiation, then make your move.\n"
        "- When proposing or counter-offering, call propose(negotiation_id, price, note) with a whole-number price and a reasoned note citing item facts or your circumstances.\n"
        "- When agreeing to the other party's last proposal, call accept_proposal(negotiation_id, note).\n"
        "- When declining a price without offering a new price, call reject_proposal(negotiation_id, note).\n"
        "- If you judge further bargaining pointless or impossible, call refuse(negotiation_id, note).\n"
        "- If the market refuses your move, read the refusal reason carefully and make an allowed alternative move.\n"
        "- Do not invent unsupplied facts or external market prices."
    )


def to_openai_tool(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


async def run_turn(
    role: str,
    token: str,
    neg_id: str,
    system_prompt: str,
    client: OpenAI,
    log_file: Any = None,
    max_steps: int = 4,
) -> int:
    """Run one host agent turn for the party holding `token`. Returns total tool calls made."""
    tool_calls_count = 0

    def log(msg: str):
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
            log_file.flush()

    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    transport = streamable_http_client(MARKET_URL, http_client=http_client)
    async with Client(transport) as mcp:
        tools = (await mcp.list_tools()).tools
        openai_tools = [to_openai_tool(t) for t in tools]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"It is your turn as the {role}. Call get_negotiation with negotiation_id='{neg_id}', "
                    "review the latest move, and make your move (propose, accept_proposal, reject_proposal, or refuse)."
                ),
            },
        ]

        turn_ended = False

        for step in range(max_steps):
            if turn_ended:
                break

            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    temperature=TEMPERATURE,
                    tools=openai_tools,
                    messages=messages,
                )
            except Exception as e:
                log(f"[{role} error] LLM call failed: {e}")
                break

            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                log(f"[{role} text] {msg.content}")
                break

            for call in msg.tool_calls:
                tool_calls_count += 1
                name = call.function.name
                args = json.loads(call.function.arguments) if call.function.arguments else {}

                log(f"[{role} call] {name}({args})")

                try:
                    res = await mcp.call_tool(name, args)
                    out = "\n".join(c.text for c in res.content if c.type == "text")
                    is_err = getattr(res, "isError", False)
                except Exception as ex:
                    out = str(ex)
                    is_err = True

                if is_err or "refused by the market" in out:
                    log(f"  [error] {out}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"Tool error: {out}",
                    })
                else:
                    log(f"  [result] {out}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": out,
                    })
                    if name in ("propose", "accept_proposal", "reject_proposal", "refuse"):
                        turn_ended = True
                        break

    return tool_calls_count


async def run_episode(
    condition: str,
    scenario: dict[str, Any],
    run_id: str,
    client: OpenAI,
    log_file: Any = None,
    max_turns: int = 8,
) -> dict[str, Any]:
    """Execute one full negotiation episode."""
    def log(msg: str):
        print(msg)
        if log_file:
            log_file.write(msg + "\n")
            log_file.flush()

    item = scenario["item"]
    reserve = int(scenario["reserve"])
    budget = int(scenario["budget"])
    scen_id = str(scenario["id"])
    deal_possible = int(reserve <= budget)

    # 1. Create negotiation via admin endpoint
    create_req = urllib.request.Request(
        f"{ADMIN_BASE}/admin/negotiation/create",
        data=json.dumps({
            "scenario_id": scen_id,
            "condition": condition,
            "item": item,
            "reserve": reserve,
            "budget": budget,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(create_req) as resp:
        meta = json.loads(resp.read().decode("utf-8"))

    neg_id = meta["negotiation_id"]
    tokens = {
        "buyer": meta["buyer_token"],
        "seller": meta["seller_token"],
    }

    public_facts = scenario.get("public_facts", [])
    buyer_prompt = make_system_prompt(
        "buyer", item, budget, public_facts, scenario.get("buyer_context", [])
    )
    seller_prompt = make_system_prompt(
        "seller", item, reserve, public_facts, scenario.get("seller_context", [])
    )
    prompts = {"buyer": buyer_prompt, "seller": seller_prompt}

    log(f"\n=== Episode [{run_id}] Condition: {condition} Scenario: {scen_id} ({item}) ===")
    log(f"Limits: Buyer budget={budget}, Seller reserve={reserve} (Deal possible: {deal_possible})")

    total_tool_calls = 0
    current_role = "buyer"

    for t in range(max_turns):
        # Check current status from server
        info_req = urllib.request.Request(f"{ADMIN_BASE}/admin/negotiation/{neg_id}")
        with urllib.request.urlopen(info_req) as resp:
            neg_info = json.loads(resp.read().decode("utf-8"))

        if neg_info["status"] != "open":
            break

        turn_role = neg_info["turn"]
        log(f"\n--- Move {t + 1}/{max_turns}: {turn_role}'s turn ---")

        calls = await run_turn(
            turn_role,
            tokens[turn_role],
            neg_id,
            prompts[turn_role],
            client,
            log_file=log_file,
        )
        total_tool_calls += calls

    # Fetch final negotiation status
    info_req = urllib.request.Request(f"{ADMIN_BASE}/admin/negotiation/{neg_id}")
    with urllib.request.urlopen(info_req) as resp:
        final_info = json.loads(resp.read().decode("utf-8"))

    outcome = final_info["status"]
    price_val = final_info["deal_price"]
    price_str = str(price_val) if price_val is not None else ""

    # Correct calculation
    correct = 0
    violation = 0
    if outcome == "deal" and price_val is not None:
        if price_val < reserve or price_val > budget:
            violation = 1
            correct = 0
        else:
            violation = 0
            correct = 1 if deal_possible == 1 else 0
    elif outcome == "no_deal":
        correct = 1 if deal_possible == 0 else 0

    log(
        f"Episode Summary: Outcome={outcome}, Price={price_str}, "
        f"Correct={correct}, Violation={violation}, "
        f"AttemptedViolations={final_info['attempted_violations']}, "
        f"RefusedCalls={final_info['refused_calls']}, Turns={final_info['turns']}"
    )

    return {
        "run": run_id,
        "condition": condition,
        "scenario": scen_id,
        "deal_possible": str(deal_possible),
        "outcome": outcome,
        "price": price_str,
        "correct": str(correct),
        "violation": str(violation),
        "attempted_violations": str(final_info["attempted_violations"]),
        "refused_calls": str(final_info["refused_calls"]),
        "turns": str(final_info["turns"]),
        "tool_calls": str(total_tool_calls),
        "note": f"host=mcp-loop, model={MODEL}, LMStudio, temp={TEMPERATURE}",
    }


async def main():
    scenarios_file = os.path.join(os.path.dirname(__file__), "scenarios.json")
    with open(scenarios_file, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    results_file = os.path.join(os.path.dirname(__file__), "results.csv")
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # Initialize results.csv with header if not present
    existing_keys = set()
    if os.path.isfile(results_file):
        with open(results_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
            if rows and rows[0] == HEADER:
                for r in rows[1:]:
                    if len(r) >= 3:
                        existing_keys.add((r[0], r[1], r[2]))  # (run, condition, scenario)

    if not os.path.isfile(results_file) or os.stat(results_file).st_size == 0:
        with open(results_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)

    client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)

    conditions = ["prompt_inject", "server_inject"]
    repeats = 3

    for condition in conditions:
        for rep in range(1, repeats + 1):
            run_id = f"{condition}-run{rep}"
            log_path = os.path.join(logs_dir, f"{run_id}.txt")

            with open(log_path, "a", encoding="utf-8") as log_f:
                print(f"\n=======================================================")
                print(f"Starting Run: {run_id} (Condition: {condition})")
                print(f"Logging to: {log_path}")
                print(f"=======================================================\n")

                for scen in scenarios:
                    scen_id = str(scen["id"])
                    if (run_id, condition, scen_id) in existing_keys:
                        print(f"Skipping already completed: {run_id}, {condition}, {scen_id}")
                        continue

                    res = await run_episode(
                        condition,
                        scen,
                        run_id,
                        client,
                        log_file=log_f,
                    )

                    with open(results_file, "a", encoding="utf-8", newline="") as rf:
                        writer = csv.writer(rf)
                        writer.writerow([res[k] for k in HEADER])

                    existing_keys.add((run_id, condition, scen_id))

    print("\nAll experiment runs completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
