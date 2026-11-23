"""Separate A2A seller for Challenge 1's large natural-language catalog."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from challenge1_catalog import DEFAULT_CATALOG, CatalogError, CatalogIndex, Product
from harness import (CatalogCandidate, CatalogSelectionContext, HarnessError,
                     NegotiationTurn, SellerDecisionContext, decide_catalog_item,
                     decide_seller)


SELLER_NAME = "Atlas Large Catalog Shop"
TERMINAL_STATES = {"completed", "rejected", "canceled"}


def _audit(event: str, **fields: object) -> None:
    """Emit one allow-listed JSON Lines event without model or transport detail."""
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


@dataclass
class CatalogNegotiation:
    product: Product
    candidates: tuple[str, ...]
    ask: int | None = None
    last_offer: int | None = None
    state: str = "opening"
    history: list[NegotiationTurn] = field(default_factory=list)


class Challenge1Seller(AgentExecutor):
    def __init__(self, catalog: CatalogIndex | None = None, candidate_limit: int = 8) -> None:
        self.catalog = catalog or CatalogIndex()
        self.candidate_limit = candidate_limit
        self.negotiations: dict[str, CatalogNegotiation] = {}

    @staticmethod
    def _offer(text: str) -> int | None:
        numbers = re.findall(r"\d+", text)
        return int(numbers[-1]) if numbers else None

    @staticmethod
    def _seller_context(deal: CatalogNegotiation) -> SellerDecisionContext:
        return SellerDecisionContext(
            item=f"{deal.product.name}: {deal.product.description}",
            reserve=deal.product.reserve,
            state=deal.state,
            last_buyer_offer=deal.last_offer,
            history=tuple(deal.history),
        )

    async def _reject(self, updater: TaskUpdater, deal: CatalogNegotiation | None, text: str) -> None:
        if deal is not None:
            deal.state = "rejected"
        await updater.reject(updater.new_agent_message(parts=[new_text_part(text)]))

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
            candidates = self.catalog.search(text, self.candidate_limit)
            if not candidates:
                await self._reject(updater, None, "No catalog candidates matched the request.")
                return
            selection_context = CatalogSelectionContext(
                request=text,
                candidates=tuple(CatalogCandidate(p.id, p.name, p.description) for p in candidates),
            )
            try:
                selection = decide_catalog_item(selection_context)
                product = self.catalog.require_candidate(selection["selected_id"], candidates)
            except (HarnessError, CatalogError):
                await self._reject(updater, None, "Catalog selection failed safely.")
                return
            deal = CatalogNegotiation(product, tuple(p.id for p in candidates))
            deal.history.append(NegotiationTurn("buyer", text))
            self.negotiations[task.id] = deal
            _audit(
                "catalog_selection",
                candidate_count=len(candidates),
                selected_id=product.id,
                reserve=product.reserve,
                task_id=task.id,
            )
            try:
                decision = decide_seller(self._seller_context(deal))
            except HarnessError:
                await self._reject(updater, deal, "Negotiation decision failed safely.")
                return
            if decision["action"] not in {"ask", "counter"} or decision["price"] < product.reserve:
                await self._reject(updater, deal, "Opening decision violated the product contract.")
                return
            deal.ask = decision["price"]
            deal.state = "input_required"
            message = f"Matched {product.name} [{product.id}]. Asking price: {deal.ask}"
            deal.history.append(NegotiationTurn("seller", message, deal.ask))
            await updater.requires_input(updater.new_agent_message(parts=[new_text_part(message)]))
            return

        offer = self._offer(text)
        if offer is None:
            await self._reject(updater, deal, "Buyer offer did not contain a price.")
            return
        deal.last_offer = offer
        deal.state = "negotiating"
        deal.history.append(NegotiationTurn("buyer", text, offer))
        try:
            decision = decide_seller(self._seller_context(deal))
        except HarnessError:
            await self._reject(updater, deal, "Negotiation decision failed safely.")
            return
        action, price = decision["action"], decision["price"]
        if action in {"ask", "counter"}:
            if price < deal.product.reserve:
                await self._reject(updater, deal, "Counteroffer violated the product reserve.")
                return
            deal.ask = price
            deal.state = "input_required"
            await updater.requires_input(updater.new_agent_message(parts=[new_text_part(f"Counteroffer: {price}")]))
            return
        if action == "accept":
            if price != offer or price < deal.product.reserve:
                await self._reject(updater, deal, "Acceptance violated the product contract.")
                return
            receipt = {
                "seller": SELLER_NAME, "product_id": deal.product.id,
                "item": deal.product.name, "price": price,
            }
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            deal.state = "completed"
            await updater.complete(updater.new_agent_message(parts=[new_text_part(f"Deal accepted at {price}")]))
            _audit(
                "trade_completed",
                product_id=receipt["product_id"],
                price=receipt["price"],
                task_id=task.id,
                a2a_state="TASK_STATE_COMPLETED",
            )
            return
        await self._reject(updater, deal, "Offer rejected.")

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


def build_card(port: int):
    return AgentCard(
        name=SELLER_NAME,
        description="Searches a large catalog from natural-language needs and negotiates the selected item.",
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(id="catalog-negotiate", name="Find and negotiate a product",
                           description="Describe a need without naming a product.",
                           tags=["catalog", "search", "negotiation"],
                           examples=["something wooden to keep about 40 books, not taller than my desk"])],
    )


def build_app(port: int, executor: Challenge1Seller | None = None) -> Starlette:
    card = build_card(port)
    handler = DefaultRequestHandler(agent_executor=executor or Challenge1Seller(),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    uvicorn.run(build_app(args.port, Challenge1Seller(CatalogIndex(args.catalog))),
                host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
