"""Harness-driven furniture seller exposed as an A2A v1.0 JSON-RPC server."""

from __future__ import annotations

import argparse
import json
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

from codex_harness import decide
from catalog_search import load_catalog, search


DEFAULT = {
    "name": "Codex Luna Furniture Shop",
    "description": "Sells lamps, chairs, and bookshelves and negotiates each purchase.",
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
}


def number(text: str) -> int | None:
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


class Seller(AgentExecutor):
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.open: dict[str, dict] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            if self.cfg.get("catalog"):
                candidates = search(self.cfg["catalog"], text)
                choice = decide(
                    f"""You select a product for a large-catalog A2A seller.
The buyer described what they need as: {text!r}
Choose exactly one item_id from these retrieved candidates, based only on fit:
{json.dumps(candidates, ensure_ascii=False)}
Return the required JSON object with item_id and a brief reason.""",
                    schema=Path(__file__).with_name("selection.schema.json"),
                )
                product = next((p for p in candidates if p["id"] == choice.get("item_id")), None)
                if product is None:
                    raise RuntimeError(f"harness selected an item outside retrieved candidates: {choice}")
                item, reserve = product["name"], int(product["reserve"])
                print("CATALOG_SELECTION", json.dumps({
                    "query": text, "candidates": [p["id"] for p in candidates],
                    "selected": product["id"], "reserve": reserve,
                }, ensure_ascii=False), flush=True)
            else:
                item = next((name for name in self.cfg["reserve"] if name in text.lower()), None)
                if item is None:
                    await updater.reject(say("I can negotiate only lamp, chair, or bookshelf purchases."))
                    return
                reserve = self.cfg["reserve"][item]
            decision = decide(
                f"""You are the seller decision engine for an A2A furniture shop.
Return only the required JSON object. The buyer asked: {text!r}
Item: {item}. Secret reserve: {reserve}.
Choose action \"ask\" and a reasonable opening price. The price must be at least the reserve.
For a normal negotiation, open around 130% of reserve. Keep message to one short English sentence."""
            )
            ask = decision.get("price")
            if not isinstance(ask, int):
                raise RuntimeError(f"seller opening decision lacks integer price: {decision}")
            ask = max(reserve, ask)
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask, "offers": []}
            await updater.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        offer = number(text)
        if offer is None:
            await updater.requires_input(say(f"Send one numeric offer. Current ask: {deal['ask']}."))
            return
        deal["offers"].append(offer)
        decision = decide(
            f"""You are the seller decision engine for an A2A furniture shop.
Return only the required JSON object.
Item: {deal['item']}. Secret reserve: {deal['reserve']}. Current ask: {deal['ask']}.
Offers so far: {deal['offers']}. The newest offer is {offer}.
Rules: accept an offer at or above reserve; never accept or counter below reserve.
For a low offer, choose \"counter\" with a price between reserve and current ask, or \"reject\" after repeated low offers.
For acceptance choose \"accept\" and price exactly {offer}. Keep message to one short English sentence."""
        )
        action = decision.get("action")
        reserve = deal["reserve"]

        if action == "accept" and offer >= reserve:
            receipt = {"seller": self.cfg["name"], "item": deal["item"], "price": offer}
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await updater.complete(say(f"Deal: {deal['item']} at {offer}."))
            del self.open[task.id]
            return
        if action == "reject":
            await updater.reject(say(decision.get("message") or f"No deal at {offer}."))
            del self.open[task.id]
            return

        proposed = decision.get("price")
        if not isinstance(proposed, int):
            raise RuntimeError(f"seller counter decision lacks integer price: {decision}")
        counter = max(reserve, proposed)
        deal["ask"] = counter
        await updater.requires_input(say(f"{offer} is too low. Counteroffer: {counter}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg: dict) -> Starlette:
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"], description=cfg["description"], version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name="Negotiate a furniture purchase",
            description="Ask for a lamp, chair, or bookshelf, then negotiate a numeric price.",
            tags=["shopping", "furniture", "negotiation", "codex"],
            examples=["I want to buy a lamp", "100"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--config")
    parser.add_argument("--catalog")
    args = parser.parse_args()
    config = dict(DEFAULT)
    config["reserve"] = dict(DEFAULT["reserve"])
    if args.config:
        with open(args.config, encoding="utf-8") as handle:
            config.update(json.load(handle))
    if args.catalog:
        config["catalog"] = load_catalog(args.catalog)
        config["description"] = (
            f"A large-catalog furniture shop with {len(config['catalog'])} products; "
            "describe the object you need and negotiate its price."
        )
    config["port"] = args.port
    uvicorn.run(build(config), host="127.0.0.1", port=args.port, log_level="warning")
