"""Configurable A2A seller used only for the required multi-shop challenge."""
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

from harness import HarnessError, NegotiationTurn, SellerDecisionContext, decide_seller


TERMINAL_STATES = {"completed", "rejected", "canceled"}


class ShopConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShopConfig:
    port: int
    name: str
    inventory: dict[str, int]


@dataclass
class ShopNegotiation:
    item: str
    reserve: int
    last_offer: int | None = None
    state: str = "opening"
    history: list[NegotiationTurn] = field(default_factory=list)


def audit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def load_shop(path: Path, port: int) -> ShopConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    shops = raw.get("shops") if isinstance(raw, dict) else None
    if not isinstance(shops, list):
        raise ShopConfigError("shop configuration has no shops list")
    matches = [shop for shop in shops if isinstance(shop, dict) and shop.get("port") == port]
    if len(matches) != 1:
        raise ShopConfigError("port must identify exactly one shop")
    shop = matches[0]
    name, inventory = shop.get("name"), shop.get("inventory")
    if not isinstance(name, str) or not name or not isinstance(inventory, dict) or not inventory:
        raise ShopConfigError("shop name and inventory are required")
    checked: dict[str, int] = {}
    for item, reserve in inventory.items():
        if not isinstance(item, str) or not item or not isinstance(reserve, int) or isinstance(reserve, bool) or reserve <= 0:
            raise ShopConfigError("inventory entries require a name and positive integer reserve")
        checked[item.lower()] = reserve
    return ShopConfig(port, name, checked)


def build_card(shop: ShopConfig) -> AgentCard:
    products = sorted(shop.inventory)
    return AgentCard(
        name=shop.name,
        description=f"Negotiates these products: {', '.join(products)}.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(
            protocol_binding="JSONRPC", url=f"http://127.0.0.1:{shop.port}/", protocol_version="1.0"
        )],
        skills=[AgentSkill(
            id="negotiate", name=f"Buy from {shop.name}",
            description=f"Sells and negotiates {', '.join(products)}.",
            tags=products, examples=[f"I want to buy a {products[0]}"],
        )],
    )


class Challenge2Seller(AgentExecutor):
    def __init__(self, shop: ShopConfig) -> None:
        self.shop = shop
        self.negotiations: dict[str, ShopNegotiation] = {}

    def _item(self, text: str) -> str | None:
        lowered = text.lower()
        return next((item for item in self.shop.inventory if item in lowered), None)

    @staticmethod
    def _offer(text: str) -> int | None:
        numbers = re.findall(r"\d+", text)
        return int(numbers[-1]) if numbers else None

    @staticmethod
    def _context(deal: ShopNegotiation) -> SellerDecisionContext:
        return SellerDecisionContext(
            item=deal.item, reserve=deal.reserve, state=deal.state,
            last_buyer_offer=deal.last_offer, history=tuple(deal.history),
        )

    async def _reject(self, updater: TaskUpdater, deal: ShopNegotiation | None, text: str) -> None:
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
            item = self._item(text)
            if item is None:
                await self._reject(updater, None, "Requested item is not sold here.")
                return
            deal = ShopNegotiation(item, self.shop.inventory[item])
            deal.history.append(NegotiationTurn("buyer", text))
            self.negotiations[task.id] = deal
            audit("negotiation_started", shop=self.shop.name, item=item,
                  reserve=deal.reserve, task_id=task.id)
        else:
            offer = self._offer(text)
            if offer is None:
                await self._reject(updater, deal, "Buyer offer did not contain a price.")
                return
            deal.last_offer = offer
            deal.state = "negotiating"
            deal.history.append(NegotiationTurn("buyer", text, offer))

        try:
            decision = decide_seller(self._context(deal))
        except HarnessError:
            await self._reject(updater, deal, "Negotiation decision failed safely.")
            return
        action, price = decision["action"], decision["price"]
        if action in {"ask", "counter"}:
            if price < deal.reserve:
                await self._reject(updater, deal, "Price violated the reserve.")
                return
            deal.state = "input_required"
            message = f"{deal.item} asking price: {price}" if deal.last_offer is None else f"Counteroffer: {price}"
            deal.history.append(NegotiationTurn("seller", message, price))
            await updater.requires_input(updater.new_agent_message(parts=[new_text_part(message)]))
            return
        if action == "accept":
            if deal.last_offer is None or price != deal.last_offer or price < deal.reserve:
                await self._reject(updater, deal, "Acceptance violated the price contract.")
                return
            receipt = {"seller": self.shop.name, "item": deal.item, "price": price}
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            deal.state = "completed"
            await updater.complete(updater.new_agent_message(parts=[new_text_part(f"Deal accepted at {price}")]))
            audit("trade_completed", shop=self.shop.name, item=deal.item, price=price,
                  task_id=task.id, a2a_state="TASK_STATE_COMPLETED")
            return
        await self._reject(updater, deal, "Offer rejected.")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            raise RuntimeError("Cannot cancel a missing task")
        deal = self.negotiations.get(task.id)
        if deal is not None:
            deal.state = "canceled"
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(shop: ShopConfig) -> Starlette:
    card = build_card(shop)
    handler = DefaultRequestHandler(
        agent_executor=Challenge2Seller(shop), task_store=InMemoryTaskStore(), agent_card=card
    )
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run one configured Challenge 2 shop")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("challenge2_shops.json"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    shop = load_shop(args.config, args.port)
    uvicorn.run(build_app(shop), host="127.0.0.1", port=shop.port, log_level="warning")


if __name__ == "__main__":
    main()
