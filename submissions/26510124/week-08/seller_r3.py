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
from opening_strategy import OpeningReader, bounds as opening_bounds
from receipt_signing import sign_receipt
from sales_advisor import AdviceReader, config_at
from shop_policy import closing_context, policy_at, quote_copy, selling_terms, shop_copy
from seller_agent import DEFAULT, Seller, bounded_history, build
from seller_context import inventory_context

ROOT = Path(__file__).resolve().parent


def validate_choice(choice, offer, last_ask, negotiation):
    """Enforce the same policy on model output and just before a signed receipt."""
    action, price = choice.get("action"), choice.get("price")
    stage, floor = negotiation["stage"], negotiation["price_floor"]
    if action not in ("counter", "accept", "reject"):
        raise HarnessError("Invalid action")
    if action != "reject" and (type(price) is not int or price < floor):
        raise HarnessError("Price below the active grade-based floor policy")
    if action == "accept" and (offer is None or price != offer):
        raise HarnessError("Acceptance does not match the authorized offer")
    if offer is None and action != "counter":
        raise HarnessError("Opening must quote this unit")
    band = negotiation.get("opening_bounds")
    if offer is None and band and not band["minimum"] <= price <= band["maximum"]:
        raise HarnessError("Opening quote is outside the pinned evidence/stock-age bounds")
    if action == "counter" and last_ask is not None and price > last_ask:
        raise HarnessError("Counteroffer increased the previous ask")
    if stage == "final_reply":
        expected = "accept" if offer >= last_ask else "reject"
        if action != expected:
            raise HarnessError("Final reply must honor the final quote or end negotiation")
    elif action == "reject":
        raise HarnessError("Give the buyer a valid counteroffer before ending negotiation")
    if stage in ("close", "final_offer") and offer >= floor and action != "accept":
        raise HarnessError("Closing policy accepts an offer at or above the active floor")
    if stage == "final_offer" and action == "counter" and price != floor:
        raise HarnessError("Final counteroffer must be the active floor")
    return choice


def inventory_decision(product, offer, history, last_ask, advice=None):
    terms = product.get("sales_terms") or selling_terms(product, policy_at())
    negotiation = product.get("negotiation") or {
        "stage": "opening" if offer is None else "negotiate", "price_floor": terms["price_floor"]}
    def validate(raw):
        return validate_choice(parse_seller(raw, negotiation["price_floor"], offer),
                               offer, last_ask, negotiation)
    context = inventory_context(product, offer, history, last_ask, terms, negotiation, advice, now=time.time())
    print("[seller-context] " + json.dumps({"stage": negotiation["stage"],
          "context_chars": len(json.dumps(context, ensure_ascii=False))}), flush=True)
    return decide("seller_inventory.txt", context, validate, prefix="inventory-seller")


class InventorySeller(Seller):
    def __init__(self, cfg):
        super().__init__({**cfg, "adaptive": {"enabled": False}, "sign_receipts": True})
        self.inventory = Inventory(cfg.get("market_dir", os.environ.get("MARKET_DIR", ROOT / ".market")))
        self.events = ROOT / "logs" / "inventory-seller-events.jsonl"
        self.r3 = {}
        self.policy = cfg.get("shop_policy") or policy_at()
        self.openings = OpeningReader(self.inventory.folder)
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
        deal = deal or {}
        product = product or deal.get("product") or {}
        negotiation = deal.get("negotiation", {})
        try:
            append_event(self.events, {"time": time.time(), "task": task, "outcome": outcome,
                                      "unit": (product or {}).get("unit"),
                                      "sku": (product or {}).get("sku"), "price": price,
                                      "category": (product or {}).get("category"),
                                      "cost": (product or {}).get("cost"),
                                      **{k: product.get(k) for k in ("kind", "grade", "supplied_at", "expires_at")},
                                      "warranty_hours": ((deal or {}).get("terms") or {}).get("warranty_hours"),
                                      "round": deal.get("round"),
                                      "stage": negotiation.get("stage"),
                                      "buyer_offers": len(deal.get("offers", [])),
                                      "active_floor": negotiation.get("price_floor"),
                                      "discount_reason": negotiation.get("discount_reason"),
                                      "decision_source": deal.get("decision_source"),
                                      "advisor_version": (deal.get("advice") or {}).get("version")})
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
            self.event(key, "expired", deal=self.r3[key])
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
                deal = self.r3[task.id] = {"history": [], "ask": None, "offers": [], "touched": now,
                                          "round": rnd, "advice": advice,
                                          "product": {k: product.get(k) for k in
                                                      ("unit", "sku", "category", "cost", "kind", "grade", "supplied_at", "expires_at")},
                                          "terms": selling_terms(product, self.policy)}
                reference = self.openings.for_product(product, deal["terms"]["warranty_hours"], rnd)
                deal["opening_bounds"] = opening_bounds(product, deal["terms"], reference, self.policy, now)
                self.event(task.id, "opened", product, deal=deal)
            else:
                if re.fullmatch(r"[0-9]{1,12}", text.strip()) is None:
                    await up.requires_input(say(f"Send an integer offer. Current asking price: {deal['ask']}"))
                    return
                offer = int(text.strip())
                product = self.inventory.current(task.id)
                deal["offers"].append(offer)
            deal["touched"] = now
            deal["negotiation"] = closing_context(product, deal, now)
            if offer is None:
                deal["negotiation"]["opening_bounds"] = deal["opening_bounds"]
            history = bounded_history(deal["history"] + [{"role": "buyer", "text": text}])
        except (StockError, OSError) as exc:
            self.event(task.id, "stock_unavailable", deal=deal)
            self.discard(task.id)
            await up.reject(say(str(exc)))
            return
        canceled = asyncio.Event()
        self.pending[task.id] = canceled
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        try:
            if offer is not None and deal["ask"] is not None and offer >= deal["ask"]:
                # Honor a quote already selected by the seller; no new model judgment needed.
                choice = {"action": "accept", "price": offer}
                deal["decision_source"] = "quoted_price_accepted"
            elif deal["negotiation"]["stage"] == "final_reply":
                choice = {"action": "reject"}
                deal["decision_source"] = "final_offer_declined"
            else:
                deal["decision_source"] = "haiku"
                choice = await self.infer(inventory_decision,
                                          {**product, "sales_terms": deal["terms"],
                                           "negotiation": deal["negotiation"]},
                                          offer, history, deal["ask"], deal["advice"])
            async with lock:
                if canceled.is_set():
                    return
                product = self.inventory.current(task.id)  # freshness/expiry after a slow model
                validate_choice(choice, offer, deal["ask"], deal["negotiation"])
                action, price = choice.get("action"), choice.get("price")
                if action == "counter":
                    # Actual quote is labelled and last; facts never expose unit cost.
                    final = deal["negotiation"]["stage"] == "final_offer"
                    reply = quote_copy(product, price, deal["terms"], deal["advice"], final=final)
                    deal.update(ask=price, history=bounded_history(history + [{"role": "seller", "text": reply}]),
                                final_quote=final, touched=time.time())
                    await up.requires_input(say(reply))
                    self.event(task.id, "final_offer" if final else "quote", product, price, deal=deal)
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
            if task.id in self.r3:
                self.event(task.id, "cancelled", deal=self.r3[task.id])
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
