"""A rule-based seller exposed as an A2A server."""

import argparse
from pathlib import Path
import json
import base64
import math
import re
import urllib.request

import uvicorn
from starlette.applications import Starlette
from cryptography.hazmat.primitives import serialization

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

from harness import run_harness


def canon(d):
    return json.dumps(
        d,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def sign_receipt(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(
        open(key_path, "rb").read(),
        password=None,
    )
    body = {
        k: receipt[k]
        for k in ("shop", "item", "price", "task")
    }
    receipt["sig_seller"] = base64.b64encode(
        key.sign(canon(body))
    ).decode()
    return receipt


def log_seller_event(event, **fields):
    path = Path("logs/seller-events.log")
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {"event": event, **fields}

    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def seller_market_context(item):
    prices = []
    sales_path = Path(__file__).resolve().parent / "logs" / "seller-sales.log"

    if sales_path.exists():
        for line in sales_path.read_text(encoding="utf-8").splitlines():
            try:
                sale = json.loads(line)
                if sale.get("item") == item:
                    prices.append(sale["price"])
            except (json.JSONDecodeError, KeyError):
                pass

    reviews = []
    try:
        with urllib.request.urlopen(
            "https://market.ourolabs.ai/api/shops",
            timeout=3,
        ) as response:
            shops = json.load(response)["shops"]

        own_shop = next(
            (shop for shop in shops if shop.get("name") == "dariaklt"),
            None,
        )
        if own_shop:
            reviews = own_shop.get("reviews", [])
    except Exception:
        pass

    recent = prices[-30:]
    sales_summary = {
        "count": len(prices),
        "recent_average": (
            round(sum(recent) / len(recent), 2)
            if recent else None
        ),
        "recent_min": min(recent) if recent else None,
        "recent_max": max(recent) if recent else None,
    }

    return {
        "sales": sales_summary,
        "reviews": reviews[-10:],
    }

DEFAULT = {
    "name": "Lamp Shop",
    "description": "Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
    "skill_description": "Ask for an item, then send a price. The shop answers with a counteroffer or closes the deal.",
    "port": 9999,
    "reserve": {
        "lamp": 100,
        "chair": 180,
        "bookshelf": 260,
    },
    "max_rounds": 3,
}


def seller_decision(item, reserve, current_ask=None, offer=None, rounds=0):
    market_context = seller_market_context(item)
    prompt = f"""
You are a seller negotiating one item.

Item: {item}
Minimum acceptable price (reserve): {reserve}
Current asking price: {current_ask}
Buyer offer: {offer}
Previous low offers: {rounds}
Recent own trades and reviews: {json.dumps(market_context, ensure_ascii=False)}

Use this market history when choosing your asking price or counteroffer.
If recent sales consistently close above reserve and reviews remain positive, you may protect a higher margin.
If reviews worsen or recent prices are close to reserve, be more conservative.

Decide the next action.

Rules:
- Never sell below the reserve.
- Never ask or counter below the reserve.
- If there is no buyer offer yet, choose an asking price.
- Do not accept an offer merely because it is above the reserve.
- Try to close the deal above the reserve and protect your margin.
- If the buyer offer is below the current asking price, normally counter with a price between the offer and the current ask.
- Accept when the buyer has reached a reasonable negotiated price close to the current ask.
- Only fall back to the reserve after several negotiation rounds if that seems necessary to save the deal.
- For a lower offer, normally counter.
- After repeated low offers, you may reject.

Return ONLY one JSON object:
{{"action":"ask","price":123}}
{{"action":"counter","price":123}}
{{"action":"accept","price":123}}
or
{{"action":"reject"}}
"""

    raw = run_harness(prompt)
    return json.loads(raw)


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open = {}

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:

        task = context.current_task

        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        up = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )

        say = lambda text: up.new_agent_message(
            parts=[new_text_part(text)]
        )

        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        # First message: identify the item.
        if deal is None:
            item = next(
                (
                    k
                    for k in self.cfg["reserve"]
                    if k in text.lower()
                ),
                None,
            )

            if item is None:
                await up.reject(
                    say(
                        "We only sell: "
                        + ", ".join(self.cfg["reserve"])
                        + "."
                    )
                )
                return

            reserve = self.cfg["reserve"][item]
            decision = seller_decision(
                item=item,
                reserve=reserve,
            )
            ask = max(
                reserve,
                int(decision.get("price", reserve)),
            )

            self.open[task.id] = {
                "item": item,
                "ask": ask,
                "rounds": 0,
            }

            log_seller_event(
                "open",
                task=task.id,
                item=item,
                ask=ask,
                reserve=reserve,
            )

            await up.requires_input(
                say(
                    f"{item}: asking {ask}. "
                    "Send your offer as a number."
                )
            )
            return

        # Follow-up message: read buyer's offer.
        m = re.search(r"\d+", text)

        if m is None:
            await up.requires_input(
                say("Send your offer as a number.")
            )
            return

        offer = int(m.group())
        reserve = self.cfg["reserve"][deal["item"]]

        log_seller_event(
            "offer",
            task=task.id,
            item=deal["item"],
            offer=offer,
            current_ask=deal["ask"],
            round=deal["rounds"],
        )

        decision = seller_decision(
            item=deal["item"],
            reserve=reserve,
            current_ask=deal["ask"],
            offer=offer,
            rounds=deal["rounds"],
        )

        action = decision.get("action", "counter")

        # After several negotiation rounds, accept any valid offer
        # at or above the reserve instead of losing the deal over a few units.
        if deal["rounds"] >= 3 and offer >= reserve:
            action = "accept"

        if action == "accept" and offer >= reserve:
            receipt = sign_receipt({
                "shop": self.cfg["name"],
                "item": deal["item"],
                "price": int(offer),
                "task": task.id,
            })

            log_path = Path("logs/seller-sales.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as f:
                f.write(json.dumps({
                    "item": deal["item"],
                    "price": int(offer),
                    "task": task.id,
                }) + "\n")

            await up.add_artifact(
                parts=[new_data_part(receipt)],
                name="receipt",
            )

            log_seller_event(
                "completed",
                task=task.id,
                item=deal["item"],
                price=offer,
            )

            await up.complete(
                say(f"Deal: {deal['item']} at {offer}.")
            )

            del self.open[task.id]
            return

        if action == "reject":
            log_seller_event(
                "rejected",
                task=task.id,
                item=deal["item"],
                offer=offer,
            )

            await up.reject(
                say(f"No deal at {offer}.")
            )
            del self.open[task.id]
            return

        deal["rounds"] += 1

        counter = max(
            reserve,
            int(decision.get("price", reserve)),
        )
        deal["ask"] = counter

        log_seller_event(
            "counter",
            task=task.id,
            item=deal["item"],
            offer=offer,
            counter=counter,
        )

        await up.requires_input(
            say(
                f"{offer} is too low. "
                f"Counteroffer: {counter}."
            )
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:

        task = context.current_task

        if task is not None:
            self.open.pop(task.id, None)

            await TaskUpdater(
                event_queue=event_queue,
                task_id=task.id,
                context_id=task.context_id,
            ).cancel()


def build(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"

    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=[
            "application/json",
            "text/plain",
        ],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=url,
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=cfg["skill_description"],
                tags=["shopping", "negotiation"],
                examples=[
                    "I want to buy a lamp",
                    "90",
                ],
            )
        ],
    )

    handler = DefaultRequestHandler(
        agent_executor=Seller(cfg),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    return Starlette(
        routes=create_agent_card_routes(card)
        + create_jsonrpc_routes(handler, "/")
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    cfg = dict(DEFAULT)

    if args.config:
        cfg.update(json.load(open(args.config)))

    if args.port:
        cfg["port"] = args.port

    uvicorn.run(
        build(cfg),
        host="127.0.0.1",
        port=cfg["port"],
        log_level="warning",
    )








