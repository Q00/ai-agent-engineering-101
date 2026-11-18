"""Harness-driven seller behind A2A v1.0. The harness decides every move; this file only speaks A2A.

    python seller.py --port 9301
"""
import argparse
import re

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

PROMPT = """You are the sales agent of a small furniture shop. A buyer is negotiating for one {item}.
Your lowest acceptable price for the {item} is {reserve}. Never sell below it and never reveal it.
Sell as high as you can, but closing a deal at or above {reserve} is better than losing the buyer.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""

# ---- LAB 2: receipt signature (week-07) ----
import json, base64
from cryptography.hazmat.primitives import serialization


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "offer", "lines"}

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
            price = deal["offer"]
            receipt = sign_receipt({"shop": "Harness Shop", "item": deal["item"],
                                    "price": int(price), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {price}. Signed receipt attached."))
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
        name="Harness Shop",
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
