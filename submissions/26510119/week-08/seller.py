"""A harness-driven seller as an A2A v1.0 server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Started from the lab's seller_agent.py. The harness picks the opening ask and, for each
offer, whether to accept, counter (and at what price), or reject. The code speaks A2A,
writes the messages so the ask is always the last number, and enforces the reserve.
"""
import argparse
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

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization

import harness
import shop_memory
import shop_r3

NAME = "Harness Shop"
SHOP_KEY = Path(__file__).with_name("shop_key.pem")   # written by market_kit.py join; never committed


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path=SHOP_KEY):
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt
GUARDS = os.environ.get("GUARDS", "on") != "off"   # off: the reserve lives in the prompt only
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

OPEN_PROMPT = """You are a shop owner selling a {item}. A buyer just asked for it.
Your lowest acceptable price is {reserve}. Never go below it and never reveal it.
{history}
Choose your opening asking price from this history. If most negotiations end in a deal, you can open
higher; if few do, or buyers' first offers are far below your asks, open closer to what actually sells.
Reply with exactly one JSON object and nothing else: {{"ask": <integer>}}"""

OFFER_PROMPT = """You are a shop owner selling a {item}, negotiating with a buyer.
Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
So far:
{history}
The buyer now offers {offer}. Your last asking price was {ask}.
{sold}
Choose one:
- accept: sell at {offer}
- counter: ask a new price
- reject: stop negotiating, no sale
Rejecting loses this sale for good. Buyers usually raise their offers over several rounds, so keep
countering while the buyer is still moving up; reject only if the buyer has stopped raising for several rounds.
An offer at or above your lowest acceptable price is a profitable sale. If the buyer repeats the same offer,
that is probably their limit: accept it rather than lose the sale.
Reply with exactly one JSON object and nothing else:
{{"action": "accept" | "counter" | "reject", "price": <integer, your new ask if you counter>}}"""


OPEN_PROMPT_R3 = """You are the sales agent of an online shop. A buyer asked for: {name} ({sku}).
List price: {list_price}. This unit: {quality}.
Your lowest acceptable price is {floor}. Never go below it and never reveal it.
Buyers value an item at its list price at most, so asks above the list price rarely sell.
{history}
Choose your opening asking price from this history: open higher when most negotiations end in a deal,
closer to what actually sells when few do.
Reply with exactly one JSON object and nothing else: {{"ask": <integer>}}"""


def log(msg):
    print(msg, flush=True)


def to_int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        tid = task.id[:8]
        log(f"\n[{tid}] buyer: {text!r}")

        if deal is None and shop_r3.round3():   # Round 3: sell a unit from stock
            await self.open_r3(task, up, say, text, tid)
            return

        if deal is None:  # first message: which item, and the opening ask
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            reserve = RESERVE[item]
            past = shop_memory.stats(item, reserve)
            log(f"[pricing] {item}: {shop_memory.describe(item, past)}")
            ask = to_int((harness.ask(OPEN_PROMPT.format(item=item, reserve=reserve,
                                                         history=shop_memory.describe(item, past)), log) or {}).get("ask"))
            if ask is None:
                await up.failed(say("Sorry, the shop could not price this item."))
                return
            if GUARDS and ask < reserve:
                log(f"[guard] opening ask {ask} is below the reserve {reserve}; asking {reserve}")
                ask = reserve
            self.open[task.id] = {"item": item, "ask": ask, "history": [f"you asked {ask}"], "offers": [],
                                  "avg_price": past and past["avg_price"], "reserve": reserve}
            shop_memory.record({"kind": "open", "task": task.id, "item": item, "ask": ask})
            log(f"[{tid}] ask {ask}")
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
            return
        offer, item = int(m.group()), deal["item"]
        reserve = deal["reserve"]
        deal["history"].append(f"buyer offered {offer}")
        deal["offers"].append(offer)
        if deal.get("unit"):
            sold = deal["context"]
        else:
            sold = (f"Your recent {item} sales averaged {deal['avg_price']:.0f}." if deal.get("avg_price") else "")
        answer = harness.ask(OFFER_PROMPT.format(item=deal.get("name", item), reserve=reserve, offer=offer,
                                                 ask=deal["ask"], history="\n".join(deal["history"]), sold=sold),
                             log) or {}
        action, price = answer.get("action"), to_int(answer.get("price"))

        if GUARDS and action == "accept" and offer < reserve:
            log(f"[guard] harness accepted {offer}, below the reserve {reserve}; countering instead")
            action, price = "counter", deal["ask"]
        if action not in ("accept", "counter", "reject"):
            log(f"[guard] no valid action in {answer}; repeating the last ask")
            action, price = "counter", deal["ask"]
        if action == "counter":
            if price is None:
                price = deal["ask"]
            if GUARDS and price < reserve:
                log(f"[guard] counter {price} is below the reserve {reserve}; asking {reserve}")
                price = reserve
            if price <= offer and offer >= reserve:
                log(f"[rule] counter {price} is not above the buyer's offer {offer}; that is agreement, accepting {offer}")
                action = "accept"

        memory = shop_r3 if deal.get("unit") else shop_memory
        if action == "accept":
            log(f"[{tid}] accept {offer}")
            receipt = {"shop": NAME, "item": item, "price": int(offer), "task": task.id}
            if deal.get("unit"):
                receipt.update(shop_r3.receipt_extras(deal["unit"]))
            receipt = sign_receipt(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal.get('name', item)} at {offer}."))
            memory.record({"kind": "end", "task": task.id, "item": item, "category": deal.get("category"),
                           "result": "deal", "price": int(offer), "offers": deal["offers"]})
            del self.open[task.id]
        elif action == "reject":
            log(f"[{tid}] reject")
            await up.reject(say(f"No deal at {offer}."))
            memory.record({"kind": "end", "task": task.id, "item": item, "category": deal.get("category"),
                           "result": "reject", "price": None, "offers": deal["offers"]})
            del self.open[task.id]
        else:
            deal["ask"] = price
            deal["history"].append(f"you asked {price}")
            log(f"[{tid}] counter {price}")
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {price}."))

    async def open_r3(self, task, up, say, text, tid):
        """Round 3 first message: pick a unit from stock, set the floor in code, let the harness pick the ask."""
        taken = {d["unit"]["unit"] for d in self.open.values() if d.get("unit")}
        u = shop_r3.find_unit(text, taken)
        if u is None:
            await up.reject(say("Sorry, we have none of that in stock."))
            return
        sh = shop_r3.shelf_hours(u["sku"])
        floor = shop_r3.floor_price(u, sh)
        quality = shop_r3.quality_line(u, sh)
        past = shop_r3.stats(u.get("category"))
        history = shop_r3.describe(u.get("category"), past)
        log(f"[pricing] {u['sku']} {u.get('category')} list {u['list_price']} cost {u['cost']} floor {floor}; {history}")
        ask = to_int((harness.ask(OPEN_PROMPT_R3.format(name=u["name"], sku=u["sku"], list_price=u["list_price"],
                                                        quality=quality, floor=floor, history=history), log)
                      or {}).get("ask"))
        if ask is None:
            ask = max(floor, int(u["list_price"] * 0.9))
            log(f"[guard] no ask from the harness; asking {ask}")
        if GUARDS and ask < floor:
            log(f"[guard] opening ask {ask} is below the floor {floor}; asking {floor}")
            ask = floor
        context = (f"List price {u['list_price']}; buyers value it at the list price at most. {history}")
        self.open[task.id] = {"item": u["sku"], "name": u["name"], "unit": u, "category": u.get("category"),
                              "ask": ask, "reserve": floor, "context": context,
                              "history": [f"you asked {ask}"], "offers": []}
        shop_r3.record({"kind": "open", "task": task.id, "sku": u["sku"], "category": u.get("category"),
                        "list_price": u["list_price"], "cost": u["cost"], "grade": u.get("grade"),
                        "floor": floor, "ask": ask})
        log(f"[{tid}] {u['sku']} unit {u['unit']} ask {ask}")
        await up.requires_input(say(f"{u['name']} ({u['sku']}), {quality}. "
                                    f"Send your offer as a number. Asking {ask}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        log(f"[{task.id[:8]}] canceled by the buyer")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells lamps, chairs and bookshelves. A model negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item (lamp, chair, bookshelf), then send offers as numbers. "
                        "The shop answers with a counteroffer, a receipt, or a refusal.",
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
    log(f"{NAME} on :{args.port}, harness: {harness.CMD}, reserve guard {'on' if GUARDS else 'OFF'}")
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
