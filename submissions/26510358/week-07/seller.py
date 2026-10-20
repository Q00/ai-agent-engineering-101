"""Harness-driven A2A v1.0 seller. Transport/guards never generate negotiation prices."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import time
import uuid

import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from a2a.utils.errors import TaskNotCancelableError

from catalog import load_catalog, search_catalog
from evidence import EvidenceError, inspect_untrusted, validate_seller_decision
from harness import Harness, HarnessError
from policy import StrategyPolicy, strategy_instruction
from tracing import Trace
from receipts import load_shop_key, sign_receipt


class Seller(AgentExecutor):
    def __init__(self, config):
        self.config = config
        self.signing_key = None
        feedback_path = Path(__file__).resolve().parent / "data" / "pricing-feedback.json"
        self.pricing_feedback = json.loads(feedback_path.read_text()) if feedback_path.exists() else {}
        self.catalog = load_catalog(config.get("catalog"))
        categories = config.get("categories")
        if categories:
            self.catalog = [p for p in self.catalog if p["category"] in categories]
        offset = config.get("reserve_offset", 0)
        if offset:
            self.catalog = [{**p, "reserve": p["reserve"] + offset} for p in self.catalog]
        self.trace = Trace("seller", config.get("log_dir"))
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
                         policy_version=self.policy.version)

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            if task.id in self.terminal:
                return
            await self._turn(task, context, event_queue)

    async def _turn(self, task, context, event_queue):
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        metadata = MessageToDict(context.message.metadata) if context.message is not None else {}
        stance = metadata.get("negotiationStance")
        remote_stance = stance if isinstance(stance, str) and stance in ("negotiable", "firm") else None
        flags = inspect_untrusted(text)
        deal = self.open.get(task.id)
        opening = deal is None
        candidates = search_catalog(text, self.catalog, limit=8) if opening else [deal["item"]]
        # A request naming a catalog ID is an exact product lookup, not a request for a cheaper variant.
        exact = re.fullmatch(r"(?:I want to buy (?:a |an )?)?([A-Za-z0-9_-]+)[.!]?", text.strip(), re.I)
        if opening and exact:
            named = [p for p in self.catalog if p["id"].lower() == exact.group(1).lower()]
            if named:
                candidates = named
        self.trace.write("remote_message", task_id=task.id, message=text, flags=flags,
                         candidates=[p["id"] for p in candidates], remote_stance=remote_stance)
        if not candidates:
            async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                if task.id not in self.terminal:
                    await up.reject(up.new_agent_message(parts=[new_text_part("No matching catalog item.")]))
                    self.terminal[task.id] = "rejected"
            return
        plain_offer = float(text.strip()) if re.fullmatch(r"\d+(?:\.\d+)?", text.strip()) else None
        trusted = {"role": "seller", "opening": opening, "task_id": task.id,
                   # A public stance is an untrusted claim, never budget proof.
                   "remote_stance": remote_stance,
                   "round": 0 if opening else deal["round"], "candidates": candidates,
                   "remote_message": text, "untrusted_flags": flags,
                   "history_summary": [] if opening else deal["history"][-8:],
                   "last_ask": None if opening else deal["ask"],
                   "pricing_feedback": self.pricing_feedback}
        reserve = candidates[0]["reserve"]
        trusted.update(reserve=reserve, offer=plain_offer, flags=flags)
        strategy = self.policy.choose("seller", trusted)

        def validate(decision):
            selected = next((p for p in candidates if p["id"] == decision.get("item_id")), None)
            if selected is None:
                raise EvidenceError("Select item_id from the supplied candidates")
            observed_offer = plain_offer if plain_offer is not None else decision.get("offer")
            if observed_offer is not None and plain_offer is None:
                if not any(float(token) == float(observed_offer) for token in re.findall(r"\d+(?:\.\d+)?", text)):
                    raise EvidenceError("Extracted offer must occur in the quoted buyer message")
            if decision.get("price") is not None and float(decision["price"]) != int(decision["price"]):
                raise EvidenceError("Market prices must be whole credits")
            return validate_seller_decision(decision, reserve=selected["reserve"],
                offer=observed_offer, opening=opening,
                allowed_item_ids=[p["id"] for p in candidates])

        try:
            decision = await asyncio.to_thread(self.harness.decide, trusted, strategy=strategy,
                instruction=strategy_instruction("seller", strategy), validator=validate,
                deadline=time.monotonic() + 90)
        except (HarnessError, EvidenceError) as exc:
            async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
                if task.id in self.terminal:
                    self.trace.write("terminal_decision_discarded", task_id=task.id,
                                     state=self.terminal[task.id])
                    return
                self.trace.write("negotiation_failed", task_id=task.id, detail=str(exc))
                await up.failed(up.new_agent_message(parts=[new_text_part("Harness could not return a valid decision.")]))
                self.terminal[task.id] = "failed"
            return
        async with self.publication_locks.setdefault(task.id, asyncio.Lock()):
            if task.id in self.terminal:
                self.trace.write("terminal_decision_discarded", task_id=task.id,
                                 state=self.terminal[task.id])
                return
            await self._publish_decision(task, up, candidates, deal, opening, text, decision)

    async def _publish_decision(self, task, up, candidates, deal, opening, text, decision):
        """Apply one validated decision while cancellation cannot interleave events."""
        item = next(p for p in candidates if p["id"] == decision["item_id"])
        if opening:
            deal = {"item": item, "ask": decision["price"], "round": 0, "history": []}
            self.open[task.id] = deal
        deal["round"] += 1
        deal["history"].append({"remote": text, "action": decision["action"], "price": decision["price"]})
        action = decision["action"]
        if action in ("open", "counter"):
            deal["ask"] = decision["price"]
            deal["quote_id"] = uuid.uuid4().hex
            metadata = {"ask": deal["ask"], "itemId": item["id"], "quoteId": deal["quote_id"],
                        "seller": self.config["name"], "quantity": 1, "currency": "credits",
                        "description": item["description"], "attributes": item["attributes"]}
            # Never place a round number or another price after the ask: the official kit reads the last number.
            msg = up.new_agent_message(parts=[new_text_part(
                f"{item['name']}. Asking price: {json.dumps(deal['ask'], allow_nan=False)}")],
                                       metadata=metadata)
            await up.requires_input(msg)
            self.trace.write("quote", task_id=task.id, metadata=metadata)
        elif action == "accept":
            receipt = {"seller": self.config["name"], "item": item["id"], "itemId": item["id"],
                       "price": decision["price"], "taskId": task.id, "quantity": 1,
                       "currency": "credits", "purchaseId": uuid.uuid4().hex,
                       "quoteId": deal.get("quote_id")}
            if self.signing_key is None:
                self.signing_key = load_shop_key(self.config.get("shop_key"))
            receipt.update(shop=self.config["name"], task=task.id)
            receipt = sign_receipt(receipt, self.signing_key)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(up.new_agent_message(parts=[new_text_part("Deal completed. See receipt.")]))
            self.open.pop(task.id, None)
            self.terminal[task.id] = "completed"
            self.trace.write("sale_completed", task_id=task.id, receipt=receipt)
        else:
            await up.reject(up.new_agent_message(parts=[new_text_part("Negotiation ended without a deal.")]))
            self.open.pop(task.id, None)
            self.terminal[task.id] = "rejected"
            self.trace.write("sale_rejected", task_id=task.id)

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


class CancellationAwareRequestHandler(DefaultRequestHandler):
    """Preserve a concurrent completed sale while returning the A2A cancel error."""
    async def on_cancel_task(self, params, context):
        task = await super().on_cancel_task(params, context)
        if task is None or task.status.state != TaskState.TASK_STATE_CANCELED:
            raise TaskNotCancelableError(message="Task finished before cancellation")
        return task


def build(config):
    categories = config.get("categories", ["lamp", "chair", "bookshelf"])
    description = config.get("description", "Shop selling " + ", ".join(categories) + ". Good-value offers, whole-credit prices and Ed25519 signed receipts.")
    card = AgentCard(name=config["name"], description=description, version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
            url=f"http://127.0.0.1:{config['port']}/")],
        skills=[AgentSkill(id="negotiate", name="Catalog search and price negotiation", description=description,
            tags=["shopping", *categories], examples=["I want to buy a lamp", "A wooden shelf for 40 books"])])
    handler = CancellationAwareRequestHandler(agent_executor=Seller(config), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--config")
    parser.add_argument("--policy")
    args = parser.parse_args()
    config = {
        "name": "Ssadassa Shop",
        "port": args.port,
        "description": (
            "Your agreed price, on a signed receipt. Shop lamps, chairs, and bookshelves at "
            "good-value, whole-credit prices. Tell us what you need and make an offer—we'll "
            "respond with clear terms and provide an Ed25519-signed receipt recording the "
            "agreed final price when the deal is complete. Find the right item, negotiate a "
            "price that works, and close with a verifiable record."
        ),
    }
    if args.config:
        config.update(json.loads(Path(args.config).read_text()))
    config["port"] = args.port
    if args.policy:
        config["policy"] = args.policy
    uvicorn.run(build(config), host="127.0.0.1", port=args.port, log_level="warning")
