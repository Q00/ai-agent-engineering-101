"""A2A v1.0 seller: model chooses, contracts validate, adapter publishes.

Start with: python seller.py --port 9999
Use --catalog catalog/products.jsonl for description-based large-catalog sales.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import re

from starlette.applications import Starlette
import uvicorn

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.errors import UnsupportedOperationError

from negotiation.config import ROOT, load_settings
from negotiation.contracts import SellerContext
from negotiation.harness import HarnessError, ModelHarness
from negotiation.runlog import RunLog
from negotiation.state import SellerMemory
from negotiation.catalog import Catalog, matches
from negotiation.catalog_contracts import NeedContext, SelectionContext


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


class Seller(AgentExecutor):
    def __init__(self, harness, log, catalog=None):
        self.harness = harness
        self.log = log
        self.memories = {}
        self.locks = {}
        self.catalog = catalog
        self.pending = {}

    async def choose_catalog(self, task_id, text, updater, say):
        messages = self.pending.setdefault(task_id, [])
        messages.append(text)
        if len(messages) > 3 or sum(map(len, messages)) > 6000:
            self.pending.pop(task_id, None)
            await updater.reject(say("Please start a new request with one concise product description."))
            self.log.record("catalog_request_limit", task_id=task_id)
            return None
        observation = {"buyer_descriptions_untrusted": list(messages)}
        need = await self.harness.need(NeedContext(), observation)
        self.log.record("catalog_need", task_id=task_id, need=need.model_dump())
        if need.action == "clarify":
            await updater.requires_input(say("Please clarify: " + need.reason))
            return None
        if need.action == "unsupported":
            self.pending.pop(task_id, None)
            await updater.reject(say("Unavailable in this catalog: " + need.reason))
            return None
        candidates, count = self.catalog.retrieve(need.model_dump())
        public = [product.public() for product in candidates]
        self.log.record("catalog_retrieval", task_id=task_id, catalog_size=len(self.catalog.products),
                        matching_count=count, candidate_count=len(public),
                        candidate_chars=len(json.dumps(public, ensure_ascii=False)),
                        candidate_ids=[row["item_id"] for row in public])
        if not candidates:
            self.pending.pop(task_id, None)
            await updater.reject(say("No catalog product satisfies those requirements."))
            return None
        selection = await self.harness.select(SelectionContext(need.model_dump(), public), observation)
        if selection.action == "clarify":
            await updater.requires_input(say("Please clarify: " + selection.reason))
            return None
        # Re-read the authoritative row, never use a reserve from model output.
        product = self.catalog.products[selection.item_id]
        if not matches(product, need.model_dump()):
            raise HarnessError("selected_inventory_row_violates_constraints")
        self.pending.pop(task_id, None)
        self.log.record("catalog_selected", task_id=task_id, item=product.public(),
                        reserve=product.reserve, reason=selection.reason)
        return product

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            memory = self.memories.get(task.id)
            if memory is not None and memory.terminal:
                raise UnsupportedOperationError("This negotiation has already ended.")
            updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
            text = get_message_text(context.message) or ""
            self.log.record("incoming_message", task_id=task.id, text=text)
            opening = memory is None
            if opening:
                if self.catalog is not None:
                    try:
                        product = await self.choose_catalog(task.id, text, updater, say)
                    except HarnessError as error:
                        self.pending.pop(task.id, None)
                        self.log.record("catalog_failed", task_id=task.id, code=str(error))
                        await updater.failed(say("The catalog decision failed; no transaction was made."))
                        return
                    if product is None:
                        return
                    memory = SellerMemory(product.item_id, product.reserve)
                else:
                    items = set(re.findall(r"\b(?:lamp|chair|bookshelf)\b", text.lower()))
                    if len(items) != 1:
                        await updater.reject(say("Name one supported item: lamp, chair, bookshelf."))
                        self.log.record("unsupported_item_request", task_id=task.id)
                        return
                    item = items.pop()
                    memory = SellerMemory(item, RESERVES[item])
                self.memories[task.id] = memory
            # Baseline wire contract: an entire follow-up message is an integer.
            # Semantic number extraction for long messages is a later challenge.
            offer = int(text.strip()) if not opening and re.fullmatch(r"[0-9]+", text.strip()) else None
            trusted = SellerContext(memory.item_id, memory.reserve,
                                    "opening" if opening else "negotiating", offer)
            try:
                decision = await self.harness.seller(trusted, memory.observation(text))
            except HarnessError as error:
                memory.terminal = True
                self.log.record("negotiation_failed", task_id=task.id, code=str(error))
                await updater.failed(say("The decision service failed; no transaction was made."))
                return
            self.log.record("seller_decision", task_id=task.id, reserve=memory.reserve,
                            buyer_offer=offer, decision=decision.model_dump())
            if decision.action in {"ask", "counter"}:
                prefix = memory.item_id + ": asking" if opening else "Counteroffer:"
                message = say(f"{prefix} {decision.price}")
                if opening and self.catalog is not None:
                    message.parts.append(new_data_part({"selected_item": product.public()}))
                await updater.requires_input(message)
            elif decision.action == "accept":
                receipt = {"seller": "Seochanit Shop", "item": decision.item_id, "price": decision.price}
                await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                await updater.complete(say(f"Deal: {decision.item_id} at {decision.price}"))
                self.log.record("receipt", task_id=task.id, receipt=receipt)
            else:
                await updater.reject(say("The seller ended this negotiation without a sale."))
            memory.apply(text, decision)
            self.log.record("decision_applied", task_id=task.id, action=decision.action,
                            terminal=memory.terminal, recent_turns=len(memory.recent))

    async def cancel(self, context, event_queue):
        task = context.current_task
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            memory = self.memories.get(task.id)
            if memory is not None and memory.terminal:
                raise UnsupportedOperationError("This negotiation has already ended.")
            if memory is not None:
                memory.terminal = True
            self.pending.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
            self.log.record("canceled", task_id=task.id)


def build(port, log_dir, catalog_path=None):
    settings = load_settings()
    log = RunLog(Path(log_dir) / "server-events.jsonl", settings.api_key)
    catalog = Catalog(catalog_path) if catalog_path else None
    log.record("startup", model=settings.model, reserves=RESERVES if catalog is None else None,
               catalog_size=len(catalog.products) if catalog else 3, port=port)
    card = AgentCard(
        name="Seochanit Shop", description=("Describe one furniture need; searches 3000 synthetic products, then negotiates."
                                            if catalog else "Sells lamps, chairs and bookshelves. A model negotiates the price."),
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                             url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id="negotiate", name="Negotiate furniture purchase",
                           description=("Describe one need with material, color, book capacity, max height/width in cm. May ask for clarification. Once selected_item data and an ask appear, offer an integer on the same task."
                                        if catalog else "Name lamp, chair or bookshelf, then offer an integer price on the same task. A counteroffer, receipt or rejection follows."),
                           tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "100"])],
    )
    executor = Seller(ModelHarness(log_dir), log, catalog)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--catalog", type=Path, help="Enable description-based selection from this JSONL inventory")
    args = parser.parse_args()
    log_dir = args.log_dir or ROOT / "logs" / "seller" / datetime.now(timezone.utc).strftime("server-%Y%m%dT%H%M%S.%fZ")
    uvicorn.run(build(args.port, log_dir, args.catalog), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
