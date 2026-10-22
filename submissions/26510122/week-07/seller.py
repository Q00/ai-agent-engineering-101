"""A2A v1.0 shop whose asks and negotiation decisions come from Codex."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
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

from harness import decide, trace
from catalog import Catalog
from pricing import PricingRecorder
from pricing_experiment import assign_price
from pricing_simulator import candidate_price
from pricing_ucb import evaluate_ucb
from receipt import sign_receipt
from time_sale import CARD_NOTICE, choose_hourly_price


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


class Seller(AgentExecutor):
    def __init__(self, name: str, reserves: dict[str, int], catalog: Catalog | None = None,
                 recorder: PricingRecorder | None = None, hourly_sale: bool = False) -> None:
        self.name = name
        self.reserves = reserves
        self.catalog = catalog
        self.open: dict[str, dict] = {}
        self.recorder = recorder
        self.hourly_sale = hourly_sale

    def observe(self, method: str, *args: object) -> None:
        """A telemetry failure must never change a negotiation or receipt."""
        if self.recorder is None:
            return
        try:
            getattr(self.recorder, method)(*args)
        except Exception as error:
            trace("pricing_record_error", method=method, error=str(error))

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        message = get_message_text(context.message) or ""
        source_message_id = context.message.message_id or None
        deal = self.open.get(task.id)

        if deal is None:
            if self.catalog:
                try:
                    product = await asyncio.to_thread(self.catalog.choose, message)
                except ValueError:
                    await updater.reject(say("No matching product in this catalog."))
                    return
                item, reserve = product["name"], product["reserve"]
                product_id = product["id"]
            else:
                item = next((name for name in self.reserves if re.search(rf"\b{name}\b", message.lower())), None)
                if item is None:
                    await updater.reject(say("We sell: " + ", ".join(self.reserves) + "."))
                    return
                reserve, product_id = self.reserves[item], item
            planned_ask = None
            selection_mode = "legacy"
            local_at = None
            if self.hourly_sale:
                arm, planned_ask, local_at = choose_hourly_price(reserve)
                selection_mode = "hourly_sale" if arm == "hourly_sale_1.06" else "hourly_regular"
            else:
                try:
                    arm, planned_ask = assign_price(
                        task.id, item, reserve, int(os.environ.get("WEEK07_PRICE_EXPERIMENT_PERCENT", "0")))
                except ValueError as error:
                    trace("pricing_experiment_error", task_id=task.id, error=str(error))
                    arm = "legacy_cap_1.12"
            if planned_ask is not None and self.recorder is not None:
                if not self.hourly_sale:
                    selection_mode = "randomized"
                if not self.hourly_sale and os.environ.get("WEEK07_PRICE_UCB_AUTO") == "1":
                    try:
                        if self.recorder.reconciliation_fresh():
                            evaluation = evaluate_ucb(self.recorder.all_trades(), item)
                            if evaluation["ready"]:
                                arm = evaluation["recommended_arm"]
                                planned_ask = candidate_price(reserve, arm)
                                selection_mode = "ucb"
                                trace("pricing_ucb_selection", task_id=task.id, item=item,
                                      arm=arm, scores=evaluation["scores"])
                    except Exception as error:
                        trace("pricing_ucb_error", task_id=task.id, error=str(error))
                try:
                    self.recorder.start(task.id, item, reserve, arm)
                except Exception as error:
                    trace("pricing_record_error", method="start_experiment", error=str(error))
                    if not self.hourly_sale:
                        planned_ask = None
                    self.observe("start", task.id, item, reserve)
            else:
                if not self.hourly_sale:
                    planned_ask = None
                self.observe("start", task.id, item, reserve)
            self.observe("model_call", task.id)
            try:
                answer = await asyncio.to_thread(decide, "seller", {
                    "phase": "opening", "item": item, "private_reserve": reserve,
                    "buyer_message": message,
                    "objective": "Choose a reasonable opening ask near the reserve. A buyer with a modest budget should have a path to a deal.",
                }, ["ask"])
            except Exception as error:
                self.observe("close", task.id, "INFRA_ERROR", f"opening model call: {type(error).__name__}")
                raise
            ask = max(reserve, int(answer["price"]))
            if ask != answer["price"]:
                trace("price_guard", task_id=task.id, reason="ask below reserve",
                      proposed=answer["price"], effective=ask)
            ceiling = math.ceil(reserve * 1.12)
            policy_file = Path("pricing-policy.json")
            if policy_file.is_file():
                policy = json.loads(policy_file.read_text(encoding="utf-8"))
                ceiling = math.ceil(reserve * float(policy["opening_max_reserve_multiplier"]))
                original = ask
                ask = min(ask, ceiling)
                trace("pricing_policy", task_id=task.id, item=item, original_ask=original,
                      effective_ask=ask, ceiling=ceiling, feedback=policy["feedback"],
                      reason=policy["reason"])
            if planned_ask is not None:
                if not reserve <= planned_ask <= ceiling:
                    raise ValueError("planned ask violates the current price policy")
                trace("hourly_price" if self.hourly_sale else "pricing_experiment",
                      task_id=task.id, item=item, arm=arm, selection_mode=selection_mode,
                      model_policy_ask=ask, planned_ask=planned_ask, local_at=local_at)
                ask = planned_ask
            self.open[task.id] = {"item": item, "product_id": product_id, "reserve": reserve, "ask": ask, "history": []}
            trace("seller_open", task_id=task.id, item=item, product_id=product_id, reserve=reserve, ask=ask)
            await updater.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            self.observe("opening_sent", task.id, ask)
            return

        offered = re.fullmatch(r"\s*(\d+)\s*", message)
        if not offered:
            await updater.requires_input(say(f"Please send one whole-number offer. Current ask: {deal['ask']}"))
            return
        offer = int(offered.group(1))
        self.observe("offer_received", task.id, offer, source_message_id)
        deal["history"].append({"buyer_offer": offer, "seller_ask": deal["ask"]})
        self.observe("model_call", task.id)
        try:
            answer = await asyncio.to_thread(decide, "seller", {
                "phase": "follow_up", "item": deal["item"], "private_reserve": deal["reserve"],
                "current_ask": deal["ask"], "buyer_offer": offer, "history": deal["history"],
                "objective": "Accept a fair offer at or above reserve promptly. Otherwise counter above reserve or reject if agreement is hopeless.",
            }, ["deal", "counter", "reject"])
        except Exception as error:
            self.observe("close", task.id, "INFRA_ERROR", f"follow-up model call: {type(error).__name__}")
            raise
        action = answer["action"]
        if action == "deal" and offer < deal["reserve"]:
            trace("price_guard", task_id=task.id, reason="deal below reserve", offered=offer)
            action = "counter"
            answer["price"] = deal["reserve"]
        if action == "deal":
            signed = sign_receipt(self.name, deal["item"], offer, task.id)
            await updater.add_artifact(parts=[new_data_part(signed)], name="receipt")
            self.observe("receipt", task.id, offer)
            await updater.complete(say(f"Deal: {deal['item']} at {offer}."))
            self.observe("complete", task.id)
            trace("seller_deal", task_id=task.id, item=deal["item"], price=offer, receipt=signed)
            del self.open[task.id]
            return
        if action == "reject":
            await updater.reject(say(f"No deal for {deal['item']} at {offer}."))
            self.observe("close", task.id, "REJECTED", "seller explicitly rejected the offer")
            trace("seller_reject", task_id=task.id, item=deal["item"], offer=offer)
            del self.open[task.id]
            return
        proposed = answer["price"]
        ask = max(deal["reserve"], int(proposed))
        if ask != proposed:
            trace("price_guard", task_id=task.id, reason="counter below reserve", proposed=proposed, effective=ask)
        deal["ask"] = ask
        trace("seller_counter", task_id=task.id, item=deal["item"], offer=offer, ask=ask)
        await updater.requires_input(say(f"Counteroffer: {ask}."))
        self.observe("counter_sent", task.id, ask, source_message_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
        self.observe("close", task.id, "CANCELLED", "A2A task cancellation")


def build(port: int, profile: dict | None = None, catalog: Catalog | None = None) -> Starlette:
    profile = profile or {}
    name = profile.get("name", "Luna Shop")
    reserves = profile.get("reserve", RESERVES)
    card_variant = os.environ.get("WEEK07_CARD_VARIANT", "baseline")
    hourly_sale = os.environ.get("WEEK07_HOURLY_SALE", "0") == "1"
    if card_variant not in {"baseline", "transparent"}:
        raise ValueError("WEEK07_CARD_VARIANT must be baseline or transparent")
    description = profile.get("description", "Negotiates purchases of " + ", ".join(reserves) + ".")
    if catalog:
        description = f"Negotiates a catalog of {len(catalog.products)} furniture products from natural-language descriptions."
    skill_description = (
        "Describe the furniture you need; the shop selects a matching product, then negotiates a numeric offer."
        if catalog else "Ask for " + ", ".join(reserves) + "; continue with a numeric offer."
    )
    skill_name = "Negotiate a purchase"
    skill_tags = ["shopping", *reserves]
    if card_variant == "transparent":
        items = f"{len(catalog.products)} catalog-listed furniture products" if catalog else ", ".join(reserves)
        description = (
            f"Sells {items} with negotiable asking prices and signed receipts for completed deals."
        )
        skill_name = "Negotiate an item price"
        skill_description = (
            "Ask for an item, then send whole-number price offers. The shop may counter, "
            "reject, or accept with a signed receipt."
        )
        skill_tags = ["shopping", "negotiation", "signed-receipt", *reserves]
    if hourly_sale:
        description = f"{description} {CARD_NOTICE}"
        skill_description = f"{skill_description} {CARD_NOTICE}"
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=name,
        description=description,
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name=skill_name,
            description=skill_description,
            tags=skill_tags,
            examples=["I want to buy a lamp", "110"],
        )],
    )
    try:
        recorder = PricingRecorder(os.environ.get("PRICING_STATE_PATH", "state/pricing_state.sqlite"))
    except Exception as error:
        trace("pricing_record_error", method="init", error=str(error))
        recorder = None
    handler = DefaultRequestHandler(agent_executor=Seller(name, reserves, catalog, recorder, hourly_sale), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--profile")
    parser.add_argument("--catalog")
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8")) if args.profile else None
    catalog = Catalog(args.catalog) if args.catalog else None
    uvicorn.run(build(args.port, profile, catalog), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
