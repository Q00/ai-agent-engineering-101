"""A2A transport adapted from lab/seller_agent.py; decisions use our harness."""
import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import serialization
import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from seller_decision import decide_opening, decide_offer


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
SHOP_NAME = "Afford Shop"
SHOP_DESCRIPTION = (
    "Lamps, chairs, and bookshelves at affordable prices. "
    "Clear offers and fair negotiation and will never scam my customers."
)


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt):
    # Use the same persistent identity that the market kit registered.
    key_path = Path(__file__).resolve().parent / "shop_key.pem"
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    body = {field: receipt[field] for field in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode("ascii")
    return receipt


class Seller(AgentExecutor):
    def __init__(self, history_path=None):
        self.open = {}
        self.history_path = (Path(history_path) if history_path is not None else
                             Path(__file__).resolve().parent / "logs" / "seller-history.jsonl")

    def record(self, task_id, event, deal, **details):
        """Local seller observations, not proof of market-verified trades.

        Each line is appended; an opening without an ending remains unfinished.
        Calls run on the seller's event loop, with no await during the write.
        """
        row = {
            "time": datetime.now(timezone.utc).isoformat(),
            "source": "local_seller", "shop": SHOP_NAME, "task": task_id,
            "event": event, "item": deal["item"],
            "opening_ask": deal["opening_ask"], "ask": deal["ask"],
            "policy": "opening-30-percent-v1", **details,
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        def say(text):
            return up.new_agent_message(parts=[new_text_part(text)])

        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        if deal is None:
            item = next((item for item in RESERVES
                         if re.search(rf"\b{item}\b", text.lower())), None)
            if item is None:
                await up.reject(say("We sell lamp, chair, and bookshelf."))
                return
            decision = await asyncio.to_thread(decide_opening, item, RESERVES[item])
            self.open[task.id] = {"item": item, "ask": decision["price"],
                                  "opening_ask": decision["price"],
                                  "countered_at_reserve": False}
            await up.requires_input(say(f"{item}: asking {decision['price']}"))
            self.record(task.id, "opened", self.open[task.id], reason=decision.get("reason"))
            return

        if not re.fullmatch(r"\s*\d+\s*", text):
            await up.requires_input(say(f"Send a whole-number offer. Asking {deal['ask']}"))
            return
        offer = int(text)
        item = deal["item"]
        self.record(task.id, "offer_received", deal, offer=offer)
        decision = await asyncio.to_thread(
            decide_offer, item, RESERVES[item], offer, deal["countered_at_reserve"])
        action, price = decision["action"], decision["price"]
        if action == "accept":
            receipt = sign_receipt({"shop": SHOP_NAME, "item": item,
                                    "price": int(price), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {price}"))
            self.record(task.id, "deal", deal, offer=offer, price=int(price),
                        reason=decision.get("reason"))
            self.open.pop(task.id)
        elif action == "reject":
            await up.reject(say("No deal."))
            self.record(task.id, "rejected", deal, offer=offer,
                        reason=decision.get("reason"))
            self.open.pop(task.id)
        else:
            deal["ask"] = price
            deal["countered_at_reserve"] |= price == RESERVES[item]
            await up.requires_input(say(f"Counteroffer: {price}"))
            self.record(task.id, "countered", deal, offer=offer,
                        reason=decision.get("reason"))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.get(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id,
                          context_id=task.context_id).cancel()
        if deal is not None:
            self.record(task.id, "canceled", deal)
            self.open.pop(task.id, None)


def build(port, history_path=None):
    card = AgentCard(
        name=SHOP_NAME, description=SHOP_DESCRIPTION,
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC",
            protocol_version="1.0", url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
            description="Name an item, then send integer price offers on the same task.",
            tags=["shopping", "negotiation"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(history_path),
                                   task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--history", type=Path,
                        help="Seller event JSONL path; use a separate path for local tests.")
    args = parser.parse_args()
    uvicorn.run(build(args.port, args.history), host="127.0.0.1", port=args.port)
