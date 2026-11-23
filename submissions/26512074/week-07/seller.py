"""Small-room furniture shop: A2A transport, bounded retrieval, model decisions."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from catalog import Catalog
from harness import decide, emit, money
from signing import sign_receipt, whole


class Seller(AgentExecutor):
    def __init__(self, catalog, name):
        self.catalog, self.name = catalog, name
        self.memory = {}
        self.locks = {}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        # Serialize simultaneous offers for one task; other tasks remain concurrent.
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            await self.turn(task, get_message_text(context.message) or "", event_queue)

    async def turn(self, task, text, queue):
        up = TaskUpdater(event_queue=queue, task_id=task.id, context_id=task.context_id)
        def say(message, **metadata):
            return up.new_agent_message(parts=[new_text_part(message)], metadata=metadata)

        state = self.memory.get(task.id)
        if state is not None and state.get("terminal"):
            # Normally the SDK rejects terminal tasks before dispatching execution.
            raise ValueError("task is already terminal")
        if state is None:
            candidates = self.catalog.search(text)
            try:
                feedback = json.loads(Path("pricing-feedback.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                feedback = {"opening_margin": 0.2, "rationale": "Initial policy; awaiting own market observations."}
            emit("catalog_retrieval", task_id=task.id, query=text,
                 catalog_size=len(self.catalog.items), candidate_count=len(candidates),
                 candidates=candidates)
            def validate(answer):
                if answer.get("action") == "reject":
                    return
                if answer.get("action") != "ask":
                    raise ValueError("opening seller action must be ask or reject")
                product = next((p for p in candidates if p["id"] == answer.get("item_id")), None)
                if product is None:
                    raise ValueError("select an actual retrieved product ID")
                if money(answer.get("price")) < product["reserve"]:
                    raise ValueError("opening ask is below selected reserve")
                whole(answer["price"])
            answer = await decide("seller_open", {"candidates": candidates,
                "pricing_guidance": feedback,
                "instruction": "Use the locally observed opening_margin as guidance for the opening ask; "
                               "select the exact named product ID when present. Prices must be whole numbers."},
                                  {"buyer_message": text}, validator=validate)
            if answer["action"] == "reject":
                self.memory[task.id] = {"terminal": True}
                await up.reject(say(answer["message"]))
                return
            product = next(p for p in candidates if p["id"] == answer["item_id"])
            state = {"product": product, "ask": answer["price"], "turn": 0,
                     "summary": answer["summary"], "terminal": False}
            self.memory[task.id] = state
        else:
            product = state["product"]
            def validate(answer):
                action = answer.get("action")
                if action not in ("ask", "accept", "reject"):
                    raise ValueError("seller action must be ask, accept or reject")
                if answer.get("item_id") != product["id"]:
                    raise ValueError("selected product cannot change during negotiation")
                if action in ("ask", "accept"):
                    whole(answer.get("price"))
                    if money(answer.get("price")) < product["reserve"]:
                        raise ValueError("price violates immutable reserve")
                if action == "accept":
                    if money(answer.get("observed_price")) != answer["price"]:
                        raise ValueError("receipt price must equal the current interpreted offer")
            answer = await decide("seller_turn", state,
                                  {"buyer_message": text}, validator=validate)
            state.update(summary=answer["summary"], turn=state["turn"] + 1)
            if answer["action"] == "reject":
                state["terminal"] = True
                await up.reject(say(answer["message"]))
                return
            if answer["action"] == "accept":
                state["terminal"] = True
                data = sign_receipt({"shop": self.name, "item": product["id"],
                                     "price": whole(answer["price"]), "task": task.id})
                await up.add_artifact(parts=[new_data_part(data)], name="receipt")
                await up.complete(say(f"Deal for {product['name']} at {answer['price']:g}."))
                emit("sale_completed", task_id=task.id, reserve=product["reserve"], receipt=data)
                return
            state["ask"] = answer["price"]
        # The last textual number is always the ask, regardless of model phrasing.
        # Metadata additionally permits semantic multi-number conversations.
        await up.requires_input(say(
            f"{answer['message']} Product: {state['product']['id']}. Asking {state['ask']:g}",
            ask=state["ask"], item_id=state["product"]["id"]))
        emit("seller_memory", task_id=task.id, item_id=state["product"]["id"],
             reserve=state["product"]["reserve"], ask=state["ask"],
             turn=state["turn"], summary=state["summary"])

    async def cancel(self, context, event_queue):
        task = context.current_task
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            self.memory.setdefault(task.id, {})["terminal"] = True
            await TaskUpdater(event_queue=event_queue, task_id=task.id,
                              context_id=task.context_id).cancel()


def build(port, *, categories=None, price_factor=1.0, name="Small Room Furniture"):
    catalog = Catalog(categories=categories, price_factor=price_factor)
    card = AgentCard(
        name=name, description="Constraint-aware furniture shopping for small rooms.",
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC",
            protocol_version="1.0", url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id=category, name=f"Buy {category}",
            description=f"Sells {category} furniture. Describe material, maximum height in cm, "
                        "book capacity, USB-C or lumbar needs; then negotiate a numeric price.",
            tags=[category, "shopping", "small-room", "constraint-search"])
            for category in catalog.describe_categories()])
    handler = DefaultRequestHandler(agent_executor=Seller(catalog, name),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--categories", nargs="+")
    parser.add_argument("--price-factor", type=float, default=1.0)
    parser.add_argument("--name", default="Small Room Furniture")
    args = parser.parse_args()
    uvicorn.run(build(args.port, categories=args.categories, price_factor=args.price_factor,
                      name=args.name), host="127.0.0.1", port=args.port, log_level="warning")
