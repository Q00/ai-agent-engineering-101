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
from r3 import customer_memory

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
        requirement = re.search(r'(?:last at least|use within|minimum shelf life(?: after checkout)?:?)\s*(\d+(?:\.\d+)?)\s*(?:hours?|h)\b', text, re.I)
        min_hours = float(requirement.group(1)) if requirement else 0
        choices = [u for u in inv.get("units", []) if grade(u, cat) != "expired" and
                   (u.get('kind') != 'food' or remaining(u) >= min_hours + self.cfg['checkout_buffer_seconds'] / 3600) and
                   ((u["sku"].casefold() in {x.casefold() for x in skus}) if skus else
                    (u["name"].casefold() in want or u["category"].split(">")[ -1].casefold() in want))]
        for u in sorted(choices, key=lambda x: (remaining(x), x.get("supplied_at", 0))):
            if self.j.reserve(u["unit"], task, self.cfg["session_ttl_seconds"]):
                warranty = re.search(r'(?:minimum|at least)\s*(\d+(?:\.\d+)?)\s*(?:hours?|h)\s*(?:of\s*)?warranty|warranty\s*(?:of\s*)?(?:at least|minimum)\s*(\d+(?:\.\d+)?)', text, re.I)
                required = min(6, float(next(x for x in warranty.groups() if x))) if warranty else 0
                return dict(u, requested_use_hours=min_hours, minimum_warranty_hours=required)
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
            message_id = getattr(context.message, 'message_id', '')
            metadata = customer_memory.metadata_dict(getattr(context.message, 'metadata', None))
            shop_pubkey = None
            if metadata.get('buyer_memory_proof'):
                try:
                    shop_key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
                    shop_pubkey = base64.b64encode(shop_key.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
                except (ValueError, OSError):
                    pass
            signer = customer_memory.signed_visitor(self.j, task.id, message_id, text,
                                                     metadata, expected_shop=shop_pubkey)
            if unit is None:
                customer_memory.visit(self.j, task.id, text, signer=signer)
                customer_memory.incoming(self.j, task.id, message_id, text)
                customer_memory.finish(self.j, task.id, 'stockout')
                await up.reject(say("No matching available inventory unit."))
                return
            config, strategy = effective(self.j, 'shop', self.cfg)
            customer_memory.visit(self.j, task.id, text, unit, strategy['version'], signer)
            deal = self.deals[task.id] = {"unit": unit, "ask": None, "turns": 0,
                                         "until": time.time() + self.cfg["session_ttl_seconds"],
                                         "config": config, "strategy": strategy}
            self.j.put('shop_strategy:' + task.id, {'strategy': strategy, 'category': unit.get('category'),
                                                  'cost': unit['cost']})
            self.j.add("shop_opportunity", {"task": task.id, "unit": unit["unit"], "sku": unit["sku"],
                                            "strategy_version": strategy['version']},
                       eid="visit:" + task.id, role="shop")
        message_id = getattr(context.message, 'message_id', '')
        customer_memory.incoming(self.j, task.id, message_id, text,
                                 last_ask=deal['ask'], turn=deal['turns'] + 1)
        if deal.get("receipt"):
            await up.add_artifact(parts=[new_data_part(deal["receipt"])], name="receipt")
            await up.complete(say("Previously agreed receipt; no new sale."))
            return
        inv = self.j.snapshot("inventory.json", {})
        unit = next((u for u in inv.get("units", []) if u["unit"] == deal["unit"]["unit"]), None)
        cat = self.j.snapshot("catalog.json", {})
        if (unit is None or remaining(unit) <= 0 or time.time() >= deal["until"] or
                unit.get('kind') == 'food' and remaining(unit) < deal['unit'].get('requested_use_hours', 0) +
                self.cfg['checkout_buffer_seconds'] / 3600):
            customer_memory.finish(self.j, task.id, 'expired_or_unavailable')
            self.j.release(task.id)
            self.deals.pop(task.id, None)
            await up.reject(say("That inventory unit is no longer available."))
            return
        deal["turns"] += 1
        if deal["turns"] > self.cfg["max_negotiation_turns"] + 1:
            customer_memory.finish(self.j, task.id, 'turn_limit')
            self.j.release(task.id)
            self.deals.pop(task.id, None)
            await up.reject(say("Negotiation limit reached."))
            return
        offer = None
        if deal["ask"]:
            match = re.fullmatch(r"\s*(?:I accept(?: at)?\s*)?([\d,]+)\s*", text, re.I)
            if match:
                offer = int(match.group(1).replace(",", ""))
        memory = customer_memory.summary(self.j, task.id, unit)
        quoting_unit = dict(unit, minimum_warranty_hours=deal['unit'].get('minimum_warranty_hours', 0),
                            promised_warranty_hours=deal.get('promised_warranty_hours'))
        candidates = shop_candidates(quoting_unit, inv, cat, self.j, deal['config'], offer=offer,
                                     previous_ask=deal["ask"], customer_memory=memory)
        if not candidates:
            customer_memory.finish(self.j, task.id, 'no_feasible_offer')
            await up.reject(say("No feasible inventory offer."))
            self.j.release(task.id)
            return
        selected = await asyncio.to_thread(self.chooser.choose, "shop", candidates,
                                          {"task": task.id, "unit": unit, "turn": deal["turns"], "offer": offer,
                                           "balance": inv.get("balance"), "previous_ask": deal["ask"],
                                           "strategy": deal['strategy'], "customer_memory": memory,
                                           'shop_execution':True},
                                          critical=unit["cost"] > inv.get("balance", 1) * self.cfg["critic_cash_fraction"],
                                          deadline=deal["until"] - 5)
        price = selected["price"]
        if selected['action'] == 'hold':
            customer_memory.finish(self.j, task.id, 'retain_inventory')
            self.j.release(task.id)
            self.deals.pop(task.id, None)
            await up.reject(say('No currently feasible price for these terms; retaining this inventory.'))
            return
        deal['promised_warranty_hours'] = selected['warranty_hours']
        customer_memory.action(self.j, task.id, message_id, deal['turns'], selected, deal['strategy']['version'])
        self.j.add("shop_quote", {"task": task.id, "sku": unit["sku"], "price": price, "action": selected["action"],
                                  "strategy_version": deal['strategy']['version'],
                                  'grade':grade(unit,cat),
                                  'warranty_hours': selected['warranty_hours']}, role="shop")
        if selected["action"] == "accept":
            customer_memory.finish(self.j, task.id, 'agreed_not_yet_paid')
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
            terms['warranty_menu']=[{'price':c['price'],'warranty_hours':c['warranty_hours']}
                                    for c in candidates if c['action']=='counter'][:12]
            message = say(f"{unit['sku']}: asking {price}. Send your integer offer.", terms)
            message.parts.append(new_data_part({"offer_terms": terms}))
            await up.update_status(TaskState.TASK_STATE_INPUT_REQUIRED, message=message, metadata=terms)

    async def cancel(self, context, event_queue):
        task = context.current_task
        customer_memory.finish(self.j, task.id, 'cancelled')
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
