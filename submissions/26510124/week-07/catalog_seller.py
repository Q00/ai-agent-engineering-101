"""A2A catalog seller: descriptions -> bounded retrieval -> model negotiation.

    .venv/bin/python catalog_seller.py --port 9100
    .venv/bin/python catalog_seller.py --config data/shop-01.json

Quotes contain a public product+ask data Part. Receipts contain product+price.
The reserve is private, and every price/accept/reject comes from Claude Code.
"""
import argparse
import asyncio
import json
import re

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from catalog import Catalog, public_product, select_product
from harness import HarnessError, seller_decision


DEFAULT = {"name": "Catalog Furniture Shop", "port": 9100,
           "categories": ["lamp", "chair", "bookshelf", "table"], "reserve_offset": 0}


class CatalogSeller(AgentExecutor):
    def __init__(self, cfg, catalog=None):
        self.cfg = cfg
        self.catalog = catalog if catalog is not None else Catalog.from_config(cfg)
        self.open = {}
        self.pending = {}
        self.locks = {}
        self.execution_locks = {}
        self.finished = set()

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        async with self.execution_locks.setdefault(task.id, asyncio.Lock()):
            if task.id in self.finished:
                return
            await self._execute(task.id, get_message_text(context.message) or "", up)

    async def _execute(self, task_id, text, up):
        def say(reply, product=None, ask=None):
            parts = [new_text_part(reply)]
            if product is not None:
                parts.append(new_data_part({"product": public_product(product), "ask": ask}))
            return up.new_agent_message(parts=parts)

        previous = self.open.get(task_id, {})
        history = previous.get("history", []) + [{"role": "buyer", "text": text}]
        product = previous.get("product")
        if product is not None and re.fullmatch(r"[0-9]+", text.strip()) is None:
            await up.requires_input(say(f"Send an integer offer. Current asking price: {previous['ask']}", product, previous["ask"]))
            return
        canceled = asyncio.Event()
        self.pending[task_id] = canceled
        lock = self.locks.setdefault(task_id, asyncio.Lock())
        try:
            try:
                if product is None:
                    decision = await asyncio.to_thread(select_product, text, self.catalog, previous.get("history", []))
                else:
                    decision = await asyncio.to_thread(seller_decision, item=product["name"],
                                                       reserve=product["reserve"], offer=int(text.strip()), history=history)
            except HarnessError as exc:
                async with lock:
                    if not canceled.is_set():
                        self.open.pop(task_id, None)
                        self.finished.add(task_id)
                        await up.failed(say(str(exc)))
                return

            async with lock:
                if canceled.is_set():
                    return
                action = decision["action"]
                if action == "clarify":
                    reply = decision["question"]
                    self.open[task_id] = {"history": history + [{"role": "seller", "text": reply}]}
                    await up.requires_input(say(reply))
                elif action in ("select", "counter"):
                    if action == "select":
                        product = decision["product"]
                    price = decision["price"]
                    reply = f"{product['name']}. {product['description']} Asking price: {price}"
                    self.open[task_id] = {"product": product, "ask": price,
                                          "history": history + [{"role": "seller", "text": reply}]}
                    await up.requires_input(say(reply, product, price))
                elif action == "accept":
                    price = decision["price"]
                    receipt = {"seller": self.cfg["name"], "item": product["id"],
                               "product": public_product(product), "price": price}
                    await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                    await up.complete(say(f"Purchased {product['name']} for {price}."))
                    print(json.dumps({"event": "catalog_sale", "task_id": task_id, **receipt}), flush=True)
                    self.open.pop(task_id, None)
                    self.finished.add(task_id)
                else:
                    await up.reject(say("No suitable product or mutually acceptable deal. Negotiation ended."))
                    self.open.pop(task_id, None)
                    self.finished.add(task_id)
        finally:
            if self.pending.get(task_id) is canceled:
                self.pending.pop(task_id, None)

    async def cancel(self, context, event_queue):
        task = context.current_task
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            canceled = self.pending.get(task.id)
            if canceled:
                canceled.set()
            self.open.pop(task.id, None)
            self.finished.add(task.id)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg):
    cfg = {**DEFAULT, **cfg}
    catalog = Catalog.from_config(cfg)
    skills = []
    for category in sorted({product["category"] for product in catalog.products}):
        products = [product for product in catalog.products if product["category"] == category]
        materials = sorted({product["material"] for product in products})
        description = (f"Sells {category}: {', '.join(materials)}; height "
                       f"{min(product['height_cm'] for product in products)} to {max(product['height_cm'] for product in products)} cm. "
                       "Describe the required function, material and dimensions. The shop searches its catalog and negotiates prices.")
        if category == "bookshelf":
            description += f" Book capacity up to {max(product['capacity_books'] for product in products)}."
        if category == "chair":
            description += " Fabric upholstered desk seating is available."
        skills.append(AgentSkill(id=f"catalog-{category}", name=f"Shop for {category}", description=description,
                                 tags=[category, *materials, "catalog-search", "negotiation"]))
    if not skills:
        raise ValueError("Shop inventory must not be empty")
    card = AgentCard(name=cfg["name"], description=f"A furniture shop with {len(catalog.products)} catalog products.",
                     version="1.0.0", default_input_modes=["text/plain", "application/json"],
                     default_output_modes=["text/plain", "application/json"], capabilities=AgentCapabilities(),
                     supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0",
                                                           url=f"http://127.0.0.1:{cfg['port']}/")], skills=skills)
    handler = DefaultRequestHandler(agent_executor=CatalogSeller(cfg, catalog), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        with open(args.config, encoding="utf-8") as stream:
            cfg.update(json.load(stream))
    if args.port is not None:
        cfg["port"] = args.port
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
