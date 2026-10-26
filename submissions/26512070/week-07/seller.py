"""The seller: an A2A v1.0 server (JSON-RPC binding) whose every decision comes from seller_brain.

    py seller.py --port 9999

This file only speaks A2A: it keeps one deal per task id, turns the buyer's text
into an offer, asks seller_brain.decide() what to do, and turns the answer into
a task state (INPUT_REQUIRED / COMPLETED with a receipt / REJECTED).
Every finished task also gets a "trade-record" artifact with the whole negotiation.

Keeping to the reserve is the brain's job (inventory.check_ask is its tool).
guard() is the last line of defense behind it: an ask or a sale below the
reserve, a first reply that is not a counter, a broken decision or a brain
crash becomes "hold the current price" (a counter at the last ask, or the list
price on the first reply). Every such case is logged as GUARD and kept in the
trade record's "guards". A sale also stops when the item is out of stock.
"""
import argparse
import asyncio
import base64
import json
import os
import re

import uvicorn
from cryptography.hazmat.primitives import serialization
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
REPLY_SECONDS = 150  # the kit buyer waits 200 s for each reply
# market_kit.py creates this key and registers its public half with the market; never commit it.
SHOP_KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop_key.pem")


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path=SHOP_KEY):
    """Add sig_seller: Ed25519 over canon({shop, item, price, task}), as on the week-07 page."""
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


def last_number(text):
    nums = re.findall(r"\d+", text or "")
    return int(nums[-1]) if nums else None


def guard(d, deal, inventory):
    """Last line of defense. Returns (decision to carry out, reason it was replaced or None)."""
    hold = deal["ask"] if deal["ask"] is not None else deal["list_price"]  # both are >= reserve
    safe = {"action": "counter", "price": hold, "say": "That does not work for us."}
    action = d.get("action") if isinstance(d, dict) else None
    if action == "brain_error":
        return safe, f"the brain failed: {d.get('error')}"
    if action == "none":
        return safe, "the brain gave no valid decision within its steps"
    if deal["ask"] is None and action != "counter":
        return safe, f"the first reply must be a counter, not {action}"
    if action == "counter":
        try:
            price = int(d["price"])
        except (KeyError, TypeError, ValueError):
            return safe, f"counter without a usable price: {d.get('price')!r}"
        ok, reason = inventory.check_ask(deal["item"], price)
        return ({**d, "price": price}, None) if ok else (safe, reason)
    if action == "accept":
        if deal["last_offer"] is None:
            return safe, "accept with no offer on the table"
        ok, reason = inventory.check_ask(deal["item"], deal["last_offer"])
        return (d, None) if ok else (safe, reason)
    if action == "reject":
        return d, None
    return safe, f"unknown decision {d!r}"


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
                # Logged and recorded: 113 buyers were turned away here unseen on 2026-10-26 (20:33-21:05).
                log(f"\n=== task {task.id[:8]} {item}: out of stock, turned the buyer away")
                trade_record.save(trade_record.build("seller", {
                    "item": item, "history": [{"from": "buyer", "text": text, "price": None},
                                              {"from": "seller", "text": f"Sorry, {item} is out of stock.",
                                               "price": None}], "guards": []}, "rejected"))
                await up.reject(say(f"Sorry, {item} is out of stock."))
                return
            deal = {"item": item, "reserve": info["reserve"], "list_price": info["list_price"],
                    "stock": info["stock"], "ask": None, "last_offer": None, "seconds_left": REPLY_SECONDS,
                    "history": [], "guards": []}
            self.open[task.id] = deal
        else:
            deal["last_offer"] = last_number(text)
            deal["stock"] = self.inventory.get(deal["item"])["stock"]
        deal["history"].append({"from": "buyer", "text": text, "price": deal["last_offer"]})

        log(f"\n=== task {task.id[:8]} {deal['item']}: buyer said {text!r}")
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(seller_brain.decide, deal, text, self.inventory),
                                         timeout=REPLY_SECONDS + 20)
        except Exception as e:  # crash or timeout: guard() turns this into "hold the price"
            raw = {"action": "brain_error", "error": repr(e)}
        d, why = guard(raw, deal, self.inventory)
        if why:
            log(f"GUARD: {why} (brain said {raw!r}) -> {d}")
            deal["guards"].append({"turn": len(deal["history"]), "reason": f"{why} (brain said {raw!r})", "used": d})
        log(f"decision: {d}")

        action, words = d.get("action"), (d.get("say") or "").strip()
        if action == "counter":
            price = int(d["price"])
            deal["ask"] = price
            deal["history"].append({"from": "seller", "text": words, "price": price})
            # The trade test reads the LAST number of this message as our ask.
            await up.requires_input(say(f"{words} My price: {price}.".strip()))
        elif action == "accept":
            price = deal["last_offer"]
            try:
                self.inventory.sell(deal["item"], price, buyer=task.context_id)
            except ValueError as e:
                await self.finish(up, task.id, deal, "rejected")
                await up.reject(say(f"Sorry, {e}."))
                return
            deal["history"].append({"from": "seller", "text": words or "accept", "price": price})
            # Sign the whole number: A2A data Parts carry 120 as 120.0, which would change the canon bytes.
            receipt = sign_receipt({"shop": NAME, "item": deal["item"], "price": int(price), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")  # receipt first
            await self.finish(up, task.id, deal, "completed", price)
            await up.complete(say(f"Deal: {deal['item']} at {price}."))
        else:  # reject (guard() lets nothing else through)
            deal["history"].append({"from": "seller", "text": words or "reject", "price": None})
            await self.finish(up, task.id, deal, "rejected")
            await up.reject(say(words or "No deal."))

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
