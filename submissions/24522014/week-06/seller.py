"""STEP 3 seller: Agent Card and a model-driven opening quote; no follow-up trades yet."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.errors import InvalidParamsError, UnsupportedOperationError

from harness import HarnessError, MODELS, parse_response, run_harness, select_harness


ROOT = Path(__file__).resolve().parent
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
LOGGER = logging.getLogger(__name__)


def requested_item(text):
    matches = [item for item in RESERVES if re.search(r"\b" + item + r"\b", text.lower())]
    if len(matches) != 1:
        raise InvalidParamsError("Name exactly one item: lamp, chair, or bookshelf.")
    return matches[0]


def validate_opening(decision, reserve):
    parse_response(json.dumps(decision))
    if decision["action"] != "ask":
        raise ValueError("The first response must ask for a price, not accept or reject")
    if decision["price"] < reserve:
        raise ValueError(f"Opening price must be at least the reserve {reserve}")


def opening_decision(provider, item, buyer_message, task_id, timeout=90):
    """Ask a model, retry a validation error once, and never invent a fallback price."""
    instructions = (ROOT / "prompts/seller.txt").read_text(encoding="utf-8")
    context = {"phase": "opening", "item": item, "reserve": RESERVES[item],
               "buyer_message": buyer_message, "history": []}
    prompt = instructions + "\nInput data:\n" + json.dumps(context, ensure_ascii=False)
    record = {"task_id": task_id, "provider": provider, "model": MODELS[provider],
              "item": item, "reserve": RESERVES[item], "attempts": [], "status": "failed"}
    deadline = time.monotonic() + timeout
    try:
        for attempt in range(2):
            entry = {"attempt": attempt + 1, "prompt": prompt}
            record["attempts"].append(entry)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError("Opening decision exceeded its total time limit")
            try:
                decision = run_harness(provider, prompt, timeout=remaining)
                entry["response"] = decision
                validate_opening(decision, RESERVES[item])
            except (ValueError, HarnessError) as error:
                entry["error"] = str(error)
                # Retry malformed decisions, but not authentication, process, or network failures.
                validation_error = isinstance(error, ValueError) or isinstance(error.__cause__, ValueError)
                if not validation_error or attempt == 1:
                    raise HarnessError(f"Opening decision failed: {error}") from error
                prompt += ("\nYour previous response was invalid. Error: " + str(error)
                           + "\nReturn a corrected JSON object obeying the original rules.")
                continue
            record["status"] = "ok"
            return decision
        raise HarnessError("No valid opening decision")
    finally:
        log_dir = ROOT / "logs/seller"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = log_dir / (stamp + "-" + uuid4().hex[:8] + ".json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Opening decision log: %s", path)


class Seller(AgentExecutor):
    def __init__(self, provider):
        self.provider = provider
        self.open = {}

    async def execute(self, context, event_queue):
        if context.current_task is not None:
            raise UnsupportedOperationError("Follow-up negotiation is not implemented in this step.")
        text = get_message_text(context.message) or ""
        item = requested_item(text)
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await up.start_work()
        try:
            decision = await asyncio.to_thread(opening_decision, self.provider, item, text, task.id)
        except HarnessError as error:
            LOGGER.error("Opening failed for task %s: %s", task.id, error)
            await up.failed(up.new_agent_message(parts=[new_text_part("Unable to obtain a valid asking price.")]))
            return
        self.open[task.id] = {"item": item, "ask": decision["price"],
                              "history": [{"role": "buyer", "text": text},
                                          {"role": "seller", "decision": decision}]}
        # The test reads the LAST number as the ask; keep the model's reason out of this text.
        message = f"{item}: asking {decision['price']}"
        await up.requires_input(up.new_agent_message(parts=[new_text_part(message)]))

    async def cancel(self, context, event_queue):
        raise UnsupportedOperationError("Cancellation is not implemented in this step.")


def build_app(provider, port):
    card = AgentCard(
        name="Harness Furniture Shop",
        description=f"Opening quotes for furniture using {provider} ({MODELS[provider]}).",
        version="0.1.0",
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                             url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id="opening_quote", name="Quote a first price",
                           description="Ask for one lamp, chair, or bookshelf.", tags=["shopping"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(provider), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["claude", "codex", "mixed"], required=True)
    parser.add_argument("--seller-harness", choices=list(MODELS), default="claude")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    provider = select_harness(args.mode, "seller", seller=args.seller_harness)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(provider, args.port), host="127.0.0.1", port=args.port)
