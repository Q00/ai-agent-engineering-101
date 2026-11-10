"""Inventory seller with pinned grade floors, guarantees and bounded advice."""
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
from inventory import Inventory, StockError, requested_skus
from opening_strategy import OpeningReader, bounds as opening_bounds
from receipt_signing import sign_receipt
from sales_advisor import AdviceReader, config_at
from shop_policy import closing_context, policy_at, quote_copy, selling_terms, shop_copy
from seller_agent import DEFAULT, ModelCapacityError, Seller, bounded_history, build
from seller_context import inventory_context
from warranty_trial import WarrantyTrial

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
    try:
        context = inventory_context(product, offer, history, last_ask, terms, negotiation, advice, now=time.time())
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc
    print("[seller-context] " + json.dumps({"stage": negotiation["stage"],
          "context_chars": len(json.dumps(context, ensure_ascii=False))}), flush=True)
    return decide("seller_inventory.txt", context, validate, prefix="inventory-seller")


def capacity_choice(product, offer, last_ask, negotiation):
    """Bounded deterministic response only when no model slot was acquired.

    Use pinned opening evidence first; otherwise add 10% to the active floor,
    capped at list unless the floor itself is higher. Never unlock a discount.
    Publication still rechecks inventory, cancellation and receipt invariants.
    """
    floor, stage = negotiation["price_floor"], negotiation["stage"]
    if offer is None:
        band = negotiation.get("opening_bounds")
        price = max(floor, min((floor * 110 + 99) // 100, product["list_price"]))
        if band:
            price = max(band["minimum"], min(band.get("target") or price, band["maximum"]))
        choice = {"action": "counter", "price": price}
    elif last_ask is not None and offer >= last_ask:
        choice = {"action": "accept", "price": offer}
    elif stage == "final_reply":
        choice = {"action": "reject"}
    elif stage in ("close", "final_offer"):
        choice = ({"action": "accept", "price": offer} if offer >= floor else
                  {"action": "counter", "price": floor})
    else:
        # Meet halfway, rounding up; never increase an ask or undercut the floor.
        price = max(floor, min(last_ask, (last_ask + max(floor, offer) + 1) // 2))
        choice = {"action": "counter", "price": price}
    return validate_choice(choice, offer, last_ask, negotiation)


class InventorySeller(Seller):
    def __init__(self, cfg):
        super().__init__({**cfg, "adaptive": {"enabled": False}, "sign_receipts": True,
                          "model_concurrency": 3})
        self.inventory = Inventory(cfg.get("market_dir", os.environ.get("MARKET_DIR", ROOT / ".market")))
        self.events = ROOT / "logs" / "inventory-seller-events.jsonl"
        self.r3 = {}
        self.policy = cfg.get("shop_policy") or policy_at()
        self.openings = OpeningReader(self.inventory.folder)
        self.warranty_trial = WarrantyTrial(self.inventory.folder)
        # A retained strategy cache cannot reactivate an intentionally disabled
        # analyst, even if a stale caller still passes advisor_enabled=True.
        self.advisor = (AdviceReader(self.inventory.folder)
                        if cfg.get("advisor_enabled") and config_at()["enabled"] else None)

    async def public_card(self, card):
        self.expire_negotiations()
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

    def event(self, task, outcome, product=None, price=None, *, deal=None,
              reason_code=None, detail=None, terminal=None, sku=None, reservation_kind=None):
        deal = deal or {}
        product = product or deal.get("product") or {}
        negotiation = deal.get("negotiation", {})
        try:
            append_event(self.events, {"time": time.time(), "task": task, "outcome": outcome,
                                      "unit": (product or {}).get("unit"),
                                      "sku": (product or {}).get("sku") or (sku[:64] if sku else None), "price": price,
                                      "category": (product or {}).get("category"),
                                      "cost": (product or {}).get("cost"),
                                      **{k: product.get(k) for k in ("kind", "grade", "supplied_at", "expires_at")},
                                      "warranty_hours": ((deal or {}).get("terms") or {}).get("warranty_hours"),
                                      "round": deal.get("round", (self.inventory.state or {}).get("round")),
                                      "reason_code": reason_code,
                                      "detail": " ".join(detail.split())[:240] if detail else None,
                                      "terminal": terminal,
                                      "reservation_kind": reservation_kind,
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
        if task not in self.pending:
            self.locks.pop(task, None)

    def expire_negotiations(self):
        # Sweep on every incoming request/card read, before stock selection. There
        # is no model call or per-negotiation timer, and at most 64 live deals.
        # Issued receipts deliberately live outside r3 and have no safe time TTL.
        now = time.time()
        expired = {key for key, d in self.r3.items() if now - d["touched"] > 300 and key not in self.pending}
        for key in expired:
            self.event(key, "expired", deal=self.r3[key], reason_code="negotiation_expired",
                       detail="No negotiation activity for 300 seconds; no receipt was issued.", terminal=True)
            self.discard(key)
        return expired

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
        if task.id in self.pending:
            # A malformed concurrent request must not release the first call's
            # reservation or publish a terminal rejection while it is deciding.
            self.event(task.id, "busy", deal=self.r3.get(task.id), reason_code="request_in_progress",
                       detail="A decision for this task is already in progress.", terminal=False)
            await up.requires_input(say("A decision for this task is already in progress."))
            return
        now = time.time()
        expired = self.expire_negotiations()
        deal = self.r3.get(task.id)
        if not text or len(text) > 4000:
            self.event(task.id, "error", deal=deal, reason_code="invalid_request",
                       detail="Empty or oversized request.", terminal=True)
            self.discard(task.id)
            await up.reject(say("Send a product SKU or a short integer offer."))
            return
        if task.id in expired or (context.current_task is not None and deal is None):
            if task.id not in expired:
                self.event(task.id, "expired", reason_code="negotiation_expired",
                           detail="Negotiation is no longer active; start a new task.", terminal=True)
            await up.reject(say("Negotiation expired; start a new task."))
            return
        offer, product = None, None
        try:
            if deal is None:
                if len(self.r3) >= 64:
                    raise StockError("Shop has too many open negotiations", reason_code="capacity_busy")
                product = self.inventory.reserve(task.id, text)
                rnd = self.inventory.state["round"]
                advice = self.advisor.for_product(product["category"], rnd) if self.advisor else None
                # Pin even absent advice; a background update cannot alter this negotiation.
                deal = self.r3[task.id] = {"history": [], "ask": None, "offers": [], "touched": now,
                                          "round": rnd, "advice": advice,
                                          "product": {k: product.get(k) for k in
                                                      ("unit", "sku", "category", "cost", "kind", "grade", "supplied_at", "expires_at")},
                                          "terms": self.warranty_trial.terms(
                                              product, selling_terms(product, self.policy), self.policy, rnd, now)}
                reference = self.openings.for_product(product, deal["terms"]["warranty_hours"], rnd)
                deal["opening_bounds"] = opening_bounds(product, deal["terms"], reference, self.policy, now, rnd=rnd)
                self.event(task.id, "opened", product, deal=deal)
            else:
                if re.fullmatch(r"[0-9]{1,12}", text.strip()) is None:
                    self.event(task.id, "invalid_request", deal=deal, reason_code="invalid_request",
                               detail="Expected an integer counteroffer; negotiation remains open.", terminal=False)
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
            skus = requested_skus(text)
            self.event(task.id, "stock_unavailable", deal=deal,
                       reason_code=getattr(exc, "reason_code", "inventory_unavailable"),
                       detail=str(exc), terminal=True,
                       sku=getattr(exc, "sku", None) or (skus[0].upper() if skus else None),
                       reservation_kind=getattr(exc, "reservation_kind", None))
            self.discard(task.id)
            await up.reject(say(str(exc)))
            return
        except Exception as exc:
            # Config/readers/policy failures happen after reserve but before a
            # model job is registered; they must not strand that reservation.
            self.event(task.id, "error", product, deal=deal, reason_code="internal_error",
                       detail=f"Preparing negotiation failed: {type(exc).__name__}", terminal=True)
            self.discard(task.id)
            await up.failed(say("Could not prepare negotiation; please retry."))
            return
        except asyncio.CancelledError:
            self.event(task.id, "cancelled", product, deal=deal, reason_code="buyer_cancelled",
                       detail="Request was cancelled before a model decision.", terminal=True)
            self.discard(task.id)
            raise
        canceled = asyncio.Event()
        self.pending[task.id] = canceled
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        receipt_issued = False
        phase = "decision"
        try:
            if offer is not None and deal["ask"] is not None and offer >= deal["ask"]:
                # Honor a quote already selected by the seller; no new model judgment needed.
                choice = {"action": "accept", "price": offer}
                deal["decision_source"] = "quoted_price_accepted"
            elif deal["negotiation"]["stage"] == "final_reply":
                choice = {"action": "reject"}
                deal["decision_source"] = "final_offer_declined"
            elif (deal["negotiation"]["stage"] in ("close", "final_offer")
                  and offer >= deal["negotiation"]["price_floor"]):
                # The pinned closing policy already requires this exact acceptance.
                choice = {"action": "accept", "price": offer}
                deal["decision_source"] = "closing_floor_accepted"
            elif deal["negotiation"]["stage"] == "final_offer":
                # A below-floor final bid must receive the active floor once.
                choice = {"action": "counter", "price": deal["negotiation"]["price_floor"]}
                deal["decision_source"] = "final_floor_quoted"
            else:
                deal["decision_source"] = "model"  # Exact profile/model is recorded by the harness.
                phase = "model"
                try:
                    choice = await self.infer(inventory_decision,
                                              {**product, "sales_terms": deal["terms"],
                                               "negotiation": deal["negotiation"]},
                                              offer, history, deal["ask"], deal["advice"])
                except ModelCapacityError:
                    phase = "capacity_policy"
                    choice = capacity_choice(product, offer, deal["ask"], deal["negotiation"])
                    deal["decision_source"] = "capacity_fallback"
            phase = "publication"
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
                    self.inventory.issue(task.id, receipt=receipt)
                    receipt_issued = True
                    self.r3.pop(task.id, None)
                    # Persistence is the commit point, even when the response
                    # connection subsequently fails. Never free this unit here.
                    self.event(task.id, "receipt_issued", product, price, deal=deal, terminal=True)
                    await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                    expiry_notice = " Complete checkout within 5 minutes." if self.inventory.leases else ""
                    await up.complete(say(f"Agreed {product['sku']} at {price}; checkout still required." + expiry_notice))
                else:
                    self.discard(task.id)
                    self.event(task.id, "rejected", product, deal=deal, reason_code="price_rejected",
                               detail="Buyer's offer did not meet the final quote.", terminal=True)
                    await up.reject(say("No deal."))
        except Exception as exc:
            self.discard(task.id)
            if not canceled.is_set():
                reason = ("receipt_delivery_error" if receipt_issued else
                          exc.reason_code if isinstance(exc, StockError) else
                          "model_error" if phase == "model" or isinstance(exc, (HarnessError, TimeoutError)) else "internal_error")
                detail = (str(exc) if isinstance(exc, (HarnessError, StockError)) else
                          f"Negotiation failed: {type(exc).__name__}")
                self.event(task.id, "delivery_error" if receipt_issued else "error", product, deal=deal,
                           reason_code=reason, detail=detail, terminal=not receipt_issued)
                await up.failed(say("Receipt retained; check task status before retrying." if receipt_issued else
                                    str(exc) if isinstance(exc, (HarnessError, StockError)) else
                                    "Negotiation failed; please start a new task."))
        except asyncio.CancelledError:
            canceled.set()
            if not receipt_issued:
                self.event(task.id, "cancelled", product, deal=deal, reason_code="buyer_cancelled",
                           detail="Request was cancelled before a receipt was issued.", terminal=True)
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
                self.event(task.id, "cancelled", deal=self.r3[task.id], reason_code="buyer_cancelled",
                           detail="Buyer cancelled negotiation before a receipt was issued.", terminal=True)
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
