"""Haiku inventory seller with pinned grade floors, guarantees and bounded advice."""
import argparse
import asyncio
import json
import os
import re
import time
from copy import deepcopy
from pathlib import Path

import uvicorn
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.tasks import TaskUpdater

from feedback import append_event
from harness import HarnessError, decide, parse_seller
from inventory import Inventory, StockError
from receipt_signing import sign_receipt
from sales_advisor import AdviceReader, config_at
from shop_policy import policy_at, quote_copy, selling_terms, shop_copy
from seller_agent import DEFAULT, Seller, bounded_history, build

ROOT = Path(__file__).resolve().parent


def inventory_decision(product, offer, history, last_ask, advice=None):
    terms = product.get("sales_terms") or selling_terms(product, policy_at())
    def validate(raw):
        choice = parse_seller(raw, terms["price_floor"], offer)
        if offer is None and choice["action"] != "counter":
            raise HarnessError("Opening must quote this unit")
        if choice["action"] == "counter" and last_ask is not None and choice["price"] > last_ask:
            raise HarnessError("Counteroffer increased the previous ask")
        return choice
    facts = {k: product.get(k) for k in
             ("sku", "name", "category", "kind", "cost", "list_price", "grade", "expires_at")}
    return decide("seller_inventory.txt", {
        "product": facts, "observed_at": time.time(), "price_floor": terms["price_floor"],
        "sales_terms": terms,
        "buyer_offer": offer, "last_ask": last_ask, "history": bounded_history(history),
        "sales_advisor": advice,
    }, validate, prefix="inventory-seller")


class InventorySeller(Seller):
    def __init__(self, cfg):
        super().__init__({**cfg, "adaptive": {"enabled": False}, "sign_receipts": True})
        self.inventory = Inventory(cfg.get("market_dir", os.environ.get("MARKET_DIR", ROOT / ".market")))
        self.events = ROOT / "logs" / "inventory-seller-events.jsonl"
        self.r3 = {}
        self.policy = cfg.get("shop_policy") or policy_at()
        self.advisor = AdviceReader(self.inventory.folder) if cfg.get("advisor_enabled") else None

    async def public_card(self, card):
        advice = None
        try:
            rnd = self.inventory.snapshot()["round"]
            if rnd < 3:
                return card
            if self.advisor:
                advice = self.advisor.for_product("", rnd)
        except StockError:
            # Startup/offline: advertise conditional policy, never fabricated stock.
            pass
        updated = deepcopy(card)
        updated.description = shop_copy(self.policy, advice)
        updated.skills[0].description = "Ask for a stocked SKU, then send an integer offer. Terms are quoted before checkout."
        return updated

    def event(self, task, outcome, product=None, price=None, *, deal=None):
        try:
            append_event(self.events, {"time": time.time(), "task": task, "outcome": outcome,
                                      "unit": (product or {}).get("unit"),
                                      "sku": (product or {}).get("sku"), "price": price,
                                      "category": (product or {}).get("category"),
                                      "cost": (product or {}).get("cost"),
                                      "warranty_hours": ((deal or {}).get("terms") or {}).get("warranty_hours"),
                                      "round": (deal or {}).get("round"),
                                      "advisor_version": ((deal or {}).get("advice") or {}).get("version")})
        except OSError:
            print("[inventory] Could not append seller event", flush=True)

    def discard(self, task):
        self.r3.pop(task, None)
        self.inventory.release(task)

    async def execute(self, context, event_queue):
        try:
            state = self.inventory.snapshot()
        except StockError:
            state = None
        if state and state["round"] < 3:
            return await super().execute(context, event_queue)
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        if not text or len(text) > 4000:
            await up.reject(say("Send a product SKU or a short integer offer."))
            return
        if task.id in self.pending:
            await up.requires_input(say("A decision for this task is already in progress."))
            return
        now = time.time()
        expired = {key for key, d in self.r3.items() if now - d["touched"] > 300 and key not in self.pending}
        for key in expired:
            self.discard(key)
        deal = self.r3.get(task.id)
        if task.id in expired or (context.current_task is not None and deal is None):
            await up.reject(say("Negotiation expired; start a new task."))
            return
        offer = None
        try:
            if deal is None:
                if len(self.r3) >= 64:
                    raise StockError("Shop has too many open negotiations")
                product = self.inventory.reserve(task.id, text)
                rnd = self.inventory.state["round"]
                advice = self.advisor.for_product(product["category"], rnd) if self.advisor else None
                # Pin even absent advice; a background update cannot alter this negotiation.
                deal = self.r3[task.id] = {"history": [], "ask": None, "turns": 0, "touched": now,
                                          "round": rnd, "advice": advice,
                                          "terms": selling_terms(product, self.policy)}
            else:
                if re.fullmatch(r"[0-9]{1,12}", text.strip()) is None:
                    await up.requires_input(say(f"Send an integer offer. Current asking price: {deal['ask']}"))
                    return
                offer = int(text.strip())
                if deal["turns"] >= 6 and offer != deal["ask"] or deal["turns"] > 6:
                    raise StockError("Negotiation limit reached")
                product = self.inventory.current(task.id)
            deal["touched"] = now
            history = bounded_history(deal["history"] + [{"role": "buyer", "text": text}])
        except (StockError, OSError) as exc:
            self.discard(task.id)
            await up.reject(say(str(exc)))
            return
        canceled = asyncio.Event()
        self.pending[task.id] = canceled
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        try:
            choice = await self.infer(inventory_decision, {**product, "sales_terms": deal["terms"]},
                                      offer, history, deal["ask"], deal["advice"])
            async with lock:
                if canceled.is_set():
                    return
                product = self.inventory.current(task.id)  # freshness/expiry after a slow model
                action, price = choice.get("action"), choice.get("price")
                if action not in ("counter", "accept", "reject"):
                    raise HarnessError("Invalid action")
                if action != "reject" and (type(price) is not int or price < deal["terms"]["price_floor"]):
                    raise HarnessError("Price below the agreed grade-based floor policy")
                if action == "accept" and (offer is None or price != offer):
                    raise HarnessError("Acceptance does not match the authorized offer")
                if action == "counter":
                    if deal["ask"] is not None and price > deal["ask"]:
                        raise HarnessError("Counteroffer increased the previous ask")
                    # Actual quote is labelled and last; facts never expose unit cost.
                    reply = quote_copy(product, price, deal["terms"], deal["advice"])
                    deal.update(ask=price, history=bounded_history(history + [{"role": "seller", "text": reply}]),
                                turns=deal["turns"] + (offer is not None), touched=time.time())
                    await up.requires_input(say(reply))
                elif action == "accept":
                    receipt = sign_receipt(self.signing_key, shop=self.cfg["name"], item=product["sku"],
                                           price=price, task=task.id)
                    receipt.update(unit=product["unit"], cert=True)
                    # Goods grades are fixed. Food claims can go stale between this message and checkout.
                    if product["kind"] == "goods":
                        receipt["claim"] = product["grade"]
                        if deal["terms"]["warranty_hours"]:
                            receipt["warranty_hours"] = deal["terms"]["warranty_hours"]
                    self.inventory.issue(task.id)
                    self.r3.pop(task.id, None)
                    await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                    await up.complete(say(f"Agreed {product['sku']} at {price}; checkout still required."))
                    self.event(task.id, "receipt_issued", product, price, deal=deal)
                else:
                    self.discard(task.id)
                    await up.reject(say("No deal."))
                    self.event(task.id, "rejected", product, deal=deal)
        except (HarnessError, StockError, OSError) as exc:
            self.discard(task.id)
            if not canceled.is_set():
                await up.failed(say(str(exc)))
                self.event(task.id, "error", product, deal=deal)
        except asyncio.CancelledError:
            canceled.set()
            self.discard(task.id)
            raise
        finally:
            self.pending.pop(task.id, None)
            self.locks.pop(task.id, None)

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task.id in self.open:
            return await super().cancel(context, event_queue)
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            if task.id in self.pending:
                self.pending[task.id].set()
            self.discard(task.id)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
        if task.id not in self.pending:
            self.locks.pop(task.id, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--shop-key", type=Path, default=ROOT / "shop_key.pem")
    args = parser.parse_args()
    cfg = {**DEFAULT, **json.loads((ROOT / "shop-adaptive.json").read_text()),
           "description": "Inventory shop with market certificates and signed receipts. Ask by SKU.",
           "skill_description": "Ask for an in-stock SKU, then send an integer offer.",
           "port": args.port, "shop_key": args.shop_key, "advisor_enabled": config_at()["enabled"]}
    uvicorn.run(build(cfg, InventorySeller(cfg)), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
