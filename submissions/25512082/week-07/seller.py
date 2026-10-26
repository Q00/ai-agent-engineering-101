"""Model-driven Week 06 seller exposed as an A2A v1.0 JSON-RPC server."""
from __future__ import annotations

import argparse
import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import HarnessError, NegotiationTurn, SellerDecisionContext, decide_seller
from protocol import RESERVES

SELLER_NAME = "Atlas Shop"
TERMINAL_STATES = {"completed", "rejected", "canceled"}


def canon(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt: dict, key_path: str | Path = "shop_key.pem") -> dict:
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    body = {name: receipt[name] for name in ("shop", "item", "price", "task")}
    signed = dict(receipt)
    signed["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return signed


@dataclass
class Negotiation:
    item: str
    reserve: int
    ask: int | None = None
    last_offer: int | None = None
    history: list[NegotiationTurn] = field(default_factory=list)
    rounds: int = 0
    state: str = "new"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Week 06 A2A seller")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def build_card(port: int) -> AgentCard:
    url = f"http://127.0.0.1:{port}/"
    return AgentCard(
        name=SELLER_NAME,
        description="Sells lamps, chairs, and bookshelves and negotiates each price.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Name an item, then negotiate until a deal or rejection.",
                           tags=["shopping", "negotiation"],
                           examples=["I want to buy a lamp", "I offer 110"])],
    )


class Seller(AgentExecutor):
    """A2A transport and state layer; Codex makes every pricing decision."""

    def __init__(self) -> None:
        self.negotiations: dict[str, Negotiation] = {}

    @staticmethod
    def _item(text: str) -> str | None:
        lowered = text.lower()
        return next((item for item in RESERVES if item in lowered), None)

    @staticmethod
    def _offer(text: str) -> int | None:
        numbers = re.findall(r"\d+", text)
        return int(numbers[-1]) if numbers else None

    @staticmethod
    def _context(deal: Negotiation) -> SellerDecisionContext:
        return SellerDecisionContext(item=deal.item, reserve=deal.reserve, state=deal.state,
                                     last_buyer_offer=deal.last_offer, history=tuple(deal.history))

    async def _safe_reject(self, updater: TaskUpdater, deal: Negotiation | None, message: str) -> None:
        if deal is not None:
            deal.state = "rejected"
            deal.history.append(NegotiationTurn("seller", message))
        await updater.reject(updater.new_agent_message(parts=[new_text_part(message)]))

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        deal = self.negotiations.get(task.id)

        if deal is not None and deal.state in TERMINAL_STATES:
            raise RuntimeError("The negotiation is already terminal")

        if deal is None:
            item = self._item(text)
            if item is None:
                await self._safe_reject(updater, None, "Requested item is not sold here.")
                return
            deal = Negotiation(item=item, reserve=RESERVES[item], state="opening")
            deal.history.append(NegotiationTurn("buyer", text))
            self.negotiations[task.id] = deal
            try:
                decision = decide_seller(self._context(deal))
            except HarnessError:
                await self._safe_reject(updater, deal, "Negotiation decision failed safely.")
                return
            if decision["action"] not in {"ask", "counter"}:
                await self._safe_reject(updater, deal, "Opening decision was invalid.")
                return
            ask = decision["price"]
            if ask < deal.reserve:
                await self._safe_reject(updater, deal, "Opening price violated the reserve.")
                return
            deal.ask = ask
            deal.state = "input_required"
            message = f"{deal.item} asking price: {ask}"
            deal.history.append(NegotiationTurn("seller", message, ask))
            await updater.requires_input(updater.new_agent_message(parts=[new_text_part(message)]))
            return

        offer = self._offer(text)
        if offer is None:
            await self._safe_reject(updater, deal, "Buyer offer did not contain a price.")
            return
        deal.last_offer = offer
        deal.rounds += 1
        deal.state = "negotiating"
        deal.history.append(NegotiationTurn("buyer", text, offer))
        try:
            decision = decide_seller(self._context(deal))
        except HarnessError:
            await self._safe_reject(updater, deal, "Negotiation decision failed safely.")
            return

        action, price = decision["action"], decision["price"]
        if action in {"ask", "counter"}:
            if price < deal.reserve:
                await self._safe_reject(updater, deal, "Counteroffer violated the reserve.")
                return
            deal.ask = price
            deal.state = "input_required"
            message = f"Counteroffer: {price}"
            deal.history.append(NegotiationTurn("seller", message, price))
            await updater.requires_input(updater.new_agent_message(parts=[new_text_part(message)]))
            return
        if action == "accept":
            if price != offer or price < deal.reserve:
                await self._safe_reject(updater, deal, "Acceptance violated the price contract.")
                return
            receipt = sign_receipt({
                "shop": SELLER_NAME,
                "item": deal.item,
                "price": int(price),
                "task": task.id,
            })
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            message = f"Deal accepted at {price}"
            deal.history.append(NegotiationTurn("seller", message, price))
            deal.state = "completed"
            await updater.complete(updater.new_agent_message(parts=[new_text_part(message)]))
            return
        if action == "reject":
            await self._safe_reject(updater, deal, "Offer rejected.")
            return
        await self._safe_reject(updater, deal, "Seller decision was invalid.")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            raise RuntimeError("Cannot cancel a missing task")
        deal = self.negotiations.get(task.id)
        if deal is not None:
            if deal.state in TERMINAL_STATES:
                raise RuntimeError("The negotiation is already terminal")
            deal.state = "canceled"
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port: int, executor: Seller | None = None) -> Starlette:
    card = build_card(port)
    handler = DefaultRequestHandler(agent_executor=executor or Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
