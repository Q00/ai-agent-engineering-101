"""A2A seller: immediate floor-price quotes and signed receipts.

    python seller.py --port 9301
"""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from google.protobuf.json_format import MessageToDict

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from loyalty_pricing import BASE_PRICES, RESERVE, customer_id_from, purchase_quote
from seller_memory import SellerMemory

OPENING = {item: purchase_quote(item, 0)["price"] for item in RESERVE}


def buyer_offer(text, asking_price=None):
    """Read an explicit offer or acceptance, never a budget mentioned in prose."""
    text = text.strip()
    if asking_price is not None and re.fullmatch(
        r"(?:i\s+accept(?:\s+(?:your|the)\s+(?:offer|price))?|accept|deal|"
        r"agreed|yes|ok(?:ay)?|i(?:'ll|\s+will)\s+take\s+it)[.!\s]*", text, re.I
    ):
        return asking_price
    amount = r"\$?(\d+)(?:\.0+)?"
    match = re.fullmatch(amount + r"[.!\s]*", text)
    if match is None:
        match = re.fullmatch(
            r"(?:i\s+(?:(?:can|will|would\s+like\s+to)\s+)?)?"
            r"(?:offer|bid|pay)\s+" + amount + r"(?:\s+for\s+(?:a\s+|the\s+)?(?:lamp|chair|bookshelf))?[.!\s]*",
            text, re.I,
        )
    if match is None:
        match = re.fullmatch(
            r"(?:i\s+(?:want\s+to|will|can|would\s+like\s+to)\s+)?buy\s+"
            r"(?:a\s+|the\s+)?(?:lamp|chair|bookshelf)\s+(?:for|at)\s+" + amount + r"[.!\s]*",
            text, re.I,
        )
    return int(match.group(1)) if match else None


def canon(data):
    """LAB 2 step 1: sorted, compact UTF-8 JSON bytes for signing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt, key_path=None):
    """LAB 2: sign integer-valued canonical JSON with the kit's shop key."""
    key_path = Path(key_path) if key_path else Path(__file__).with_name("shop_key.pem")
    with key_path.open("rb") as source:
        key = serialization.load_pem_private_key(source.read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return {**receipt, "sig_seller": base64.b64encode(key.sign(canon(body))).decode("ascii")}


class Seller(AgentExecutor):
    def __init__(self, memory_path=None):
        self.open = {}  # task id -> {"item", "ask", "offer", "lines"}
        path = Path(memory_path) if memory_path is not None else Path(os.environ.get(
            "SELLER_MEMORY", str(Path(__file__).with_name("seller-memory.jsonl"))))
        try:
            self.memory = SellerMemory(path)
            print(f"[seller-memory] loaded {len(self.memory.latest)} tasks from {path}", flush=True)
        except (OSError, ValueError) as error:
            self.memory = None
            print(f"[seller-memory-error] {error}; existing file retained; trading continues", flush=True)

    def remember(self, task_id, deal, outcome, sale_price=None):
        if self.memory is None:
            return
        row = {
            "task_id": task_id, "item": deal["item"], "outcome": outcome,
            "started_at": deal["started_at"],
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "opening_ask": deal["quote"]["price"], "last_ask": deal["ask"],
            "customer_id": deal["customer_id"], "pricing": deal["quote"],
            "last_offer": deal["offer"], "sale_price": sale_price,
            "buyer_messages": deal["buyer_messages"], "seller_quotes": deal["seller_quotes"],
            "elapsed_seconds": round(time.monotonic() - deal["started"], 3),
        }
        try:
            self.memory.record(row)
            print(f"[seller-memory] {task_id} {deal['item']} -> {outcome}", flush=True)
        except (OSError, ValueError) as error:
            print(f"[seller-memory-error] {error}; trading continues", flush=True)

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
            message_metadata = MessageToDict(context.message.metadata)
            customer_id = customer_id_from(message_metadata) or customer_id_from(context.metadata)
            # A self-reported ID is never treated as authenticated identity.
            # If history is unavailable, use the welcome offer without claiming
            # to know this customer's purchase count.
            completed = self.memory.completed_purchases(customer_id) if self.memory and customer_id else 0
            quote = purchase_quote(item, completed)
            deal = self.open[task.id] = {
                "item": item, "ask": None, "offer": None, "lines": [f"buyer: {text}"],
                "customer_id": customer_id, "quote": quote,
                "started": time.monotonic(),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "buyer_messages": 1, "seller_quotes": 0,
            }
        else:
            deal["buyer_messages"] += 1
            deal["lines"].append(f"buyer: {text}")
        deal["offer"] = buyer_offer(text, deal["ask"])

        reserve = RESERVE[deal["item"]]
        opening = deal["quote"]["price"]
        # Each task keeps its quoted tier, even if another purchase completes.
        # Confirm the discounted amount rather than charging a higher offered
        # base price. The receipt then matches the buyer's final agreed price.
        action = "accept" if deal["offer"] == opening and opening >= reserve else "counter"
        if action == "accept":
            price = deal["offer"]
            receipt = sign_receipt({"shop": "psyoung82", "item": deal["item"],
                                    "price": int(price), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(
                f"Purchased {deal['item']} at {price}. Ed25519-signed receipt attached. "
                "Please verify the receipt and leave an honest review of your experience."
            ))
            self.remember(task.id, deal, "deal", sale_price=int(price))
            del self.open[task.id]
            return
        deal["ask"] = opening
        quote = deal["quote"]
        tier = (f"Purchase {quote['purchase_number']}: up to {quote['discount_percent']}% off"
                if quote["discount_percent"] else "Standard price after the welcome and return offers")
        reply = (f"{deal['item']}: asking {opening}. {tier}; base price {quote['base_price']}. "
                 "Rounded up to whole units; course minimum applies. "
                 "A consistent customer_id in A2A metadata enables purchase-count tracking. "
                 "Signed receipt after purchase. Reply 'I accept' or send the purchase price: "
                 f"{opening}")
        deal["lines"].append(f"you: {reply}")
        deal["seller_quotes"] += 1
        self.remember(task.id, deal, "open")
        # Some buyers use the last number in text; others read metadata.ask.
        # Both must see the current quote, rather than a signature algorithm ID.
        await up.requires_input(up.new_agent_message(
            parts=[new_text_part(reply)],
            metadata={"ask": deal["ask"], "item": deal["item"],
                      "accept_message": str(deal["ask"]),
                      "signed_receipt_on_purchase": True,
                      "loyalty": {**deal["quote"],
                                  "customer_id_provided": deal["customer_id"] is not None,
                                  "identity_authenticated": False}},
        ))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
        if deal is not None:
            self.remember(task.id, deal, "canceled")


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name="psyoung82 Furniture | Welcome & Return Offers",
        description=(f"First-purchase offers: lamp {OPENING['lamp']}, "
                     f"chair {OPENING['chair']}, bookshelf {OPENING['bookshelf']}. "
                     "Up to 10% off the first completed purchase, up to 5% off the second; "
                     f"base prices lamp {BASE_PRICES['lamp']}, chair {BASE_PRICES['chair']}, "
                     f"bookshelf {BASE_PRICES['bookshelf']}. Whole-unit rounding and minimum prices apply. "
                     "Send the same customer_id in A2A metadata for repeat-purchase tracking; "
                     "anonymous buyers receive the welcome quote. Quotes include metadata.ask; signed receipts."),
        version="1.8.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Send an item and explicit offer to buy directly, or ask for a quote and accept it with a number or 'I accept'. A completed purchase includes a signed receipt.",
                           tags=["shopping", "negotiation"],
                           examples=[f"I want to buy a {item} for {OPENING[item]}" for item in OPENING]
                           + ["I want to buy a lamp", "I accept"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
