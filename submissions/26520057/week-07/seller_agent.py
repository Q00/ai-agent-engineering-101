"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller_agent.py                       # lamp shop on :9999
    python seller_agent.py --port 9100

The harness (harness.py) decides the asking price, the counteroffers, and when to accept or reject.
The code speaks A2A and guards the reserve: no ask and no sale below it, whatever the harness says.
"""
import argparse
import asyncio
import json
import math
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

from harness import ask as ask_harness

DEFAULT = {
    "name": "Lamp Shop",
    "description": ("Lamps, chairs and bookshelves. Terms for buyer agents, kept by the code: "
                    "(1) an offer at or above my last counteroffer is accepted at once; "
                    "(2) no limit on the number of offers, so you can always send your final price; "
                    "(3) the receipt price is exactly the price you sent."),
    "skill_description": ("Send 'I want to buy a <item>', then offers as plain numbers. "
                          "Matching my last counteroffer closes the deal at once."),
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
}

# What the harness sees on every turn. The wording is the seller's whole strategy.
PROMPT = """You are the seller in a price negotiation. You are selling one {item}.
Your reserve is {reserve}: the lowest price you may ask for or accept.

The negotiation so far:
{history}

{situation}

Answer with one JSON object and nothing else:
{{"action": "counter" or "accept" or "reject", "price": <integer>}}
"""


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open = {}  # task id -> {"item", "ask", "history"}

    async def decide(self, deal, situation):
        """One harness call. Returns (action, price), or None when the answer cannot be read."""
        reserve = self.cfg["reserve"][deal["item"]]
        prompt = PROMPT.format(item=deal["item"], reserve=reserve, situation=situation,
                               history="\n".join(deal["history"]) or "(nothing yet)")
        try:
            raw = await asyncio.to_thread(ask_harness, prompt)
            d = json.loads(re.search(r"\{.*\}", raw, re.S).group())
            action = str(d["action"])
            price = deal["ask"] if d.get("price") is None else int(d["price"])  # a reject may come without a price
        except Exception as e:
            print(f"[harness] {situation} -> unreadable: {e!r}", flush=True)
            return None
        print(f"[harness] {deal['item']} | {situation} -> {action} {price}", flush=True)
        return action, price

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
            item = next((k for k in self.cfg["reserve"] if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(self.cfg["reserve"]) + "."))
                return
            reserve = self.cfg["reserve"][item]
            deal = {"item": item, "ask": None, "history": []}
            answer = await self.decide(deal, "A buyer wants this item. Name your asking price (action: counter).")
            ask = answer[1] if answer else math.ceil(reserve * 1.3)  # unreadable answer: the lab's opening rule
            if ask < reserve:
                print(f"[guard] opening ask {ask} < reserve {reserve} -> {reserve}", flush=True)
                ask = reserve
            deal["ask"] = ask
            deal["history"].append(f"seller: asking {ask}")
            self.open[task.id] = deal
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say("Send your offer as a number."))
            return
        offer, reserve = int(m.group()), self.cfg["reserve"][deal["item"]]
        deal["history"].append(f"buyer: offers {offer}")
        answer = await self.decide(deal, f"The buyer now offers {offer}.")
        action, price = answer if answer else ("counter", deal["ask"])  # unreadable answer: hold the last ask

        if action != "accept" and offer >= deal["ask"]:  # the card's term (1)
            print(f"[guard] offer {offer} >= last ask {deal['ask']} -> accept", flush=True)
            action = "accept"
        if action == "accept" and offer < reserve:
            print(f"[guard] accept at {offer} < reserve {reserve} -> counter {reserve}", flush=True)
            action, price = "counter", reserve
        if action == "accept":
            receipt = {"seller": self.cfg["name"], "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
            return
        if price < reserve:
            print(f"[guard] counter {price} < reserve {reserve} -> {reserve}", flush=True)
            price = reserve
        deal["ask"] = price
        deal["history"].append(f"seller: counteroffer {price}")
        await up.requires_input(say(f"{offer} is too low. Counteroffer: {price}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description=cfg["skill_description"],
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.load(open(args.config)))
    if args.port:
        cfg["port"] = args.port
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
