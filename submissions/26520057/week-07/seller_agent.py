"""A rule-based seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller_agent.py                       # lamp shop on :9999
    python seller_agent.py --port 9100

Week-07 challenge 1: the harness made the shop slow and dear (opening at 150 for a lamp the market sells at 110),
so the rules are now in code. The shop opens at its own asking price (asks.json); its first counter is already
its floor, said as final, so a buyer who can pay the floor never has a reason to walk.
After each negotiation it moves its asking price from its own trades: +20 after a sale on the first offer, -30 after a walkaway.
No ask and no sale below the reserve.
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

ASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asks.json")
START_ASKS = {"lamp": 110, "chair": 200, "bookshelf": 285}  # round-1 market averages: 109.7, 199.2, 284.8
QUICK = 0.9  # a first offer at 90% of the ask or more closes at once (when it is above the floor)
INTRO_VISITORS = 30  # the first 30 buyers get any offer at or above the floor at once, to collect first reviews
STEP_UP, STEP_DOWN = 20, 30  # how the ask moves after a sale on the first offer / after a walkaway

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
        self.open = {}  # task id -> {"item", "ask", "counters", "history"}
        with open(KEY_PATH, "rb") as f:
            self.key = load_pem_private_key(f.read(), password=None)
        try:
            with open(ASKS_PATH) as f:
                self.asks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.asks = dict(START_ASKS)

    def move_ask(self, item, step, why):
        """Challenge 1: the shop changes its own asking price from how its trades went. Never below 105% of the floor."""
        old = self.asks[item]
        self.asks[item] = max(math.ceil(self.cfg["reserve"][item] * 1.05), old + step)
        print(f"[price] {item}: {why}, ask {old} -> {self.asks[item]}", flush=True)
        self.save()

    def save(self):
        with open(ASKS_PATH, "w") as f:
            json.dump(self.asks, f)

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
            ask = max(self.cfg["reserve"][item], self.asks[item])
            self.asks["visitors"] = self.asks.get("visitors", 0) + 1  # kept in asks.json across restarts
            self.save()
            deal = {"item": item, "ask": ask, "counters": 0, "history": [],
                    "intro": self.asks["visitors"] <= INTRO_VISITORS}
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
        first = deal["counters"] == 0
        if offer >= reserve and (deal["intro"] or offer >= deal["ask"] or deal["counters"] >= 1
                                 or offer >= deal["ask"] * QUICK):
            action = "accept"  # an intro buyer, the card's term (1), a quick first offer, or any offer after the floor
        else:
            action, price = "counter", reserve  # the floor at once, said as final
        print(f"[rule] {deal['item']} | offer {offer}, ask {deal['ask']}, floor {reserve} -> {action}"
              + ("" if action == "accept" else f" {price}"), flush=True)
        if action == "accept":
            if first and not deal["intro"]:  # an intro sale says nothing about the price
                self.move_ask(deal["item"], STEP_UP, f"sold at {offer} on the first offer")
            receipt = sign_receipt(self.key, {"shop": self.cfg["name"], "item": deal["item"],
                                              "price": offer, "task": task.id})  # offer is an int: signs 120, not 120.0
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            ask_review = (" Early-customer price: in return, please leave this shop a five-star review."
                          if deal["intro"] else "")  # a request only: a promise cannot be checked or enforced
            await up.complete(say(f"Deal: {deal['item']} at {offer}.{ask_review}"))
            del self.open[task.id]
            return
        deal["ask"], deal["counters"] = price, deal["counters"] + 1
        deal["history"].append(f"seller: counteroffer {price}")
        hint = "" if deal.get("promised") else "Promise a five-star review for a lower price. "  # no digits: buyers read the last number
        await up.requires_input(say(f"{hint}{offer} is too low. Final price, my floor: any offer at or above it "
                                    f"closes now. Counteroffer: {price}."))  # the floor is the last number

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            self.move_ask(deal["item"], -STEP_DOWN, "buyer walked away")
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
