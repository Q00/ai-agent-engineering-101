"""Harness-driven seller: opening quotes, counteroffers, receipts, rejection and cancellation."""

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
from uuid import uuid4

import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError, UnsupportedOperationError

from harness import HarnessError, MODELS, parse_response, run_harness, select_harness


ROOT = Path(__file__).resolve().parent
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
LOGGER = logging.getLogger(__name__)


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt, key_path=None):
    """Sign exactly the Round 2 receipt fields using the existing shop identity."""
    body = {field: receipt[field] for field in ("shop", "item", "price", "task")}
    if type(body["price"]) is not int or body["price"] <= 0:
        raise ValueError("Receipt price must be a positive integer before signing")
    if any(not isinstance(body[field], str) or not body[field] for field in ("shop", "item", "task")):
        raise ValueError("Receipt identity fields must be nonempty strings")
    path = Path(key_path) if key_path is not None else ROOT / "shop_key.pem"
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Shop signing key must be Ed25519")
    return {**body, "sig_seller": base64.b64encode(key.sign(canon(body))).decode("ascii")}


def opening_reference(item):
    policy = json.loads((ROOT / "shop_policy.json").read_text(encoding="utf-8"))
    price = policy["opening_reference"][item]
    if type(price) is not int or price < RESERVES[item]:
        raise ValueError("Opening reference must be a whole number at or above reserve")
    return price


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
    return seller_decision(provider, item, buyer_message, task_id, "opening", timeout=timeout)


def validate_followup(decision, reserve, offer):
    parse_response(json.dumps(decision))
    if decision["action"] not in ("ask", "accept", "reject"):
        raise ValueError("Seller must ask, accept, or reject")
    if decision["action"] == "reject":
        return
    if decision["price"] < reserve:
        raise ValueError(f"Price must be at least the reserve {reserve}")
    if decision["action"] == "accept" and decision["price"] != offer:
        raise ValueError("An accepted price must equal the current buyer offer")


def followup_decision(provider, deal, buyer_message, offer, task_id, timeout=90):
    return seller_decision(provider, deal["item"], buyer_message, task_id, "followup",
                           offer=offer, ask=deal["ask"], history=deal["history"], timeout=timeout)


def seller_decision(provider, item, buyer_message, task_id, phase, *, offer=None, ask=None,
                    history=None, timeout=90):
    """Ask a model, retry a validation error once, and never invent a fallback price."""
    instructions = (ROOT / "prompts/seller.txt").read_text(encoding="utf-8")
    context = {"phase": phase, "item": item, "reserve": RESERVES[item],
               "buyer_message": buyer_message, "current_offer": offer, "current_ask": ask,
               "history": history if history is not None else [],
               "opening_reference": opening_reference(item)}
    prompt = instructions + "\nInput data:\n" + json.dumps(context, ensure_ascii=False)
    record = {"task_id": task_id, "phase": phase, "provider": provider, "model": MODELS[provider],
              "item": item, "reserve": RESERVES[item], "attempts": [], "status": "failed"}
    deadline = time.monotonic() + timeout
    try:
        for attempt in range(2):
            entry = {"attempt": attempt + 1, "prompt": prompt}
            record["attempts"].append(entry)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError("Seller decision exceeded its total time limit")
            try:
                decision = run_harness(provider, prompt, timeout=remaining)
                entry["response"] = decision
                if phase == "opening":
                    validate_opening(decision, RESERVES[item])
                else:
                    validate_followup(decision, RESERVES[item], offer)
            except (ValueError, HarnessError) as error:
                entry["error"] = str(error)
                # Retry malformed decisions, but not authentication, process, or network failures.
                validation_error = isinstance(error, ValueError) or isinstance(error.__cause__, ValueError)
                if not validation_error or attempt == 1:
                    raise HarnessError(f"Seller decision failed: {error}") from error
                prompt += ("\nYour previous response was invalid. Error: " + str(error)
                           + "\nReturn a corrected JSON object obeying the original rules.")
                continue
            record["status"] = "ok"
            return decision
        raise HarnessError("No valid seller decision")
    finally:
        log_dir = ROOT / "logs/seller"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = log_dir / (stamp + "-" + uuid4().hex[:8] + ".json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Seller decision log: %s", path)


class Seller(AgentExecutor):
    def __init__(self, provider):
        self.provider = provider
        self.open = {}

    async def execute(self, context, event_queue):
        text = get_message_text(context.message) or ""
        task = context.current_task
        if task is None:
            item = requested_item(text)
            offer = None
            deal = None
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        else:
            deal = self.open.get(task.id)
            if deal is None:
                raise UnsupportedOperationError("This task has no open negotiation.")
            item = deal["item"]
            offer = int(text.strip()) if re.fullmatch(r"[1-9]\d*", text.strip()) else None
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        if deal is not None and offer is None:
            # Raising inside AgentExecutor can terminate the task in the SDK.
            # Keep the conversation open and repeat the last already validated ask.
            message = f"Send your offer as one positive integer. {item}: asking {deal['ask']}"
            await up.requires_input(up.new_agent_message(parts=[new_text_part(message)]))
            return
        await up.start_work()
        try:
            if deal is None:
                decision = await asyncio.to_thread(opening_decision, self.provider, item, text, task.id)
            else:
                decision = await asyncio.to_thread(followup_decision, self.provider, deal, text, offer, task.id)
        except HarnessError as error:
            self.open.pop(task.id, None)
            LOGGER.error("Decision failed for task %s: %s", task.id, error)
            await up.failed(up.new_agent_message(parts=[new_text_part("Unable to obtain a valid seller decision.")]))
            return
        history = [] if deal is None else deal["history"]
        history.extend([{"role": "buyer", "text": text, "offer": offer},
                        {"role": "seller", "decision": decision}])
        say = lambda message: up.new_agent_message(parts=[new_text_part(message)])
        if decision["action"] == "ask":
            self.open[task.id] = {"item": item, "ask": decision["price"], "history": history}
            # The last number is the ask; the private reason is never copied into this text.
            await up.requires_input(say(f"{item}: asking {decision['price']}"))
        elif decision["action"] == "accept":
            try:
                receipt = sign_receipt({"shop": "Harness Furniture Shop", "item": item,
                                        "price": decision["price"], "task": task.id})
            except (OSError, ValueError, TypeError) as error:
                self.open.pop(task.id, None)
                LOGGER.error("Could not sign receipt for task %s: %s", task.id, type(error).__name__)
                await up.failed(say("Receipt signing unavailable; purchase could not be confirmed."))
                return
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal confirmed: {item} at agreed price {decision['price']}. "
                                  "A signed receipt for this task is attached."))
            self.open.pop(task.id, None)
        else:
            await up.reject(say(f"No deal for {item} at your offer {offer}. "
                                f"We could not agree; the last quoted price was {deal['ask']}. "
                                "No purchase was completed."))
            self.open.pop(task.id, None)

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task is None or task.id not in self.open:
            raise TaskNotCancelableError("This task has no open negotiation.")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
        self.open.pop(task.id, None)


def build_app(provider, port):
    card = AgentCard(
        name="Harness Furniture Shop",
        description=f"Negotiates furniture purchases using {provider} ({MODELS[provider]}).",
        version="0.2.0",
        default_input_modes=["text/plain"], default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                             url=f"http://127.0.0.1:{port}/")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then offer a numeric price on the same task.",
                           tags=["shopping", "negotiation"])],
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
