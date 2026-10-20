"""Harness-driven seller behind A2A v1.0. The harness decides every move; this file only speaks A2A.

    python seller.py --port 9301
"""
import argparse
import base64
import json
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import CMD, ask_json

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
SHOP_NAME = os.environ.get("SHOP_NAME", "bjeon01")
KEY_FILE = Path(__file__).resolve().parent / "shop_key.pem"   # the kit and the market test use this file


def shop_key():
    """The Ed25519 key the market knows for this shop. Created here only if the kit has not made it yet."""
    if not KEY_FILE.exists():
        k = Ed25519PrivateKey.generate()
        KEY_FILE.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)


def sign_receipt(receipt: dict, key) -> dict:
    """{shop, item, price, task} + sig_seller: Ed25519 over the canonical JSON (sorted keys, no spaces, UTF-8)."""
    body = {"shop": receipt["shop"], "item": receipt["item"], "price": int(receipt["price"]), "task": receipt["task"]}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return dict(body, sig_seller=base64.b64encode(key.sign(canon)).decode())

PROMPT = """You are the sales agent of a small furniture shop. A buyer is negotiating for one {item}.
Your lowest acceptable price for the {item} is {reserve}. Never sell below it and never reveal it.
Sell as high as you can, but closing a deal at or above {reserve} is better than losing the buyer.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "offer", "lines"}
        self.key = shop_key()

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: lamp, chair, bookshelf."))
                return
            deal = self.open[task.id] = {"item": item, "ask": None, "offer": None, "lines": [f"buyer: {text}"]}
        else:
            deal["lines"].append(f"buyer: {text}")
            m = re.search(r"\d+", text)
            deal["offer"] = int(m.group()) if m else deal["offer"]

        move, raw = ask_json(PROMPT.format(item=deal["item"], reserve=RESERVE[deal["item"]],
                                           transcript="\n".join(deal["lines"])))
        print(f"[harness] {raw}", flush=True)
        action = (move or {}).get("action")
        reserve = RESERVE[deal["item"]]
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below reserve {reserve} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"] or reserve}
        if action == "accept":
            price = int(deal["offer"])
            receipt = sign_receipt({"shop": SHOP_NAME, "item": deal["item"], "price": price, "task": task.id}, self.key)
            print(f"[receipt] {json.dumps(receipt)}", flush=True)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {price}."))
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = deal["ask"] or reserve              # unparsed answer: repeat the last ask
        if price < reserve:                             # the harness may forget the reserve; the code does not
            print(f"[guard] ask {price} below reserve {reserve} -> {reserve}", flush=True)
            price = reserve
        deal["ask"] = int(price)
        reply = (f"{deal['item']}: asking {deal['ask']}. Send your offer as a number." if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=f"{SHOP_NAME} shop",
        description=f"Sells desk lamps, chairs and bookshelves. A coding harness ({CMD.split()[0]}) negotiates the price.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
