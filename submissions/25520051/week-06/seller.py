"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Skeleton from the lab's seller_agent.py. The harness decides the asking price,
counteroffers, and when to accept or reject. The code only speaks A2A and
guards two limits: never below the reserve, never above its own previous ask.
"""
import argparse
import asyncio
import re
import sys

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import ask_json

sys.stdout.reconfigure(encoding="utf-8")   # the trade test redirects stdout to a file (cp949 on Windows)

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

SYSTEM = """You are the owner of a small furniture shop, bargaining with a buyer over one item.
You want to sell, but at a good price. You must never go below your reserve price.
Do not raise your price above your previous ask.
Reply with one line of JSON and nothing else:
{"action": "counter" | "accept" | "reject", "price": <integer>, "message": "<one short sentence to the buyer>"}
- counter: you propose "price" (your new ask).
- accept: you accept the buyer's latest offer; "price" is that offer.
- reject: you end the negotiation for good."""


def decide(deal: dict, offer: int | None) -> dict:
    """Ask the harness for the next move; return {"action", "price", "message"}."""
    lines = [f"Item: {deal['item']}", f"Your reserve (lowest price you may accept): {deal['reserve']}"]
    if offer is None:
        lines.append("The buyer just asked for this item. Name your opening asking price (action counter).")
    else:
        lines += ["Conversation so far:"] + [f"  {who}: {text}" for who, text in deal["history"]]
        lines.append(f"Your previous ask: {deal['ask']}. The buyer now offers {offer}. Decide.")
    ans = ask_json(SYSTEM, "\n".join(lines)) or {}
    action = ans.get("action") if ans.get("action") in ("counter", "accept", "reject") else "counter"
    try:
        price = int(round(float(ans.get("price"))))
    except (TypeError, ValueError):
        price = deal["ask"] or round(deal["reserve"] * 1.3)
    message = str(ans.get("message") or "").strip()
    return {"action": action, "price": price, "message": message}


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        print(f"[seller] task {task.id[:8]} <- {text!r}", flush=True)

        if deal is None:  # first message: which item?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            deal = {"item": item, "reserve": RESERVE[item], "ask": None, "history": [("buyer", text)]}
            d = await asyncio.to_thread(decide, deal, None)
            deal["ask"] = max(d["price"], deal["reserve"])            # guard: never ask below reserve
            self.open[task.id] = deal
            await self.counter(up, say, deal, d["message"])
            return

        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. My price: {deal['ask']}"))
            return
        offer = int(float(m.group()))
        deal["history"].append(("buyer", text))
        d = await asyncio.to_thread(decide, deal, offer)
        print(f"[seller] harness -> {d}", flush=True)

        if d["action"] == "accept" and offer < deal["reserve"]:     # guard: never sell below reserve
            print(f"[seller] guard: accept at {offer} < reserve {deal['reserve']}, countering", flush=True)
            d = {"action": "counter", "price": deal["ask"], "message": ""}
        if d["action"] == "accept":
            receipt = {"seller": "Harness Shop", "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"{d['message']} Deal: {deal['item']} at {offer}.".strip()))
            del self.open[task.id]
            return
        if d["action"] == "reject":
            await up.reject(say(d["message"] or "No deal."))
            del self.open[task.id]
            return
        # counter: clamp between reserve and the previous ask
        deal["ask"] = min(max(d["price"], deal["reserve"]), deal["ask"])
        await self.counter(up, say, deal, d["message"])

    async def counter(self, up, say, deal, message):
        # The trade test reads the ask as the LAST number in the message, so the ask always ends it.
        text = f"{message} My price: {deal['ask']}".strip()
        deal["history"].append(("you", text))
        print(f"[seller] -> {text!r}", flush=True)
        await up.requires_input(say(text))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name="Harness Shop",
        description="Sells desk lamps, chairs and bookshelves. A language model negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item, then send a price as a number. The shop answers with a counteroffer "
                        "(the last number in its message), a receipt artifact, or a rejection.",
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
