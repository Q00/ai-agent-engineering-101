"""Directory-based concurrent buyer with disjoint budget envelopes and a ledger."""
from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from buyer import buy
from harness import decide, emit
from protocol import A2AClient, amount, task_state, task_text

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "assignments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"line_id": {"type": "string"}, "shop_id": {"type": "string"},
                           "budget": {"type": "number"}},
            "required": ["line_id", "shop_id", "budget"]}},
        "summary": {"type": "string"}},
    "required": ["assignments", "summary"]}


async def shop(directory, shopping, total_budget, ledger_path):
    amount(total_budget, "total budget")
    ids = [line["id"] for line in shopping]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("shopping list must contain distinct nonempty line IDs")
    shop_ids = [entry["id"] for entry in directory]
    if len(shop_ids) != len(set(shop_ids)):
        raise ValueError("directory shop IDs must be distinct")
    ledger_path = Path(ledger_path)
    if ledger_path.exists():
        # Never repeat a purchase after restart, including a lost completion response.
        raise ValueError("ledger already exists; reconcile prior purchases before a new run")

    async def discover(entry):
        async with A2AClient(entry["url"], label=entry["id"]) as client:
            card = await client.discover()
            return {"id": entry["id"], "url": entry["url"], "name": card.get("name"),
                    "skills": card["skills"]}
    cards = await asyncio.gather(*(discover(entry) for entry in directory))
    by_shop = {card["id"]: card for card in cards}

    def validate(plan):
        assignments = plan["assignments"]
        if sorted(a["line_id"] for a in assignments) != sorted(ids):
            raise ValueError("assign each shopping line exactly once")
        allocated = Decimal(0)
        for assignment in assignments:
            if assignment["shop_id"] not in by_shop:
                raise ValueError("unknown directory shop ID")
            value = amount(assignment["budget"], "line budget")
            if value <= 0:
                raise ValueError("each budget envelope must be positive")
            allocated += Decimal(str(value))
        if allocated > Decimal(str(total_budget)):
            raise ValueError("sum of budget envelopes exceeds immutable total budget")

    plan = await decide("market_plan", {"shopping_list": shopping, "total_budget": total_budget},
                        {"directory_cards": cards}, schema=PLAN_SCHEMA, validator=validate)
    lines = {line["id"]: line for line in shopping}
    routes = {a["line_id"]: a["shop_id"] for a in plan["assignments"]}

    async def quote(assignment):
        line_id = assignment["line_id"]
        card = by_shop[assignment["shop_id"]]
        async with A2AClient(card["url"], label=line_id) as client:
            await client.discover()
            def validate_open(answer):
                if answer.get("action") != "open" or lines[line_id]["item"].casefold() not in answer.get("message", "").casefold():
                    raise ValueError("opening must ask about the exact requested product")
            opening = await decide("buyer_open", {"item": lines[line_id]["item"],
                "budget": assignment["budget"]}, {"skills": card["skills"]}, validator=validate_open)
            task = await client.send(opening["message"])
            if task_state(task) != "TASK_STATE_INPUT_REQUIRED":
                raise ValueError("preflight quote did not return an open negotiation")
            ask = amount(task["status"]["message"].get("metadata", {}).get("ask"), "quoted ask")
            return line_id, task, ask

    quotes = await asyncio.gather(*(quote(a) for a in plan["assignments"]))
    initial_tasks = {line_id: task for line_id, task, ask in quotes}
    quoted_asks = {line_id: ask for line_id, task, ask in quotes}
    all_asks_fit = sum(Decimal(str(a)) for a in quoted_asks.values()) <= Decimal(str(total_budget))

    def validate_quoted(plan):
        validate(plan)
        for assignment in plan["assignments"]:
            line_id = assignment["line_id"]
            if assignment["shop_id"] != routes[line_id]:
                raise ValueError("keep the quoted shop route; never open duplicate purchases")
            if all_asks_fit and assignment["budget"] < quoted_asks[line_id]:
                raise ValueError("all asks fit the total; allocate enough for each quoted ask")

    plan = await decide("market_plan", {"shopping_list": shopping, "total_budget": total_budget,
        "phase": "Allocate envelopes after quotes; keep the same routes. When all asks fit, "
                 "each envelope must cover its current quoted ask. Otherwise negotiate within the total.",
        "routes": routes, "all_asks_fit": all_asks_fit},
        {"quotes": [{"line_id": line_id, "shop_id": routes[line_id], "ask": ask,
                     "seller_message": task_text(task)} for line_id, task, ask in quotes]},
        schema=PLAN_SCHEMA, validator=validate_quoted)
    emit("market_plan", shops=len(cards), total_budget=total_budget, plan=plan,
         quoted_asks=quoted_asks, all_asks_fit=all_asks_fit)
    ledger = {"total_budget": total_budget, "plan": plan, "lines": {}}

    def persist():
        # Single asyncio event loop: updates and replacement contain no awaits.
        tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ledger_path)

    # Allocate all envelopes before any offer: concurrency cannot double-spend them.
    for assignment in plan["assignments"]:
        ledger["lines"][assignment["line_id"]] = {**assignment, "state": "allocated"}
    persist()

    async def purchase(assignment):
        line_id = assignment["line_id"]
        def progress(memory):
            ledger["lines"][line_id].update(state="negotiating", memory=memory)
            persist()
        outcome = await buy(by_shop[assignment["shop_id"]]["url"], lines[line_id]["item"],
                            assignment["budget"], line_id=line_id, on_progress=progress,
                            initial_task=initial_tasks[line_id])
        ledger["lines"][line_id].update(state=outcome["outcome"], result=outcome)
        persist()
        return outcome

    outcomes = await asyncio.gather(*(purchase(a) for a in plan["assignments"]))
    spent = sum((Decimal(str(r["price"])) for r in outcomes if r["outcome"] == "completed"), Decimal(0))
    completed = all(r["outcome"] == "completed" for r in outcomes)
    ledger.update(passed=completed and spent <= Decimal(str(total_budget)), spent=float(spent))
    persist()
    emit("market_finished", passed=ledger["passed"], spent=ledger["spent"],
         total_budget=total_budget, lines=len(outcomes), unique_lines=len(ledger["lines"]))
    return ledger


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--shopping-list", type=Path, required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(shop(json.loads(args.directory.read_text(encoding="utf-8")),
                              json.loads(args.shopping_list.read_text(encoding="utf-8")),
                              args.budget, args.ledger))
    raise SystemExit(0 if result["passed"] else 1)
