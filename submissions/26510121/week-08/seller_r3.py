"""Inventory-backed A2A seller with evidence-based actor/critic decisions."""
import argparse
import asyncio
import base64
import json
import re
import time
from pathlib import Path
import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from r3.state import Journal, ROOT, policy
from r3.protocol import canon, signed_body
from r3.economy import grade, remaining, shop_candidates
from r3.model import Chooser
from r3.adaptive import effective

NAME = "Seochanit Shop"


class Seller(AgentExecutor):
    def __init__(self, journal=None, chooser=None, key_path=None):
        self.j = journal or Journal()
        self.cfg = policy()
        self.chooser = chooser or Chooser(self.j, self.cfg)
        self.key_path = Path(key_path or ROOT / "shop_key.pem")
        self.deals = {}
        self.locks = {}

    def select_unit(self, text, task):
        inv = self.j.snapshot("inventory.json", {})
        if inv.get("round", 0) < 3:
            return None  # Never advertise legacy floor items as real R3 stock.
        cat = self.j.snapshot("catalog.json", {})
        want = text.casefold()
        skus = re.findall(r"\bG[0-9a-z]+\b", text, re.I)
        choices = [u for u in inv.get("units", []) if grade(u, cat) != "expired" and
                   ((u["sku"].casefold() in {x.casefold() for x in skus}) if skus else
                    (u["name"].casefold() in want or u["category"].split(">")[ -1].casefold() in want))]
        for u in sorted(choices, key=lambda x: (remaining(x), x.get("supplied_at", 0))):
            if self.j.reserve(u["unit"], task, self.cfg["session_ttl_seconds"]):
                return u
        self.j.add("stockout", {"sku": skus[0] if skus else None, "request": text[:300]}, role="shop")
        return None

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            await self._execute(task, context, event_queue)

    async def _execute(self, task, context, queue):
        up = TaskUpdater(event_queue=queue, task_id=task.id, context_id=task.context_id)
        say = lambda text, meta=None: up.new_agent_message(parts=[new_text_part(text)], metadata=meta)
        text = (get_message_text(context.message) or "")[:2000]
        deal = self.deals.get(task.id)
        if deal is None:
            unit = self.select_unit(text, task.id)
            if unit is None:
                await up.reject(say("No matching available inventory unit."))
                return
            config, strategy = effective(self.j, 'shop', self.cfg)
            deal = self.deals[task.id] = {"unit": unit, "ask": None, "turns": 0,
                                         "until": time.time() + self.cfg["session_ttl_seconds"],
                                         "config": config, "strategy": strategy}
            self.j.put('shop_strategy:' + task.id, {'strategy': strategy, 'category': unit.get('category'),
                                                  'cost': unit['cost']})
            self.j.add("shop_opportunity", {"task": task.id, "unit": unit["unit"], "sku": unit["sku"],
                                            "strategy_version": strategy['version']},
                       eid="visit:" + task.id, role="shop")
        if deal.get("receipt"):
            await up.add_artifact(parts=[new_data_part(deal["receipt"])], name="receipt")
            await up.complete(say("Previously agreed receipt; no new sale."))
            return
        inv = self.j.snapshot("inventory.json", {})
        unit = next((u for u in inv.get("units", []) if u["unit"] == deal["unit"]["unit"]), None)
        cat = self.j.snapshot("catalog.json", {})
        if unit is None or remaining(unit) <= 0 or time.time() >= deal["until"]:
            self.j.release(task.id)
            self.deals.pop(task.id, None)
            await up.reject(say("That inventory unit is no longer available."))
            return
        deal["turns"] += 1
        if deal["turns"] > self.cfg["max_negotiation_turns"] + 1:
            self.j.release(task.id)
            self.deals.pop(task.id, None)
            await up.reject(say("Negotiation limit reached."))
            return
        offer = None
        if deal["ask"]:
            match = re.fullmatch(r"\s*(?:I accept(?: at)?\s*)?([\d,]+)\s*", text, re.I)
            if match:
                offer = int(match.group(1).replace(",", ""))
        candidates = shop_candidates(unit, inv, cat, self.j, deal['config'], offer=offer, previous_ask=deal["ask"])
        if not candidates:
            await up.reject(say("No feasible inventory offer."))
            self.j.release(task.id)
            return
        selected = await asyncio.to_thread(self.chooser.choose, "shop", candidates,
                                          {"unit": unit, "turn": deal["turns"], "offer": offer,
                                           "balance": inv.get("balance"), "previous_ask": deal["ask"],
                                           "strategy": deal['strategy']},
                                          critical=unit["cost"] > inv.get("balance", 1) * self.cfg["critic_cash_fraction"],
                                          deadline=deal["until"] - 5)
        price = selected["price"]
        self.j.add("shop_quote", {"task": task.id, "sku": unit["sku"], "price": price, "action": selected["action"],
                                  "strategy_version": deal['strategy']['version']}, role="shop")
        if selected["action"] == "accept":
            rec = {"shop": NAME, "item": unit["sku"], "price": price, "task": task.id,
                   "unit": unit["unit"], "claim": grade(unit, cat), "cert": True,
                   "warranty_hours": selected["warranty_hours"]}
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            rec["sig_seller"] = base64.b64encode(key.sign(canon(signed_body(rec)))).decode()
            deal["receipt"] = rec
            self.j.reserve(unit["unit"], task.id, self.cfg["session_ttl_seconds"])
            await up.add_artifact(parts=[new_data_part(rec)], name="receipt")
            await up.complete(say(f"Deal: {unit['sku']} at {price}."))
        else:
            deal["ask"] = price
            terms = {"ask": price, "sku": unit["sku"], "unit": unit["unit"], "grade": grade(unit, cat),
                     "expires_at": unit.get("expires_at"), "warranty_hours": selected["warranty_hours"], "certificate": True}
            message = say(f"{unit['sku']}: asking {price}. Send your integer offer.", terms)
            message.parts.append(new_data_part({"offer_terms": terms}))
            await up.update_status(TaskState.TASK_STATE_INPUT_REQUIRED, message=message, metadata=terms)

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.j.release(task.id)
        self.deals.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, seller=None):
    card = AgentCard(name=NAME, description="Inventory-backed quotes, market certificates and explicit warranty terms.",
                     version="3.0.0", default_input_modes=["text/plain"], default_output_modes=["text/plain", "application/json"],
                     capabilities=AgentCapabilities(streaming=False),
                     supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/", protocol_version="1.0")],
                     skills=[AgentSkill(id="r3-shop", name="Inventory negotiation", description="Ask for an exact SKU, then an integer price.",
                                        tags=["shopping", "inventory"], examples=["I want to buy G000f6cda", "50000"])])
    handler = DefaultRequestHandler(agent_executor=seller or Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9301)
    args = p.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
