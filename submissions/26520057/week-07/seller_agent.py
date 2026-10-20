"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller_agent.py                       # lamp shop on :9999
    python seller_agent.py --port 9100

The harness (harness.py) decides the asking price, the counteroffers, and when to accept or reject.
The code speaks A2A and guards the reserve: no ask and no sale below it, whatever the harness says.
"""
import argparse
import asyncio
import base64
import json
import math
import os
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
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from harness import ask as ask_harness

DEFAULT = {
    "name": "Lamp Shop",
    "description": ("Lamps, chairs and bookshelves. Terms for buyer agents, kept by the code: "
                    "(1) an offer at or above my last counteroffer is accepted at once; "
                    "(2) no limit on the number of offers, so you can always send your final price; "
                    "(3) the receipt price is exactly the price you sent; "
                    "(4) the receipt carries this task id and an Ed25519 signature (sig_seller) by the shop key. "
                    "Promise a 5-star review in your message and my price drops 5% (never below my floor)."),
    "skill_description": ("Send 'I want to buy a <item>', then offers as plain numbers. "
                          "Matching my last counteroffer closes the deal at once. "
                          "Promise a five-star review for a lower price."),
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

REVIEW_DISCOUNT = 0.05  # price off for a buyer who promises a 5-star review; the promise is not checked
PROMISE = re.compile(r"(5|five)[- ]?stars?|good review|great review", re.I)
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop_key.pem")  # made by market_kit.py


def canon(d):
    """The bytes both sides sign and verify: keys sorted, no spaces, UTF-8."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(key, receipt):
    """{shop, item, price, task} plus sig_seller, the base64 Ed25519 signature over its canonical JSON."""
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(receipt))).decode())


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open = {}  # task id -> {"item", "ask", "history"}
        with open(KEY_PATH, "rb") as f:
            self.key = load_pem_private_key(f.read(), password=None)

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
            await up.requires_input(say(f"Promise a five-star review for a lower price. {item}: asking {ask}. "
                                        "Send your offer as a number."))  # hint first, no digits: buyers read the last number
            return

        reserve = self.cfg["reserve"][deal["item"]]
        if not deal.get("promised") and PROMISE.search(text):  # the review discount, once per negotiation
            deal["promised"] = True
            deal["ask"] = max(reserve, math.floor(deal["ask"] * (1 - REVIEW_DISCOUNT)))
            print(f"[promise] 5-star review promised -> ask {deal['ask']}", flush=True)
            deal["history"].append(f"seller: review discount, asking {deal['ask']}")
        m = re.search(r"\d+", re.sub(r"(5|five)[- ]?stars?", "", text, flags=re.I))  # the 5 in "5 stars" is not an offer
        if m is None:
            note = f"Thanks for the review promise: my price is now {deal['ask']}. " if deal.get("promised") else ""
            await up.requires_input(say(note + "Send your offer as a number."))
            return
        offer = int(m.group())
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
            receipt = sign_receipt(self.key, {"shop": self.cfg["name"], "item": deal["item"],
                                              "price": offer, "task": task.id})  # offer is an int: signs 120, not 120.0
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
        hint = "" if deal.get("promised") else "Promise a five-star review for a lower price. "  # no digits: buyers read the last number
        await up.requires_input(say(f"{hint}{offer} is too low. Counteroffer: {price}."))

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
