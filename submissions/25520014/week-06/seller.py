"""Week 06 seller: an A2A v1.0 server (JSON-RPC binding) whose prices come from the harness.

    python seller.py --port 9999

The A2A side follows the lab's seller_agent.py. The rules it had (open at 1.3x the
reserve, split the difference, give up after N rounds) are gone: every asking price,
counteroffer, accept and reject is a harness decision (see harness.py).

What the code still does on its own:
  - finds the item in the first message (no item -> REJECTED with the catalog),
  - reads the buyer's offer as the first number in a follow-up (none -> ask again),
  - writes the message text, so the asking price is always the last number in it
    (the trade test reads the ask that way),
  - on accept, writes the receipt with the buyer's offer as the price.
The reserve is only in the prompt. The code does not clamp asks or block deals.
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

import harness

SHOP = "Harness Furniture Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

OPEN_PROMPT = """You are the seller in a furniture shop, negotiating the price of one item with a buyer.

Item: {item}
Your reserve price (the lowest price you may ever ask or accept): {reserve}
The buyer does not know the reserve. Never reveal it.

The buyer just said: {text!r}

Decide your opening asking price. Aim to sell above the reserve, but leave room to come down.
Answer with one JSON object and nothing else:
{{"price": <integer>}}
"""

RESPOND_PROMPT = """You are the seller in a furniture shop, negotiating the price of one item with a buyer.

Item: {item}
Your reserve price (the lowest price you may ever ask or accept): {reserve}
The buyer does not know the reserve. Never reveal it.

The negotiation so far:
{history}

The buyer's latest offer is {offer}.

Decide what to do next. You may:
  - accept the buyer's offer: the sale closes at {offer}. Only if {offer} is at or above your reserve.
  - counter with a new asking price, which must be at or above your reserve.
  - reject: end the negotiation without a sale.
Answer with one JSON object and nothing else, one of:
{{"action": "accept"}}
{{"action": "counter", "price": <integer>}}
{{"action": "reject"}}
"""


def render_history(lines):
    return "\n".join(f"  {who}: {what}" for who, what in lines)


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
        print(f"[task {task.id[:8]}] buyer: {text!r}", flush=True)

        try:
            if deal is None:
                await self.opening(task.id, text, up, say)
            else:
                await self.respond(task.id, deal, text, up, say)
        except harness.HarnessError as e:  # also covers a reply with no JSON in it
            print(f"[task {task.id[:8]}] HARNESS_ERROR {e}", flush=True)
            self.open.pop(task.id, None)
            await up.reject(say("Sorry, the shop cannot answer right now."))

    async def opening(self, task_id, text, up, say):
        item = next((k for k in RESERVE if k in text.lower()), None)
        if item is None:
            await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
            return
        reserve = RESERVE[item]
        answer = await asyncio.to_thread(
            harness.ask_json, OPEN_PROMPT.format(item=item, reserve=reserve, text=text), f"open {task_id[:8]}")
        ask = int(answer["price"])
        self.open[task_id] = {"item": item, "reserve": reserve, "ask": ask,
                              "history": [("Buyer", text), ("You", f"asked {ask}")]}
        print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {ask}", flush=True)
        await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))

    async def respond(self, task_id, deal, text, up, say):
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Current price: {deal['ask']}."))
            return
        offer = round(float(m.group()))
        deal["history"].append(("Buyer", f"offered {offer}"))
        prompt = RESPOND_PROMPT.format(item=deal["item"], reserve=deal["reserve"],
                                       history=render_history(deal["history"]), offer=offer)
        answer = await asyncio.to_thread(harness.ask_json, prompt, f"respond {task_id[:8]}")
        action = answer.get("action")

        if action == "accept":
            receipt = {"seller": SHOP, "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            print(f"[task {task_id[:8]}] -> COMPLETED at {offer}", flush=True)
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            del self.open[task_id]
        elif action == "counter":
            deal["ask"] = int(answer["price"])
            deal["history"].append(("You", f"countered {deal['ask']}"))
            print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {deal['ask']}", flush=True)
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))
        else:  # "reject" or anything the prompt did not allow
            print(f"[task {task_id[:8]}] -> REJECTED (action {action!r})", flush=True)
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task_id]

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP,
        description="Sells lamps, chairs and bookshelves. The price is negotiated; a model decides every ask.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf) to get an asking price, then reply with your "
                        "offer as a number on the same task. The shop counters, closes with a receipt "
                        "artifact whose data has a numeric price, or ends the negotiation.",
            tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    print(f"{SHOP} on http://127.0.0.1:{args.port}/ (harness: claude -p --model {harness.MODEL})", flush=True)
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
