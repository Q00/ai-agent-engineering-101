"""Model-driven buyer with bounded memory and immutable spending authority."""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from harness import decide, emit
from protocol import A2AClient, amount, receipt, task_state, task_text


async def buy(url, item, budget, *, line_id=None, on_progress=None, max_rounds=6,
              initial_task=None):
    amount(budget, "budget")
    memory = {"item": item, "budget": budget, "task_id": None,
              "last_offer": None, "summary": "No negotiation yet.", "binding_pending": False}
    started = time.monotonic()

    def progress(**fields):
        memory.update(fields)
        if on_progress:
            on_progress(dict(memory))

    def result(outcome, **fields):
        return {"outcome": outcome, "item": item, "task_id": memory["task_id"],
                "last_offer": memory["last_offer"], "summary": memory["summary"], **fields}

    def validate_open(answer):
        if answer.get("action") != "open":
            raise ValueError("begin with action=open to ask the shop about the requested item")
        if item.casefold() not in answer.get("message", "").casefold():
            raise ValueError("opening message must name the requested item exactly")

    def validate_turn(answer):
        if answer.get("action") not in ("offer", "walk"):
            raise ValueError("buyer action must be offer or walk")
        if answer["action"] == "offer":
            price = amount(answer.get("price"))
            if price > budget:
                raise ValueError(f"offer exceeds immutable authorized budget {budget}")

    async def negotiate():
        async with A2AClient(url, label=line_id or item) as client:
            card = await client.discover()
            if initial_task is None:
                opening = await decide(
                    "buyer_open", {"item": item, "budget": budget},
                    {"shop_name": card.get("name"), "skills": card["skills"]},
                    validator=validate_open)
                progress(summary=opening["summary"][:700])
                task = await client.send(opening["message"])
            else:
                task = initial_task
                progress(summary="Preflight quote received; no binding offer has been sent.")
            progress(task_id=task["id"])
            for turn in range(max_rounds + 1):
                state = task_state(task)
                if state == "TASK_STATE_COMPLETED":
                    data = receipt(task, item, budget, memory["last_offer"])
                    progress(binding_pending=False)
                    emit("purchase_verified", line_id=line_id, item=item,
                         price=data["price"], task_id=task["id"], receipt=data)
                    return result("completed", price=data["price"], receipt=data)
                if state in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
                    progress(binding_pending=False)
                    return result("rejected", terminal_state=state)
                if state != "TASK_STATE_INPUT_REQUIRED":
                    raise ValueError(f"unsupported shopping task state {state}")
                progress(binding_pending=False)
                if turn == max_rounds:
                    # An operational limit is not a substitute negotiation policy.
                    await client.cancel(task["id"])
                    return result("failed", error="operational round limit reached")
                trusted = {"item": item, "budget": budget, "turn": turn,
                           "rounds_left": max_rounds - turn,
                           "last_offer": memory["last_offer"], "summary": memory["summary"]}
                untrusted = {"seller_message": task_text(task),
                             "metadata": task.get("metadata", {}),
                             "message_metadata": task.get("status", {}).get("message", {}).get("metadata", {})}
                answer = await decide("buyer_turn", trusted, untrusted, validator=validate_turn)
                progress(summary=answer["summary"][:700])
                emit("buyer_memory", line_id=line_id, task_id=task["id"], turn=turn,
                     summary=memory["summary"], summary_chars=len(memory["summary"]),
                     observed_price=answer.get("observed_price"), action=answer["action"])
                if answer["action"] == "walk":
                    await client.cancel(task["id"])
                    return result("walked")
                offer = answer["price"]
                # Persist authority BEFORE the network request that may complete a sale.
                progress(last_offer=offer, binding_pending=True)
                task = await client.send(format(offer, ".15g"), task["id"])
            raise AssertionError("unreachable")

    try:
        outcome = await asyncio.wait_for(negotiate(), timeout=270)
    except Exception as exc:
        # A lost response is not proof that no purchase happened. Never blind-retry.
        outcome = result("unknown" if memory["binding_pending"] else "failed",
                         error=f"{type(exc).__name__}: {exc}")
        emit("buyer_error", line_id=line_id, **outcome)
    emit("buyer_finished", line_id=line_id, seconds=round(time.monotonic() - started, 3), **outcome)
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    result = asyncio.run(buy(args.url, args.item, args.budget))
    print(json.dumps(result, ensure_ascii=True), flush=True)
    raise SystemExit(0 if result["outcome"] in ("completed", "walked", "rejected") else 1)


if __name__ == "__main__":
    main()
