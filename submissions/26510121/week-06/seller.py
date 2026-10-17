"""A2A v1.0 seller: model chooses, contracts validate, adapter publishes.

Start with: python seller.py --port 9999
The fixed three-item catalog is the baseline; the large-catalog challenge follows.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
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


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


class Seller(AgentExecutor):
    def __init__(self, harness, log):
        self.harness = harness
        self.log = log
        self.memories = {}
        self.locks = {}

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
                await updater.requires_input(say(f"{prefix} {decision.price}"))
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
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
            self.log.record("canceled", task_id=task.id)


def build(port, log_dir):
    settings = load_settings()
    log = RunLog(Path(log_dir) / "server-events.jsonl", settings.api_key)
    log.record("startup", model=settings.model, reserves=RESERVES, port=port)
    card = AgentCard(
        name="Seochanit Shop", description="Sells lamps, chairs and bookshelves. A model negotiates the price.",
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                             url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id="negotiate", name="Negotiate furniture purchase",
                           description="Name lamp, chair or bookshelf, then offer an integer price on the same task. A counteroffer, receipt or rejection follows.",
                           tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "100"])],
    )
    executor = Seller(ModelHarness(log_dir), log)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args()
    log_dir = args.log_dir or ROOT / "logs" / "seller" / datetime.now(timezone.utc).strftime("server-%Y%m%dT%H%M%S.%fZ")
    uvicorn.run(build(args.port, log_dir), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
