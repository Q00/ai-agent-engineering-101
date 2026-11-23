"""GroqCloud-backed A2A 1.0 seller, including large catalog retrieval.

Start with `python seller.py --port 9999`; the trade test starts it automatically.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import sys

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from catalog import Catalog
from llm_harness import MODEL, decide

SELLER_PROMPT = """You are an independent shop negotiating product prices over A2A.
You are given the item's reserve (minimum legal selling price), previous ask, offer, and history.
OPENING: choose a realistic asking price, preferably between the reserve and 1.2x reserve.
IF OFFER >= RESERVE: choose accept (we have a viable sale), without demanding a higher price.
IF OFFER < RESERVE: choose counter; reduce a previous high ask when helpful, but not below reserve.
After several low offers, you may choose reject to stop bargaining.
Always output only JSON with {\"action\": ..., \"price\": integer}.
The server separately enforces the minimum reserve and all A2A task states.
"""
SELECT_PROMPT = """You are a catalog-matching shop agent.
Select the most appropriate SKU for the buyer's description from ONLY the retrieved candidates.
Return {\"action\":\"choose\", \"sku\":\"SKU-from-list\", \"price\":0}.
Do not invent an SKU. Prioritize the product's stated function, size, material and capacity.
"""


class Shop(AgentExecutor):
    def __init__(self):
        self.open_tasks = {}
        self.catalog = Catalog()

    async def model(self, prompt, data, allowed):
        return await asyncio.to_thread(decide, prompt, data, allowed)

    async def choose_product(self, description):
        exact = self.catalog.exact(description)
        if exact:
            return exact
        candidates = self.catalog.search(description, top_k=5)
        if not candidates:
            return None
        decision = await self.model(SELECT_PROMPT, {
            "buyer_description": description,
            "candidates": candidates,
        }, ["choose"])
        chosen = next((r for r in candidates if r["sku"] == decision.get("sku")), None)
        if chosen is None:
            raise ValueError("Model chose an SKU outside the retrieved candidate shortlist")
        return chosen

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        reply = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        user_text = get_message_text(context.message) or ""
        deal = self.open_tasks.get(task.id)
        if deal is None:
            product = await self.choose_product(user_text)
            if product is None:
                await updater.reject(reply("Sorry, no catalog product matches that request."))
                return
            reserve = int(product["reserve"])
            chosen = await self.model(SELLER_PROMPT, {
                "stage": "opening", "product": product,
                "reserve": reserve, "round": 0, "buyer_text": user_text,
            }, ["ask"])
            # The LLM chooses the intended ask; business safeguards prevent unsafe prices.
            ask = max(reserve, min(chosen["price"], math.ceil(reserve * 1.3)))
            self.open_tasks[task.id] = {"product": product, "ask": ask, "rounds": 0, "offers": []}
            print(f"[seller] task={task.id} sku={product['sku']} opening={ask} reserve={reserve}", flush=True)
            await updater.requires_input(reply(f"{product['name']}: asking {ask}. Send an offer as a number."))
            return

        offer_match = re.fullmatch(r"\s*(\d+)\s*", user_text)
        if not offer_match:
            await updater.requires_input(reply(f"Please send one numeric offer. Asking {deal['ask']}"))
            return
        offer = int(offer_match.group(1))
        product = deal["product"]
        reserve = int(product["reserve"])
        deal["rounds"] += 1
        deal["offers"].append(offer)
        choice = await self.model(SELLER_PROMPT, {
            "stage": "response", "product": product, "reserve": reserve,
            "offer": offer, "previous_ask": deal["ask"],
            "round": deal["rounds"], "previous_offers": deal["offers"][-5:],
        }, ["accept", "counter", "reject"])
        action = choice["action"]
        if action == "accept" and offer >= reserve:
            receipt = {"seller": "Raksmey A2A Shop", "item": product["category"],
                       "sku": product["sku"], "price": offer}
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await updater.complete(reply(f"Deal: {product['name']} at {offer}."))
            del self.open_tasks[task.id]
            print(f"[seller] task={task.id} sold sku={product['sku']} price={offer}", flush=True)
            return
        if action == "reject" or deal["rounds"] >= 6:
            await updater.reject(reply(f"No deal at {offer}."))
            del self.open_tasks[task.id]
            return
        # 'accept' below reserve is converted to a code-enforced counteroffer.
        counter = max(reserve, min(int(choice["price"]), math.ceil(reserve * 1.3)))
        deal["ask"] = counter
        print(f"[seller] task={task.id} offer={offer} counter={counter} reserve={reserve}", flush=True)
        await updater.requires_input(reply(f"{offer} is too low. Counteroffer: {counter}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task:
            self.open_tasks.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def make_app(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=f"Raksmey A2A Shop {os.getenv('SHOP_ID', 'main')}",
        description=(f"A Groq-powered shop specialising in {os.getenv('SHOP_SPECIALTY', 'furniture')}; "
                     "offers lamps, chairs, bookshelves and a large catalog."),
        version="1.0.0", default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description=(f"Specialty: {os.getenv('SHOP_SPECIALTY', 'furniture')}. "
                                        "Describe an item or send one offer price; get a receipt."),
                           tags=["shopping", "negotiation", "catalog"],
                           examples=["I want to buy a lamp", "110", "wooden shelf for 40 books"])],
    )
    handler = DefaultRequestHandler(agent_executor=Shop(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    print(f"[seller] A2A v1.0 port={args.port} model={MODEL}", flush=True)
    uvicorn.run(make_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
