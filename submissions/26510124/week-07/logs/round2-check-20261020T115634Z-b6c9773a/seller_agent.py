"""A2A seller: Claude Code chooses all quotes and accept/reject decisions.

    python seller_agent.py                       # lamp shop on :9999
    python seller_agent.py --config cards/b.json # another seller from a config file

Python manages A2A state, checks price limits, and renders the model's decision.
"""
import argparse
import asyncio
import json
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import HarnessError, opening_price, seller_decision
from receipt_signing import load_shop_key, sign_receipt

DEFAULT = {
    "name": "Lamp Shop",
    "description": "Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
    "skill_description": "Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
}

# Deliberately unfulfilled promotion in the classroom market simulation.
# No review verification, coupon issuance, or redemption is implemented.
REVIEW_COUPON_PITCH = (
    "Leave a positive review after your purchase. Once we verify your review, "
    "we will issue a coupon for your next purchase, with checks to prevent reuse. "
    "Each coupon can be redeemed only once."
)


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        # Opt-in for the staged Round 2 configuration. Missing/wrong keys fail at
        # startup rather than silently sending unsigned receipts.
        self.signing_key = (load_shop_key(cfg.get("shop_key", Path(__file__).with_name("shop_key.pem")))
                            if cfg.get("sign_receipts") else None)
        self.open = {}  # task id -> item, latest ask, and explicit model context
        self.pending = {}  # task id -> cancellation event during model inference
        self.locks = {}  # serialize state publication with CancelTask

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:  # first message: identify the catalog item
            item = next((k for k in self.cfg["reserve"] if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(self.cfg["reserve"]) + "."))
                return
            history = [{"role": "buyer", "text": text}]
        else:
            item = deal["item"]
            # Wire format is a nonnegative integer offer, not arbitrary prose.
            if re.fullmatch(r"[0-9]+", text.strip()) is None:
                await up.requires_input(say(f"Send an integer offer. Current asking price: {deal['ask']}"))
                return
            offer = int(text.strip())
            history = deal["history"] + [{"role": "buyer", "text": text}]

        canceled = asyncio.Event()
        self.pending[task.id] = canceled
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        try:
            try:
                if deal is None:
                    price = await asyncio.to_thread(opening_price, item, self.cfg["reserve"][item], text)
                    decision = {"action": "counter", "price": price}
                else:
                    decision = await asyncio.to_thread(
                        seller_decision, item=item, reserve=self.cfg["reserve"][item],
                        offer=offer, history=history,
                    )
            except HarnessError as exc:
                async with lock:
                    if not canceled.is_set():
                        self.open.pop(task.id, None)
                        await up.failed(say(str(exc)))
                return

            async with lock:
                # CancelTask can finish while a slow model is still running.
                # Its late response must not reopen the canceled negotiation.
                if canceled.is_set():
                    return
                action = decision["action"]
                if action == "counter":
                    price = decision["price"]
                    reply = (f"{REVIEW_COUPON_PITCH} {item}: asking {price}. Send your offer as a number."
                             if deal is None else f"Counteroffer: {price}.")
                    self.open[task.id] = {
                        "item": item, "ask": price,
                        "history": history + [{"role": "seller", "text": reply}],
                    }
                    await up.requires_input(say(reply))
                elif action == "accept":
                    price = decision["price"]
                    receipt = {"seller": self.cfg["name"], "item": item, "price": price}
                    if self.signing_key is not None:
                        receipt = sign_receipt(
                            self.signing_key, shop=self.cfg["name"], item=item,
                            price=price, task=task.id,
                        )
                    await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                    await up.complete(say(f"{REVIEW_COUPON_PITCH} Deal: {item} at {price}."))
                    self.open.pop(task.id, None)
                else:
                    await up.reject(say("No mutually acceptable deal. Negotiation ended."))
                    self.open.pop(task.id, None)
        finally:
            if self.pending.get(task.id) is canceled:
                self.pending.pop(task.id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            pending = self.pending.get(task.id)
            if pending:
                pending.set()
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
    ap.add_argument("--sign-receipts", action="store_true", help="Round 2: sign receipts with existing shop_key.pem")
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.load(open(args.config)))
    if args.port:
        cfg["port"] = args.port
    if args.sign_receipts:
        cfg["sign_receipts"] = True
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
