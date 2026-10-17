"""Single-item buyer loop. Model chooses offers; protocol state controls execution."""
import asyncio

import httpx

from negotiation.a2a_client import ProtocolError, asking_price, metadata_ask, receipt, task_text
from negotiation.contracts import BuyerContext
from negotiation.state import BuyerMemory
from negotiation.price_reading import PriceContext


async def settled_task(client, task, task_id):
    while True:
        if task.get("id") != task_id:
            raise ProtocolError("task_id_changed")
        if task["status"].get("state") not in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
            return task
        await asyncio.sleep(0.25)
        task = await client.get_task(task_id)


def outcome(task, item, budget, agreed_price):
    state = task["status"].get("state")
    if state == "TASK_STATE_COMPLETED":
        data = receipt(task, item, budget, agreed_price)
        return {"outcome": "purchased", "task_id": task["id"], "receipt": data}
    if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
        return {"outcome": "stopped", "task_id": task["id"], "state": state}
    if state == "TASK_STATE_FAILED":
        raise ProtocolError("seller_task_failed")
    if state != "TASK_STATE_INPUT_REQUIRED":
        raise ProtocolError("unexpected_task_state")
    return None


async def negotiate(client, harness, base_url, item, budget, log, *, request_text=None, progress=None, semantic_prices=False):
    card = await client.discover(base_url)
    task = await client.send(request_text if request_text is not None else f"I want to buy a {item}")
    memory = BuyerMemory(task["id"], task.get("contextId", ""))
    previous_ask = None
    if progress is not None:
        await progress(memory)
    for turn in range(8):
        task = await settled_task(client, task, memory.task_id)
        result = outcome(task, item, budget, memory.last_offer)
        if result is not None:
            log.record("buyer_result", **result)
            return result
        text = task_text(task)
        # Catalog mode identifies the selected SKU before any binding price offer.
        selected = [part["data"]["selected_item"]
                    for part in task.get("status", {}).get("message", {}).get("parts", [])
                    if isinstance(part.get("data"), dict) and "selected_item" in part["data"]]
        if selected and (len(selected) != 1 or selected[0].get("item_id") != item):
            await client.cancel(memory.task_id)
            raise ProtocolError("selected_item_mismatch_before_offer")
        if semantic_prices:
            mechanical_ask = metadata_ask(task)
            reading = await harness.price(PriceContext("seller", item, previous_ask, text))
            log.record("ask_reading", task_id=memory.task_id, metadata_ask=mechanical_ask,
                       reading=reading.model_dump())
            if reading.action in {"none", "ambiguous"}:
                raise ProtocolError("seller_ask_unclear")
            if reading.price != mechanical_ask:
                raise ProtocolError("seller_ask_metadata_mismatch")
            current_ask = reading.price
        else:
            current_ask = asking_price(task)
        previous_ask = current_ask
        context = BuyerContext(item, budget, current_ask)
        decision = await harness.buyer(context, memory.observation(text, card, 8 - turn))
        log.record("buyer_decision", task_id=memory.task_id, budget=budget, decision=decision.model_dump())
        memory.apply(text, decision)
        if progress is not None:
            await progress(memory)
        if decision.action == "walk_away":
            task = await client.cancel(memory.task_id)
            task = await settled_task(client, task, memory.task_id)
            result = outcome(task, item, budget, memory.last_offer)
            if result is None:
                raise ProtocolError("cancel_did_not_end_task")
            log.record("buyer_result", **result)
            return result
        try:
            task = await client.send(str(decision.price), memory.task_id)
        except httpx.HTTPError:
            # A sent offer can already be binding. Never blindly send it again.
            log.record("offer_response_lost", task_id=memory.task_id, price=decision.price)
            task = await settled_task(client, await client.get_task(memory.task_id), memory.task_id)
            result = outcome(task, item, budget, memory.last_offer)
            if result is None:
                raise ProtocolError("offer_outcome_unknown") from None
            log.record("buyer_result", recovered_by="GetTask", **result)
            return result
    task = await settled_task(client, task, memory.task_id)
    result = outcome(task, item, budget, memory.last_offer)
    if result is None:
        raise ProtocolError("negotiation_turn_limit")
    log.record("buyer_result", **result)
    return result
