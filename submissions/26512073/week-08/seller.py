"""A2A transport adapted from lab/seller_agent.py; decisions use our harness."""
import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from cryptography.hazmat.primitives import serialization
import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from seller_strategy import (HISTORY_PATH, POLICY, decide_opening, decide_offer,
                             round3_inventory, available_units, load_round3_rules,
                             stock_opening_ask, decide_stock_offer)

RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
SHOP_NAME = "Afford Shop"
SHOP_DESCRIPTION = (
    "Food and goods from available stock. Ask for a SKU, then negotiate a price. "
    "Checkout includes the market's product certificate."
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
        self.held = {}  # unit id -> monotonic expiry; includes checkout handoff
        self.task_locks = {}
        self.history_path = (
            Path(history_path) if history_path is not None else HISTORY_PATH
        )

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
            "policy": deal.get("policy", POLICY), **details,
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
        if round3_inventory() or (deal and "unit" in deal):
            lock = self.task_locks.setdefault(task.id, asyncio.Lock())
            async with lock:
                await self.execute_stock(task.id, text, up, say)
            return
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

    async def execute_stock(self, task_id, text, up, say):
        if not load_round3_rules().get("enabled"):
            await up.reject(say("Round 3 trading is not enabled yet."))
            return
        now = time.monotonic()
        self.held = {unit: expiry for unit, expiry in self.held.items() if expiry > now}
        for old_task, old in list(self.open.items()):
            if "unit" in old and now - old["last_seen"] > 300:
                self.open.pop(old_task)
        deal = self.open.get(task_id)
        if deal is None:
            units = available_units(text, self.held)
            if not units:
                await up.reject(say("No matching unexpired unit is available in stock."))
                return
            # FIFO among matching available units, including both food and goods.
            # Missing supply times sort last rather than looking artificially old.
            unit = min(units, key=lambda u: (u.get("supplied_at", float("inf")),
                                             u["unit"]))
            ask = stock_opening_ask(unit)
            deal = {"item": unit["sku"], "unit": unit, "ask": ask,
                    "opening_ask": ask, "counters": 0, "last_seen": now,
                    "policy": "round3-cost5-floor3-fifo-halfway-v2"}
            self.open[task_id] = deal
            self.held[unit["unit"]] = now + 300
            self.record(task_id, "opened", deal, unit=unit["unit"])
            await up.requires_input(say(f"{unit['sku']}: asking {ask}"))
            return
        unit = deal["unit"]
        if not any(u["unit"] == unit["unit"] for u in available_units(unit["sku"])):
            self.open.pop(task_id, None)
            self.held.pop(unit["unit"], None)
            await up.reject(say("The selected unit is no longer available."))
            return
        deal["last_seen"] = now
        self.held[unit["unit"]] = now + 300
        if not re.fullmatch(r"\s*\d+\s*", text):
            await up.requires_input(say(f"Send a whole-number offer. Asking {deal['ask']}"))
            return
        offer = int(text)
        self.record(task_id, "offer_received", deal, offer=offer, unit=unit["unit"])
        decision = await asyncio.to_thread(decide_stock_offer, unit, offer, deal["counters"])
        if unit.get("kind") == "food" and unit.get("expires_at") is not None \
                and unit["expires_at"] <= time.time():
            self.record(task_id, "rejected", deal, unit=unit["unit"],
                        reason="Unit expired while the model was deciding.")
            self.open.pop(task_id, None)
            self.held.pop(unit["unit"], None)
            await up.reject(say("This food unit has expired; no sale made."))
            return
        if decision["action"] == "accept":
            receipt = sign_receipt({"shop": SHOP_NAME, "item": unit["sku"],
                                    "unit": unit["unit"], "cert": True,
                                    "price": offer, "task": task_id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {unit['sku']} at {offer}. Checkout confirms availability."))
            self.record(task_id, "agreement", deal, price=offer, unit=unit["unit"],
                        reason=decision.get("reason"))
            self.open.pop(task_id, None)
            # Retain the hold while the buyer completes checkout. A receipt is
            # not proof of payment; the kit updates inventory after settlement.
        else:
            deal["counters"] += 1
            deal["ask"] = decision["price"]
            self.record(task_id, "countered", deal, offer=offer, unit=unit["unit"],
                        reason=decision.get("reason"))
            await up.requires_input(say(f"Counteroffer: {decision['price']}"))

    async def cancel(self, context, event_queue):
        task = context.current_task
        lock = self.task_locks.get(task.id)
        if lock is not None:
            async with lock:
                await self.cancel_task(task, event_queue)
        else:
            await self.cancel_task(task, event_queue)

    async def cancel_task(self, task, event_queue):
        deal = self.open.get(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id,
                          context_id=task.context_id).cancel()
        if deal is not None:
            self.record(task.id, "canceled", deal)
            self.open.pop(task.id, None)
            if "unit" in deal:
                self.held.pop(deal["unit"]["unit"], None)


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
