"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

The code only speaks A2A. The harness (see harness.py, prompts/seller.md) decides the
asking price, the counteroffers, and when to accept or reject. The code enforces the
reserve on top: it never asks below it and never sells below it.
"""
import argparse
import math
import re
import sys
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

import harness

HERE = Path(__file__).resolve().parent
NAME = "Codex Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
PROMPT = (HERE / "prompts" / "seller.md").read_text(encoding="utf-8")


def log(line):
    print(f"[seller] {line}", file=sys.stderr, flush=True)


def decide(item, history, offer):
    """Ask the harness. Returns (action, price) after the reserve guard."""
    reserve = RESERVE[item]
    lines = "\n".join(f"{who}: {what}" for who, what in history) or "(nothing yet)"
    prompt = (PROMPT.replace("[[item]]", item).replace("[[reserve]]", str(reserve))
              .replace("[[history]]", lines).replace("[[offer]]", str(offer) if offer is not None else "none yet"))
    ans = harness.ask(prompt) or {}
    action, price = ans.get("action"), ans.get("price")
    try:
        price = int(price)
    except (TypeError, ValueError):
        price = None
    log(f"harness said action={action!r} price={price!r} (offer {offer}, reserve {reserve})")

    # reserve guard: the harness may be wrong, the reserve may not
    if action == "accept" and (offer is None or offer < reserve):
        log(f"guard: accept below reserve -> counter at {reserve}")
        return "counter", reserve
    if action in ("ask", "counter") and (price is None or price < reserve):
        log(f"guard: {action} {price} below reserve -> {reserve}")
        return action, reserve
    if action == "accept":
        return "accept", offer
    if action == "reject":
        return "reject", 0
    if action in ("ask", "counter"):
        return action, price
    fallback = math.ceil(reserve * 1.3)
    log(f"guard: unusable answer {ans!r} -> ask {fallback}")
    return "ask" if offer is None else "counter", fallback


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "history": [(who, text)]}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:  # first message: which item?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            deal = self.open[task.id] = {"item": item, "history": [("buyer", text)]}
            _, price = decide(item, deal["history"], None)
            reply = f"{item}: asking {price}. Send your offer as a number."
            deal["history"].append(("seller", reply))
            await up.requires_input(say(reply))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say("Send your offer as a number."))
            return
        offer = int(m.group())
        deal["history"].append(("buyer", text))
        action, price = decide(deal["item"], deal["history"], offer)
        if action == "accept":
            receipt = {"seller": NAME, "item": deal["item"], "price": price}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {price}."))
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
        else:
            reply = f"{offer} is too low. Counteroffer: {price}."  # the last number is the ask
            deal["history"].append(("seller", reply))
            await up.requires_input(say(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, public_url=None):
    url = public_url or f"http://127.0.0.1:{port}/"  # behind a tunnel the card must carry the public address
    card = AgentCard(
        name=NAME,
        description="Sells desk lamps, chairs and bookshelves. A Codex harness negotiates the price.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    ap.add_argument("--public-url", help="address written into the Agent Card, e.g. a tunnel URL ending in /")
    args = ap.parse_args()
    uvicorn.run(build(args.port, args.public_url), host=args.host, port=args.port, log_level="warning")
