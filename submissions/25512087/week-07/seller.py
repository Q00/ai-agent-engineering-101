"""A2A v1 shop for the week-07 market: signed receipts, instant rule-based answers, adaptive asks."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import CommandHarness
from policy import SellerPolicy
from catalog import CatalogIndex
from challenge_catalog import select_catalog_product
from negotiation import (FastSellerPolicy, counter_message, end_message, find_item, opening_message, opening_offer,
                         parse_offer, parse_price, says_accept)
from pricing import AdaptivePricer


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def canon(value: dict[str, object]) -> bytes:
    """Encode the exact canonical JSON representation required by the market."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def sign_receipt(
    receipt: dict[str, object],
    key_path: str | Path = "shop_key.pem",
) -> dict[str, object]:
    """Attach an Ed25519 signature over the market's four receipt fields."""
    key = serialization.load_pem_private_key(
        Path(key_path).read_bytes(),
        password=None,
    )
    body = {
        key_name: receipt[key_name]
        for key_name in ("shop", "item", "price", "task")
    }
    signed = dict(receipt)
    signature = key.sign(canon(body))
    signed["sig_seller"] = base64.b64encode(signature).decode()
    try:                                   # Round 2 counts only receipts that verify: check before sending
        key.public_key().verify(signature, canon(body))
        if not isinstance(body["price"], int) or isinstance(body["price"], bool):
            raise ValueError(f"price {body['price']!r} is not a whole number")
    except Exception as error:             # noqa: BLE001 - log every reason a receipt would not count
        print(json.dumps({"event": "receipt_self_check_failed", "error": str(error), **body},
                         ensure_ascii=False), file=sys.stderr, flush=True)
    return signed


CARD_NAME = "Ggari Furniture · Instant Signed Deals"


def build_card(port: int, name: str = "Harness Furniture Shop", catalog_size: int = 3) -> AgentCard:
    """Public Agent Card, same text as the Worker shop: no prices on it (they follow the market)."""
    description = (
        "Lamps, chairs and bookshelves at fair market prices, closed fast. We scan this market's counted "
        "deals every hour, and again the moment another shop's share jumps, so our opening ask always stays "
        "within the range this market's deals actually close at. Every message is answered in milliseconds by "
        "rule-based code: an offer at our current price closes in that one message, a counter never goes above "
        "our opening ask, and we never sign below the market floor. Every receipt is Ed25519-signed over "
        "{shop, item, price, task} and shows exactly the agreed price, so your deal counts in Round 2."
    )
    if catalog_size > 3:
        description = f"A harness-driven shop with {catalog_size} searchable products."
    return AgentCard(
        name=CARD_NAME if name != "Harness Furniture Shop" else name,
        description=description,
        version="1.3.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://127.0.0.1:{port}/",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate-furniture",
                name="Buy a lamp, chair or bookshelf, fast",
                description=("Name the item; our ask is the last number in each reply. Send a number to offer, "
                             "or say ok to take the ask and get a signed receipt at once."),
                tags=["shopping", "furniture", "lamp", "chair", "bookshelf", "fast", "signed-receipt",
                      "market-price"],
                examples=["I want to buy a chair", "ok"],
            )
        ],
    )


class SellerAgent(AgentExecutor):
    """Rule-based shop: answers every turn at once, never below reserve, prices adapt to its own trades.

    With `harness_policy` set (--use-harness) the old harness-driven SellerPolicy decides counters
    instead; an offer at or above reserve is still accepted in code without waiting for the harness.
    """

    STALE_SECONDS = 180      # an open negotiation with no message for this long counts as lost
    MAX_TEXT = 4000          # characters of one buyer message (LLM buyers can be wordy)
    IMPLAUSIBLE_FACTOR = 2   # an offer above 2x our ask is treated as a typo, not accepted
    MAX_ROUNDS = 6           # stop long haggles; should_end usually closes a dead end sooner

    def __init__(
        self,
        policy: SellerPolicy | None,
        catalog: CatalogIndex | None = None,
        name: str = "Harness Furniture Shop",
        pricer: AdaptivePricer | None = None,
        harness_policy: bool = False,
    ) -> None:
        self.policy = policy
        self.catalog = catalog
        self.name = name
        self.pricer = pricer or AdaptivePricer(RESERVES)
        self.fast = FastSellerPolicy()
        self.harness_policy = harness_policy and policy is not None
        self.open: dict[str, dict[str, Any]] = {}

    def _sweep(self) -> None:
        now = time.time()
        for task_id, deal in list(self.open.items()):
            if now - float(deal["seen"]) > self.STALE_SECONDS:
                self.open.pop(task_id, None)
                if deal["item"] in RESERVES:
                    self.pricer.lost(str(deal["item"]), list(deal["offers"]))
                self._log(task_id, "lost", item=deal["item"], offers=deal["offers"])

    async def _open(self, task, text: str, updater: TaskUpdater, say) -> None:
        item = find_item(text)
        product_id = item
        if item is not None:
            reserve = RESERVES[item]
            ask = self.pricer.ask(item)
            self.pricer.opened(item)
        elif self.catalog is not None and self.policy is not None:
            selected, candidates = await asyncio.to_thread(
                select_catalog_product, text, self.catalog, self.policy.harness)
            item, product_id, reserve = selected.name, selected.product_id, selected.reserve
            ask = math.ceil(reserve * 1.15)
            self._log(task.id, "catalog_retrieval", query=text,
                      candidates=[m.product.product_id for m in candidates], selected=selected.product_id)
        else:
            await updater.reject(say("We sell lamps, chairs and bookshelves. Name one of them to start."))
            return
        deal = {"item": item, "product_id": product_id, "reserve": reserve, "ask": ask,
                "opening_ask": ask, "rounds": 0, "offers": [], "seen": time.time()}
        self.open[task.id] = deal
        self._log(task.id, "opening", item=item, ask=ask, reserve=reserve)
        named = opening_offer(text)
        if named is not None and ask <= named <= self.IMPLAUSIBLE_FACTOR * max(ask, reserve):   # "a lamp for 110"
            deal["offers"].append(named)
            await self._accept(task, deal, named, updater, say)
            return
        if named is not None and reserve <= named < ask:                 # first offer under our ask: one counter
            deal["offers"].append(named)
            counter = self.fast.respond(reserve, ask, named, 0).price
            deal["rounds"], deal["ask"] = 1, counter
            self._log(task.id, "response", item=item, offer=named, reserve=reserve, decision="counter",
                      decision_price=counter)
            await updater.requires_input(say(counter_message(named, counter, reserve, item)))
            return
        if named is not None and 0 < named < reserve:                    # "a lamp for 95": show the floor now
            deal["offers"].append(named)
            deal["rounds"], deal["floor_quotes"], deal["ask"] = 1, 1, reserve
            self._log(task.id, "response", item=item, offer=named, reserve=reserve, decision="counter",
                      decision_price=reserve)
            await updater.requires_input(say(counter_message(named, reserve, reserve, item)))
            return
        await updater.requires_input(say(opening_message(item, ask, reserve)))

    async def _accept(self, task, deal: dict[str, Any], price: int, updater: TaskUpdater, say) -> None:
        item = str(deal["item"])
        receipt = sign_receipt({"shop": self.name, "item": item, "price": int(price), "task": task.id})
        await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await updater.complete(say(f"Deal: {item} at {int(price)}. Signed receipt attached."))
        self.open.pop(task.id, None)
        if item in RESERVES:
            self.pricer.deal(item, int(price), int(deal["opening_ask"]), int(deal["rounds"]))
        self._log(task.id, "deal", item=item, price=int(price), rounds=deal["rounds"])

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        if len(text) > self.MAX_TEXT:
            await updater.reject(say(f"Message longer than {self.MAX_TEXT} characters."))
            self.open.pop(task.id, None)
            return
        self._sweep()
        deal = self.open.get(task.id)
        if deal is None:
            await self._open(task, text, updater, say)
            return

        deal["seen"] = time.time()
        reserve, ask, rounds = int(deal["reserve"]), int(deal["ask"]), int(deal["rounds"])
        offer, problem = parse_price(text)
        if problem:                                         # "185,000", "-5": never guess what was meant
            self._log(task.id, "price_refused", text=text[:80], problem=problem)
            await updater.requires_input(say(
                f"We could not read that price ({problem}). Send your offer as a plain whole number, "
                f"for example {ask}. Current ask is {ask}."))
            return
        if offer is not None and offer > self.IMPLAUSIBLE_FACTOR * max(ask, reserve):   # 1850 for a 185 chair
            self._log(task.id, "price_refused", offer=offer, problem="far above ask")
            await updater.requires_input(say(
                f"That offer ({offer}) is far above our ask, so we did not take it. "
                f"Send the price you mean as a plain number. Current ask is {ask}."))
            return
        if offer is None:
            if says_accept(text):                               # "ok, deal" without a number: take the ask
                await self._accept(task, deal, ask, updater, say)
                return
            await updater.requires_input(say(f"Current ask is {ask}. Send your offer as a number."))
            return
        deal["offers"].append(offer)

        if self.harness_policy and offer < reserve:
            decision = await asyncio.to_thread(
                self.policy.respond, str(deal["item"]), reserve, ask, offer, rounds)
            if decision.action == "reject":                     # never drop a buyer who may still climb
                decision = self.fast.respond(reserve, ask, offer, rounds)
        else:
            decision = self.fast.respond(reserve, ask, offer, rounds)
        self._log(task.id, "response", item=deal["item"], offer=offer, reserve=reserve,
                  decision=decision.action, decision_price=decision.price)

        if decision.action == "accept" and offer >= reserve:
            await self._accept(task, deal, int(decision.price or offer), updater, say)
            return
        deal["rounds"] = rounds + 1
        if (deal["rounds"] >= self.MAX_ROUNDS
                or self.fast.should_end(reserve, list(deal["offers"]), int(deal.get("floor_quotes", 0)))):
            self.open.pop(task.id, None)
            if deal["item"] in RESERVES:
                self.pricer.lost(str(deal["item"]), list(deal["offers"]))
            self._log(task.id, "ended", item=deal["item"], offers=deal["offers"])
            await updater.reject(say(end_message(str(deal["item"]), reserve)))
            return
        deal["ask"] = max(reserve, min(ask, int(decision.price or reserve)))
        if deal["ask"] <= reserve:
            deal["floor_quotes"] = int(deal.get("floor_quotes", 0)) + 1
        await updater.requires_input(say(counter_message(offer, deal["ask"], reserve, str(deal["item"]))))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            return
        self.open.pop(task.id, None)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await updater.cancel()

    @staticmethod
    def _log(task_id: str, event: str, **values: object) -> None:
        print(
            json.dumps(
                {"event": event, "task_id": task_id, **values},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )


def build_app(
    port: int,
    harness_command: str | None = None,
    name: str = "Harness Furniture Shop",
    catalog_path: Path | None = None,
    use_harness: bool = False,
) -> Starlette:
    catalog = CatalogIndex.load(catalog_path) if catalog_path else None
    card = build_card(port, name=name, catalog_size=len(catalog.products) if catalog else 3)
    policy = SellerPolicy(CommandHarness(harness_command))
    handler = DefaultRequestHandler(
        agent_executor=SellerAgent(policy, catalog=catalog, name=name, harness_policy=use_harness),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--harness-cmd")
    parser.add_argument("--name", default="Harness Furniture Shop")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--use-harness", action="store_true",
                        help="let the harness choose counters below reserve (slower; default is rule-based)")
    args = parser.parse_args()
    uvicorn.run(
        build_app(args.port, args.harness_cmd, args.name, args.catalog, args.use_harness),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
