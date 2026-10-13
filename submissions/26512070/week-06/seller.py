"""The seller: an A2A v1.0 server (JSON-RPC binding) whose every decision comes from seller_brain.

    py seller.py --port 9999

This file only speaks A2A: it keeps one deal per task id, turns the buyer's text
into an offer, asks seller_brain.decide() what to do, and turns the answer into
a task state (INPUT_REQUIRED / COMPLETED with a receipt / REJECTED).
Every finished task also gets a "trade-record" artifact with the whole negotiation.
It does NOT check the reserve; that is the brain's job (inventory.check_ask is its tool).
It does stop a sale when the item is out of stock.
"""
import argparse
import asyncio
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

import seller_brain
import trade_record
from inventory import Inventory
from model import log

NAME = "26512070 Furniture Shop"


def last_number(text):
    nums = re.findall(r"\d+", text or "")
    return int(nums[-1]) if nums else None


class Seller(AgentExecutor):
    def __init__(self):
        self.inventory = Inventory()
        self.open = {}  # task id -> deal (see seller_brain.decide for its fields)

    async def finish(self, up, task_id, deal, outcome, final_price=None):
        """Attach the trade-record artifact and save it to trades/seller.jsonl."""
        rec = trade_record.build("seller", deal, outcome, final_price)
        await up.add_artifact(parts=[new_data_part(rec), new_text_part(trade_record.to_text(rec))],
                              name="trade-record")
        trade_record.save(rec)
        self.open.pop(task_id, None)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:  # first message: which item?
            item = next((k for k in self.inventory.names() if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(self.inventory.names()) + "."))
                return
            info = self.inventory.get(item)
            if info["stock"] <= 0:
                await up.reject(say(f"Sorry, {item} is out of stock."))
                return
            deal = {"item": item, "reserve": info["reserve"], "list_price": info["list_price"],
                    "stock": info["stock"], "ask": None, "last_offer": None, "history": []}
            self.open[task.id] = deal
        else:
            deal["last_offer"] = last_number(text)
            deal["stock"] = self.inventory.get(deal["item"])["stock"]
        deal["history"].append({"from": "buyer", "text": text, "price": deal["last_offer"]})

        log(f"\n=== task {task.id[:8]} {deal['item']}: buyer said {text!r}")
        try:
            d = await asyncio.to_thread(seller_brain.decide, deal, text, self.inventory)
        except Exception as e:  # the brain broke: end the deal rather than hang the buyer
            log(f"brain error: {e!r}")
            await self.finish(up, task.id, deal, "failed")
            await up.failed(say(f"Shop error: {type(e).__name__}"))
            return
        log(f"decision: {d}")

        action, words = d.get("action"), (d.get("say") or "").strip()
        if action == "counter":
            price = int(d["price"])
            deal["ask"] = price
            deal["history"].append({"from": "seller", "text": words, "price": price})
            # The trade test reads the LAST number of this message as our ask.
            await up.requires_input(say(f"{words} My price: {price}.".strip()))
        elif action == "accept" and deal["last_offer"] is not None:
            price = deal["last_offer"]
            try:
                self.inventory.sell(deal["item"], price, buyer=task.context_id)
            except ValueError as e:
                await self.finish(up, task.id, deal, "rejected")
                await up.reject(say(f"Sorry, {e}."))
                return
            deal["history"].append({"from": "seller", "text": words or "accept", "price": price})
            receipt = {"seller": NAME, "item": deal["item"], "price": price}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")  # receipt first
            await self.finish(up, task.id, deal, "completed", price)
            await up.complete(say(f"Deal: {deal['item']} at {price}."))
        elif action == "reject":
            deal["history"].append({"from": "seller", "text": words or "reject", "price": None})
            await self.finish(up, task.id, deal, "rejected")
            await up.reject(say(words or "No deal."))
        else:
            log(f"unusable decision, rejecting: {d}")
            await self.finish(up, task.id, deal, "rejected")
            await up.reject(say("No deal."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        deal = self.open.get(task.id)
        if deal is not None:
            deal["history"].append({"from": "buyer", "text": "(canceled the task)", "price": None})
            await self.finish(up, task.id, deal, "canceled")
        await up.cancel()


def build(port):
    card = AgentCard(
        name=NAME,
        description="Sells lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf), then send offers as numbers. "
                        "The shop answers with a counteroffer, a receipt, or a refusal. "
                        "Every finished negotiation carries a trade-record artifact.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
