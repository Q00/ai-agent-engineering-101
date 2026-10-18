"""A2A v1 JSON-RPC seller.
All price and acceptance decisions call the harness.
"""

from __future__ import annotations

import argparse
import asyncio
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from catalog import load, search
from storage import stores

LOG = logging.getLogger("seller")

def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Harness returned not valid positive integer: {value!r}")

    return value

async def decide(req: dict) -> dict:
    from harness.decide import decision_for
    return await asyncio.to_thread(decision_for, req)

async def followup_decision(request: dict, incoming_text: str, reserve: int,
                            current_ask: int) -> tuple[dict, int]:
    """Ask the harness again if it contradicts an offer at the current ask."""
    plain_offer = int(incoming_text) if re.fullmatch(r"\s*\d+\s*", incoming_text) else None
    if plain_offer is not None:
        request = {**request, "numericOffer": plain_offer}
    for attempt in range(2):
        choice = await decide(request)
        try:
            offer = _positive_int(choice.get("parsed_offer"))
            if plain_offer is not None and offer != plain_offer:
                raise ValueError(f"The numeric buyer offer is {plain_offer}, not {offer}")
            action = choice.get("action")
            if offer >= current_ask and offer >= reserve and action != "accept":
                raise ValueError(
                    f"Buyer offered {offer}, meeting your current ask {current_ask}; "
                    f"return accept with price {offer} and parsed_offer {offer}"
                )
            if action == "accept":
                price = _positive_int(choice.get("price"))
                if price != offer or price < reserve:
                    raise ValueError("Acceptance must equal the buyer offer and meet reserve")
            elif action == "offer":
                _positive_int(choice.get("price"))
            elif action not in ("reject", "walk_away"):
                raise ValueError("Unknown seller action")
        except ValueError as exc:
            LOG.warning("invalid harness decision attempt=%s action=%s price=%s "
                        "parsed_offer=%s feedback=%s", attempt + 1,
                        choice.get("action"), choice.get("price"),
                        choice.get("parsed_offer"), exc)
            if attempt == 1:
                raise
            request = {**request, "validationFeedback": str(exc)}
            continue
        LOG.info("decision action=%s price=%s parsed_offer=%s attempt=%s",
                 action, choice.get("price"), offer, attempt + 1)
        return choice, offer
    raise AssertionError("Unreachable decision loop")

class Seller(AgentExecutor):

    def __init__(self, catalog: list[dict], deal_store, name: str, message_suffix: str = ""):
        self.catalog = catalog
        self.by_id = {item["id"]: item for item in catalog}
        self.deals = deal_store
        self.name = name
        self.message_suffix = message_suffix
    
    async def execute(self, context, event_queue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        
        updater = TaskUpdater(event_queue=event_queue, task_id = task.id, context_id = task.context_id)
        text = get_message_text(context.message) or ""
        print("GOT:", repr(text), "parts:", list(context.message.parts))
        deal = await self.deals.get(task.id)

        if deal is None:
            simple = re.fullmatch(
                r"\s*(?:(:i want to buy|buy|want)\s+(?:a|an)\s+)?"
                r"(lamp|chair|bookshelf)\s*[.!]?\s*", text, re.I,
            )

            exact = simple.group(1).lower() if simple else None
            if exact and exact in self.by_id:
                item = self.by_id[exact]
            else:
                candidates = search(text, self.catalog)
                if not candidates:
                    await updater.reject(updater.new_agent_message(parts=[new_text_part("No matching item.")]))
                    return
                try:
                    selected = await decide({"side": "select_item", "description": text, "candidates": candidates})
                    item = {x["id"]:x for x in candidates}.get(selected.get("itemId"))
                except Exception as e:
                    print(f"[seller]    execute {e}")
                    LOG.exception("catalog selection failed")
                    item = None
                if item is None:
                    await updater.reject(updater.new_agent_message(parts=[new_text_part("no matching item.")]))
                    return
                LOG.info("catalog shortlist=%s selected=%s reserve=%s", [x["id"] for x in candidates], item["id"], item["reserve"])
            try:
                selection = await decide({"side": "seller", "item": item["name"], "itemId": item["id"], "limit": item["reserve"], "transcript": [text]})
                if selection.get("action") != "offer":
                    raise ValueError(f"Opening must be an offer, got{str(item['name'])}")
                ask = max(item["reserve"], _positive_int(selection.get("price")))
                deal = {"itemId": item["id"], "ask": ask, "turn": 0, "transcript": [f"buyer: {text}"]}
                await self.deals.save(task.id, deal)
                msg = updater.new_agent_message(parts=[new_text_part(f"{item['name']}: asking {ask}{self.message_suffix}")], metadata={"ask":ask})
                await updater.requires_input(msg)
                LOG.info("open task=%s item=%s ask=%s", task.id, item["id"], ask)
            except Exception as e:
                LOG.exception("opening decision failed")
                await updater.failed(updater.new_agent_message(parts=[new_text_part(str(e))]))
            return

        item = self.by_id[deal["itemId"]]
    
        try:
            choice, offer = await followup_decision(
                {"side": "seller", "item": item["name"],
                 "itemId": item["id"], "limit": item["reserve"],
                 "currentAsk": deal["ask"], "incomingText": text,
                 "turn": deal["turn"], "transcript": deal["transcript"][-6:]},
                text, item["reserve"], deal["ask"],
            )
            deal["turn"] += 1
            deal["transcript"].append(f"buyer: {text}")
            if choice.get("action") == "accept":
                price = _positive_int(choice.get("price"))
                if price != offer or price < item["reserve"]:
                    raise ValueError("Acceptance must equal buyer offer and meet reserve")
                receipt = {"seller": self.name, "item": item["name"],
                           "itemId": item["id"], "price": price, "taskId": task.id}
                await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                await updater.complete(updater.new_agent_message(
                    parts=[new_text_part(f"Deal: {item['name']} at {price}.")]))
                await self.deals.delete(task.id)
                LOG.info("completed task=%s item=%s price=%s", task.id, item["id"], price)
            elif choice.get("action") == "offer":
                ask = max(item["reserve"], _positive_int(choice.get("price")))
                deal["ask"] = ask
                deal["transcript"].append(f"seller: {ask}")
                await self.deals.save(task.id, deal)
                await updater.requires_input(updater.new_agent_message(
                    parts=[new_text_part(f"Counteroffer: {ask}{self.message_suffix}")],
                    metadata={"ask": ask}))
                LOG.info("counter task=%s incoming=%s ask=%s", task.id, offer, ask)
            elif choice.get("action") in ("reject", "walk_away"):
                await updater.reject(updater.new_agent_message(parts=[new_text_part("No deal.")]))
                await self.deals.delete(task.id)
                LOG.info("rejected task=%s incoming=%s", task.id, offer)
            else:
                raise ValueError("Unknown seller action")
        except Exception as exc:
            LOG.exception("follow-up decision failed")
            await updater.failed(updater.new_agent_message(parts=[new_text_part(str(exc))]))
            await self.deals.delete(task.id)

    async def cancel(self, context, event_queue) -> None:
        task = context.current_task
        if task is None:
            return
        await self.deals.delete(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id,
                          context_id=task.context_id).cancel()


def build_app(*, port: int = 9999, cfg: dict | None = None) -> Starlette:
    cfg = cfg or {}
    url = os.getenv("PUBLIC_BASE_URL") or f"http://127.0.0.1:{port}/"
    if not url.endswith("/"):
        url += "/"
    products = load(Path(cfg["catalog"])) if cfg.get("catalog") else load()
    categories = cfg.get("categories")
    if categories:
        products = [p for p in products if p.get("category", p["id"]) in categories]
    for item in products:
        if item["id"] in cfg.get("reserve", {}):
            item["reserve"] = cfg["reserve"][item["id"]]
    task_store, deal_store = stores()
    card = AgentCard(
        name=cfg.get("name", "Catalog Shop"),
        description=cfg.get("description", "Negotiates lamps, chairs, bookshelves and a large product catalog."),
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate products",
                           description="Shop for " + ", ".join(categories or ("lamp", "chair", "bookshelf")),
                           tags=list(categories or ("lamp", "chair", "bookshelf")),
                           examples=["I want to buy a lamp", "something wooden to keep about 40 books"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(
        products, deal_store, card.name, cfg.get("message_suffix", "")),
                                    task_store=task_store, agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


app = build_app(port=int(os.getenv("PORT", "9999")))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(port=args.port, cfg=config), host="127.0.0.1", port=args.port,
                log_level="warning")
