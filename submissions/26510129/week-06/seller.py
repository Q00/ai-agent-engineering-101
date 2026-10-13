"""My shop as an A2A v1.0 server (JSON-RPC). The code speaks A2A; the harness sets every price.

    python seller.py --port 9999
"""
import argparse
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

import harness

NAME = "Han's Lamp Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

SYSTEM = (
    "You are the pricing brain of a small furniture shop that negotiates with buyers over a protocol. "
    "You sell lamp, chair, bookshelf. Your reserve prices, which you never ask below and never sell below, are: "
    "lamp 100, chair 180, bookshelf 260. Open above the reserve and come down gradually toward it as the buyer comes up. "
    "Accept any offer at or above the reserve when the buyer has stopped moving. Reject only after several low offers. "
    'Answer with ONE line of JSON and nothing else: {"action":"ask"|"accept"|"counter"|"reject","price":<integer>}'
)


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "log": [(who, text), ...]}

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

        if deal is None:  # first message: which item, and the opening ask
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            d = harness.decide(SYSTEM, f'A new buyer says: "{text}". The item is {item}. Decide the opening ask.')
            if d is None or d.get("action") != "ask" or "price" not in d:
                await up.reject(say("Sorry, the shop could not price that right now."))
                return
            self.open[task.id] = {"item": item, "ask": d["price"], "log": [("buyer", text), ("shop", f"asking {d['price']}")]}
            await up.requires_input(say(f"{item}: asking {d['price']}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Current ask: {deal['ask']}."))
            return
        offer = int(m.group())
        deal["log"].append(("buyer", str(offer)))
        transcript = "\n".join(f"{who}: {what}" for who, what in deal["log"])
        d = harness.decide(SYSTEM, f"Item: {deal['item']}. Your current ask: {deal['ask']}. Transcript so far:\n{transcript}\n"
                                   f"The buyer just offered {offer}. Decide: accept, counter (with a price), or reject.")
        action = (d or {}).get("action")
        if action == "accept":
            receipt = {"seller": NAME, "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
        elif action == "counter" and "price" in d:
            deal["ask"] = d["price"]
            deal["log"].append(("shop", f"counter {d['price']}"))
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))
        else:  # no usable answer: hold the current ask
            deal["log"].append(("shop", f"counter {deal['ask']}"))
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells desk lamps, chairs and bookshelves. A Claude Code (haiku) harness sets every price.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
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
