"""Harness-driven seller exposed as an A2A v1.0 server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Built on the lab's seller_agent.py: the A2A side (card, task, TaskUpdater,
receipt artifact) is the same. The rules are gone; every price decision is a
call to the harness (harness.py):
  - first message  -> harness picks the asking price
  - each offer     -> harness answers counter / accept / reject

Where the reserve lives: in the prompt (the model is told the floor) AND in
code (guard() below). The code guard never invents a decision, it only
refuses illegal ones: an ask below the reserve is raised to the reserve, an
accept below the reserve becomes a counter at the current ask.
"""
import argparse
import math
import re

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import ask_json, log

NAME = "Haiku Furniture Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

OPEN_PROMPT = """You are the owner of a small furniture shop negotiating with a buyer.
A buyer wants a {item}. Your lowest acceptable price (reserve) is {reserve}; never ask below it.
Pick an opening asking price that leaves room to negotiate down.
Answer with JSON only, no prose: {{"price": <integer>, "say": "<one short sentence, no numbers>"}}"""

OFFER_PROMPT = """You are the owner of a small furniture shop negotiating with a buyer over a {item}.
Your lowest acceptable price (reserve) is {reserve}. Never ask below it and never sell below it.
History so far (your asks and the buyer's offers):
{history}
The buyer now offers {offer}. Your current asking price is {ask}.
Decide one of:
- "accept": sell at the buyer's offer (only if the offer is at least {reserve}).
- "counter": name a new asking price (at least {reserve}); move toward the buyer to close a deal.
- "reject": end the negotiation (only if the buyer is clearly not going to reach {reserve}).
Answer with JSON only, no prose: {{"action": "accept"|"counter"|"reject", "price": <integer>, "say": "<one short sentence, no numbers>"}}"""


def guard_ask(price, reserve, fallback):
    """Integer ask, never below the reserve. Non-numeric -> fallback."""
    try:
        p = math.ceil(float(price))
    except (TypeError, ValueError):
        return fallback
    if p < reserve:
        log(f"[guard] ask {p} below reserve {reserve} -> {reserve}")
        p = reserve
    return p


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "history": [str]}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        log(f"[task {task.id[:8]}] <- {text!r}")
        deal = self.open.get(task.id)

        if deal is None:  # first message: which item, and what is the asking price?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            reserve = RESERVE[item]
            data, _ = ask_json(OPEN_PROMPT.format(item=item, reserve=reserve), f"seller {task.id[:8]}")
            data = data or {}
            ask = guard_ask(data.get("price"), reserve, fallback=math.ceil(reserve * 1.3))
            self.open[task.id] = {"item": item, "ask": ask, "history": [f"you asked {ask}"]}
            note = str(data.get("say") or "Thanks for your interest.")
            log(f"[task {task.id[:8]}] -> open ask {ask}")
            await up.requires_input(say(f"{note} {item}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
            return
        offer, reserve, item = math.floor(float(m.group())), RESERVE[deal["item"]], deal["item"]
        deal["history"].append(f"buyer offered {offer}")
        data, _ = ask_json(OFFER_PROMPT.format(item=item, reserve=reserve, offer=offer, ask=deal["ask"],
                                               history="\n".join(deal["history"])), f"seller {task.id[:8]}")
        data = data or {"action": "counter", "price": deal["ask"]}   # harness failed: hold the current ask
        action = str(data.get("action", "")).lower()
        note = str(data.get("say") or "")

        if action == "accept" and offer < reserve:
            log(f"[guard] accept at {offer} below reserve {reserve} -> counter")
            action = "counter"
            data["price"] = deal["ask"]

        if action == "accept":
            receipt = {"seller": NAME, "item": item, "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {offer}."))
            log(f"[task {task.id[:8]}] -> COMPLETED at {offer}")
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say(f"{note} No deal at {offer}.".strip()))
            log(f"[task {task.id[:8]}] -> REJECTED at {offer}")
            del self.open[task.id]
            return

        deal["ask"] = guard_ask(data.get("price"), reserve, fallback=deal["ask"])
        if str(deal["ask"]) not in note and re.search(r"\d", note):
            note = ""   # the model's sentence names another price (e.g. one the guard raised); drop it
        deal["history"].append(f"you asked {deal['ask']}")
        log(f"[task {task.id[:8]}] -> counter {deal['ask']}")
        await up.requires_input(say(f"{note} Counteroffer: {deal['ask']}.".strip()))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells lamps, chairs and bookshelves. A model negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item, then send a price as a number. The shop answers with a counteroffer "
                        "(the last number in its message), a receipt artifact, or a rejection.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
