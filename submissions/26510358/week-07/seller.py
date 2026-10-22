"""Harness-driven A2A v1.0 seller. Transport/guards never generate negotiation prices."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import re
import time
import uuid

import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from a2a.utils.errors import TaskNotCancelableError

from catalog import load_catalog, search_catalog
from evidence import EvidenceError, actual_offer, inspect_untrusted, validate_seller_decision
from harness import Harness, HarnessError
from policy import StrategyPolicy, strategy_instruction
from tracing import Trace
from receipts import canonical_receipt, load_shop_key, sign_receipt
from pricing import PricePolicy, opening_ask
from shopfront import public_items, public_description, public_skills
from buyer_access import BuyerAccess
from trade_funnel import emit_stage


class Seller(AgentExecutor):
    def __init__(self, config):
        self.config = config
        self.signing_key = None
        # Benchmarked opening caps are configuration; the harness still picks every price.
        price_path = Path(config.get("price_policy") or Path(__file__).resolve().parent / "data" / "price-policy.json")
        self.price_policy = json.loads(price_path.read_text())
        self.catalog = load_catalog(config.get("catalog"))
        categories = config.get("categories")
        if categories:
            self.catalog = [p for p in self.catalog if p["category"] in categories]
        offset = config.get("reserve_offset", 0)
        if offset:
            self.catalog = [{**p, "reserve": p["reserve"] + offset} for p in self.catalog]
        self.trace = Trace("seller", config.get("log_dir"))
        self.buyer_access = BuyerAccess(config.get("buyer_access"))
        self.pricing = PricePolicy(self.price_policy, self.trace,
                                       state_path=config.get("pricing_state"))
        self.harness = Harness("seller", self.trace)
        self.policy = StrategyPolicy(config.get("policy"))
        self.trace.write("policy_loaded", policy_sha256=self.policy.sha256,
                         policy_version=self.policy.version)
        self.open = {}
        self.locks = {}
        # The turn lock covers model work; cancellation only waits for the short
        # event-publication lock. SDK TaskUpdater terminal flags are per instance.
        self.publication_locks = {}
        self.terminal = {}
        self.trace.write("seller_started", name=config["name"], catalog_size=len(self.catalog),
                         policy_version=self.policy.version, price_policy=self.price_policy["version"])

    def opening_cap(self, item, quote_policy=None):
        """Highest allowed quote under this task's original pricing phase."""
        quote_policy = quote_policy or self.pricing.quote_policy()
        return opening_ask(item, quote_policy)

    def stage(self, task, stage, **fields):
        """Record observed progress without adding remote text or private limits."""
        emit_stage(self.trace, stage, side="seller", task_id=task.id,
                   shop=self.config["name"], **fields)

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            if task.id in self.terminal:
                return
            denied = self._buyer_access_denial(context)
            if denied:
                async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                    await self._reject_buyer(task, event_queue, denied)
                return
            try:
                await self._turn(task, context, event_queue)
            except Exception:
                self.stage(task, "ended", outcome="error", reason_code="seller_execution_error")
                raise

    def _buyer_access_denial(self, context):
        metadata = MessageToDict(context.message.metadata) if context.message is not None else {}
        call_context = getattr(context, "call_context", None)
        return self.buyer_access.match(user=getattr(call_context, "user", None),
            message_metadata=metadata, request_metadata=getattr(context, "metadata", None))

    async def _reject_buyer(self, task, event_queue, denied):
        """Caller holds the publication lock, so denial cannot race a receipt."""
        if task.id not in self.terminal:
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            await up.reject(up.new_agent_message(parts=[new_text_part(
                "This shop is unavailable to this buyer under its local access policy.")]))
            self.open.pop(task.id, None)
            self.terminal[task.id] = "rejected"
            self.trace.write("buyer_access_denied", task_id=task.id,
                             policy_version=self.buyer_access.version, **denied)
            self.stage(task, "ended", outcome="rejected", reason_code="access_policy")

    async def _turn(self, task, context, event_queue):
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        metadata = MessageToDict(context.message.metadata) if context.message is not None else {}
        stance = metadata.get("negotiationStance")
        remote_stance = stance if isinstance(stance, str) and stance in ("negotiable", "firm") else None
        flags = inspect_untrusted(text)
        deal = self.open.get(task.id)
        opening = deal is None
        if opening:
            self.stage(task, "negotiation_started")
        quote_policy = (self.pricing.refresh()
                        if opening else deal.get("quote_policy", self.pricing.quote_policy()))
        query = text
        candidates = search_catalog(query, self.catalog, limit=8) if opening else [deal["item"]]
        # A request naming a catalog ID is an exact product lookup, not a request for a cheaper variant.
        exact = re.fullmatch(r"(?:I want to buy (?:a |an )?)?([A-Za-z0-9_-]+)[.!]?", query.strip(), re.I)
        if opening and exact:
            named = [p for p in self.catalog if p["id"].lower() == exact.group(1).lower()]
            if named:
                candidates = named
        if opening:
            candidates = [{**p, "max_opening_ask": self.opening_cap(p, quote_policy)} for p in candidates]
        self.trace.write("remote_message", task_id=task.id, message=text, flags=flags,
                         candidates=[p["id"] for p in candidates], remote_stance=remote_stance)
        if not candidates:
            async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                if task.id not in self.terminal:
                    await up.reject(up.new_agent_message(parts=[new_text_part("No matching catalog item.")]))
                    self.terminal[task.id] = "rejected"
                    self.stage(task, "ended", outcome="rejected", reason_code="no_matching_item")
            return
        plain_offer = actual_offer(text)
        message_id = getattr(context.message, "message_id", "")
        offer_ids = set() if opening else deal.setdefault("offer_ids", set())
        offer_count = 0 if opening else deal.get("offer_count", 0)
        if plain_offer is not None and (not message_id or message_id not in offer_ids):
            offer_count += 1
            offer_ids.add(message_id)
        if not opening:
            deal.update(offer_count=offer_count, offer_ids=offer_ids)
        if plain_offer is not None:
            self.stage(task, "offer_received", item=candidates[0]["id"], price=plain_offer,
                       quote_id=None if opening else deal.get("quote_id"),
                       quantity=1, offer_number=offer_count)
        trusted = {"role": "seller", "opening": opening, "task_id": task.id,
                   # A public stance is an untrusted claim, never budget proof.
                   "remote_stance": remote_stance,
                   "round": 0 if opening else deal["round"], "candidates": candidates,
                   "remote_message": text, "untrusted_flags": flags,
                   "history_summary": [] if opening else deal["history"][-8:],
                   "last_ask": None if opening else deal["ask"],
                   "price_policy": self.price_policy["version"],
                   "pricing_phase": quote_policy["phase"],
                   "observed_offer": plain_offer, "offer_count": offer_count,
                   "accept_floor_bids": quote_policy["accept_floor_bids"]}
        reserve = candidates[0]["reserve"]
        trusted.update(reserve=reserve, offer=plain_offer, flags=flags)
        strategy = self.policy.choose("seller", trusted)
        # Priority is derived from our own open task and verified numeric bid.
        # Remote metadata, claimed budget or a low bid cannot buy queue priority.
        can_close = (not opening and plain_offer is not None and plain_offer >= reserve
                     and (plain_offer >= deal["ask"] or quote_policy["accept_floor_bids"]
                          or offer_count >= 2))
        admission = {"priority": "completion"} if can_close else {}
        self.trace.write("seller_admission_priority", task_id=task.id,
                         priority="completion" if can_close else "normal")

        def validate(decision):
            selected = next((p for p in candidates if p["id"] == decision.get("item_id")), None)
            if selected is None:
                raise EvidenceError("Select item_id from the supplied candidates")
            observed_offer = plain_offer
            if decision.get("offer") != observed_offer:
                raise EvidenceError("offer must equal the runtime-verified actual bid or null")
            if decision.get("price") is not None and float(decision["price"]) != int(decision["price"]):
                raise EvidenceError("Market prices must be whole credits")
            return validate_seller_decision(decision, reserve=selected["reserve"],
                offer=observed_offer, opening=opening,
                allowed_item_ids=[p["id"] for p in candidates],
                max_ask=selected.get("max_opening_ask"), last_ask=None if opening else deal["ask"],
                accept_floor_bids=quote_policy["accept_floor_bids"],
                offer_count=offer_count)

        try:
            decision = await asyncio.to_thread(self.harness.decide, trusted, strategy=strategy,
                instruction=strategy_instruction("seller", strategy), validator=validate,
                # Bound queue + model + retries together, below public proxy timeouts.
                deadline=time.monotonic() + 35, attempts=3, **admission)
        except (HarnessError, EvidenceError) as exc:
            async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                if task.id in self.terminal:
                    self.trace.write("terminal_decision_discarded", task_id=task.id,
                                     state=self.terminal[task.id])
                    return
                self.trace.write("negotiation_failed", task_id=task.id, detail=str(exc))
                await up.failed(up.new_agent_message(parts=[new_text_part("Harness could not return a valid decision.")]))
                self.terminal[task.id] = "failed"
                self.stage(task, "ended", outcome="error", reason_code="negotiation_failed")
            return
        async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
            if task.id in self.terminal:
                self.trace.write("terminal_decision_discarded", task_id=task.id,
                                 state=self.terminal[task.id])
                return
            denied = self._buyer_access_denial(context)
            if denied:
                await self._reject_buyer(task, event_queue, denied)
                return
            await self._publish_decision(task, up, candidates, deal, opening, text, decision,
                                         quote_policy=quote_policy, offer_count=offer_count, offer_ids=offer_ids)

    async def _publish_decision(self, task, up, candidates, deal, opening, text, decision, *,
                                quote_policy=None, offer_count=0, offer_ids=None):
        """Apply one validated decision while cancellation cannot interleave events."""
        item = next(p for p in candidates if p["id"] == decision["item_id"])
        if opening:
            deal = {"item": item, "ask": decision["price"], "round": 0, "history": [],
                    "quote_policy": quote_policy or self.pricing.quote_policy(),
                    "offer_count": offer_count, "offer_ids": offer_ids or set()}
            self.open[task.id] = deal
        deal["round"] += 1
        deal["history"].append({"remote": text, "action": decision["action"], "price": decision["price"]})
        action = decision["action"]
        if action in ("open", "counter"):
            changed = deal["ask"] != decision["price"]
            deal["ask"] = decision["price"]
            if changed or not deal.get("quote_id"):
                deal["quote_id"] = uuid.uuid4().hex
            metadata = {"ask": deal["ask"], "itemId": item["id"], "quoteId": deal["quote_id"],
                        "seller": self.config["name"], "quantity": 1, "currency": "credits",
                        "description": item["description"], "attributes": item["attributes"]}
            # Never place a round number or another price after the ask: the official kit reads the last number.
            ask = json.dumps(deal["ask"], allow_nan=False)
            msg = up.new_agent_message(parts=[new_text_part(
                f"{item['id']} x 1, total {ask} credits. Offer that total on this task. Asking price: {ask}")],
                                       metadata=metadata)
            await up.requires_input(msg)
            self.trace.write("quote", task_id=task.id, metadata=metadata)
            self.stage(task, "quote_issued", item=item["id"], quote_id=deal["quote_id"],
                       price=deal["ask"], quantity=1)
        elif action == "accept":
            self.stage(task, "offer_accepted", item=item["id"], quote_id=deal.get("quote_id"),
                       price=decision["price"], quantity=1, evidence="validated_decision")
            receipt = {"seller": self.config["name"], "item": item["id"], "itemId": item["id"],
                       "price": decision["price"], "taskId": task.id, "quantity": 1,
                       "currency": "credits", "purchaseId": uuid.uuid4().hex,
                       "quoteId": deal.get("quote_id")}
            receipt.update(shop=self.config["name"], task=task.id)
            try:
                if self.signing_key is None:
                    self.signing_key = load_shop_key(self.config.get("shop_key"))
                receipt = sign_receipt(receipt, self.signing_key)
                self.signing_key.public_key().verify(base64.b64decode(receipt["sig_seller"]),
                                                     canonical_receipt(receipt))
            except Exception as exc:
                self.trace.write("receipt_validation_failed", task_id=task.id, detail=str(exc))
                await up.failed(up.new_agent_message(parts=[new_text_part("Receipt validation failed; settlement is unresolved.")]))
                self.open.pop(task.id, None)
                self.terminal[task.id] = "failed"
                self.stage(task, "ended", item=item["id"], outcome="unresolved",
                           reason_code="receipt_validation_failed")
                return
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            self.stage(task, "receipt_issued", item=item["id"], quote_id=deal.get("quote_id"),
                       price=decision["price"], quantity=1, evidence="local_signature")
            await up.complete(up.new_agent_message(parts=[new_text_part(
                f"{item['id']} x 1 accepted, total {decision['price']} credits. "
                "Signed receipt issued; market validation pending.")]))
            self.open.pop(task.id, None)
            self.terminal[task.id] = "completed"
            self.trace.write("sale_completed", task_id=task.id, receipt=receipt,
                             receipt_signature_checked=True, market_verified=False,
                             offer_count=deal.get("offer_count", 0))
            self.stage(task, "ended", item=item["id"], outcome="completed",
                       reason_code="local_receipt_issued")
        else:
            await up.reject(up.new_agent_message(parts=[new_text_part("Negotiation ended without a deal.")]))
            self.open.pop(task.id, None)
            self.terminal[task.id] = "rejected"
            self.trace.write("sale_rejected", task_id=task.id)
            self.stage(task, "ended", item=item["id"], outcome="rejected",
                       reason_code="negotiation_rejected")

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task:
            async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                if task.id in self.terminal:
                    # V2 treats executor.cancel() exceptions as task failures.
                    # Keep the completed publication intact; the handler below
                    # reports the non-cancelable outcome outside ActiveTask.
                    self.trace.write("cancel_already_terminal", task_id=task.id,
                                     state=self.terminal[task.id])
                    return
                self.terminal[task.id] = "canceled"
                self.open.pop(task.id, None)
                await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
                self.trace.write("sale_canceled", task_id=task.id)
                self.stage(task, "ended", outcome="canceled", reason_code="caller_canceled")


class CancellationAwareRequestHandler(DefaultRequestHandler):
    """Preserve a concurrent completed sale while returning the A2A cancel error."""
    async def on_cancel_task(self, params, context):
        task = await super().on_cancel_task(params, context)
        if task is None or task.status.state != TaskState.TASK_STATE_CANCELED:
            raise TaskNotCancelableError(message="Task finished before cancellation")
        return task


def build(config):
    executor = Seller(config)
    categories = config.get("categories", ["lamp", "chair", "bookshelf"])
    description = config.get("description", "Shop selling " + ", ".join(categories) +
        ". Request a current quote; completed purchases include an Ed25519-signed receipt.")
    card = AgentCard(name=config["name"], description=description, version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
            url=f"http://127.0.0.1:{config['port']}/")],
        skills=[AgentSkill(id="negotiate", name="Catalog search and price negotiation", description=(
            "Name an item to receive a quote. Reply with a numeric offer on the same task; "
            "the shop responds with a counteroffer, a signed receipt, or a rejection."),
            tags=["shopping", *categories], examples=["I want to buy a lamp", "A wooden shelf for 40 books"])])
    handler = CancellationAwareRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    if config.get("public_profile"):
        async def public_card(_request):
            # No network/model call on discovery. The cached phase is the latest
            # observed phase; a fresh negotiation always obtains its own quote.
            offers = public_items(executor.catalog, executor.pricing.quote_policy())
            body = MessageToDict(card)
            body["description"] = config.get("description") or public_description(offers)
            body["skills"] = public_skills(offers)
            return JSONResponse(body, headers={"Cache-Control": "no-store"})
        routes.insert(0, Route("/.well-known/agent-card.json", public_card, methods=["GET"]))
    return Starlette(routes=routes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--config")
    parser.add_argument("--policy")
    args = parser.parse_args()
    config = {
        "name": "Ssadassa Shop",
        "port": args.port,
        "public_profile": True,
    }
    if args.config:
        config.update(json.loads(Path(args.config).read_text()))
    config["port"] = args.port
    if args.policy:
        config["policy"] = args.policy
    uvicorn.run(build(config), host="127.0.0.1", port=args.port, log_level="warning")
