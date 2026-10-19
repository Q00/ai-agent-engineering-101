"""A2A v1.0 shop whose asks and negotiation decisions come from Codex."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import decide, trace


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


class Seller(AgentExecutor):
    def __init__(self) -> None:
        self.open: dict[str, dict] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        message = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            item = next((name for name in RESERVES if re.search(rf"\b{name}\b", message.lower())), None)
            if item is None:
                await updater.reject(say("We sell lamp, chair, and bookshelf."))
                return
            reserve = RESERVES[item]
            answer = await asyncio.to_thread(decide, "seller", {
                "phase": "opening", "item": item, "private_reserve": reserve,
                "buyer_message": message,
                "objective": "Choose a reasonable opening ask near the reserve. A buyer with a modest budget should have a path to a deal.",
            }, ["ask"])
            ask = max(reserve, int(answer["price"]))
            if ask != answer["price"]:
                trace("price_guard", task_id=task.id, reason="ask below reserve", proposed=answer["price"], effective=ask)
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask, "history": []}
            trace("seller_open", task_id=task.id, item=item, reserve=reserve, ask=ask)
            await updater.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        offered = re.fullmatch(r"\s*(\d+)\s*", message)
        if not offered:
            await updater.requires_input(say(f"Please send one whole-number offer. Current ask: {deal['ask']}"))
            return
        offer = int(offered.group(1))
        deal["history"].append({"buyer_offer": offer, "seller_ask": deal["ask"]})
        answer = await asyncio.to_thread(decide, "seller", {
            "phase": "follow_up", "item": deal["item"], "private_reserve": deal["reserve"],
            "current_ask": deal["ask"], "buyer_offer": offer, "history": deal["history"],
            "objective": "Accept a fair offer at or above reserve promptly. Otherwise counter above reserve or reject if agreement is hopeless.",
        }, ["deal", "counter", "reject"])
        action = answer["action"]
        if action == "deal" and offer < deal["reserve"]:
            trace("price_guard", task_id=task.id, reason="deal below reserve", offered=offer)
            action = "counter"
            answer["price"] = deal["reserve"]
        if action == "deal":
            await updater.add_artifact(parts=[new_data_part({
                "seller": "Luna Shop", "item": deal["item"], "price": offer,
            })], name="receipt")
            await updater.complete(say(f"Deal: {deal['item']} at {offer}."))
            trace("seller_deal", task_id=task.id, item=deal["item"], price=offer)
            del self.open[task.id]
            return
        if action == "reject":
            await updater.reject(say(f"No deal for {deal['item']} at {offer}."))
            trace("seller_reject", task_id=task.id, item=deal["item"], offer=offer)
            del self.open[task.id]
            return
        proposed = answer["price"]
        ask = max(deal["reserve"], int(proposed))
        if ask != proposed:
            trace("price_guard", task_id=task.id, reason="counter below reserve", proposed=proposed, effective=ask)
        deal["ask"] = ask
        trace("seller_counter", task_id=task.id, item=deal["item"], offer=offer, ask=ask)
        await updater.requires_input(say(f"Counteroffer: {ask}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int) -> Starlette:
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name="Luna Shop",
        description="Negotiates purchases of a lamp, chair, or bookshelf.",
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name="Negotiate a purchase",
            description="Ask for a lamp, chair, or bookshelf; continue with a numeric offer.",
            tags=["shopping", "lamp", "chair", "bookshelf"],
            examples=["I want to buy a lamp", "110"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
