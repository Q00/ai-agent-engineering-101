"""A furniture seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding). The harness negotiates.

    python seller.py --port 9999

Started from the lab's seller_agent.py. The A2A side is the same; the rules are
replaced by harness calls (harness.py). The harness decides the asking price,
every counteroffer, and when to accept or reject. Code guards the reserve:
  - an ask below the reserve is raised to the reserve
  - an accept below the reserve becomes a counter at the last ask
  - a counter above the last ask is held at the last ask
  - an offer at or above the current ask is accepted without asking the harness
Every guard that fires is logged with "GUARD:".
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

import harness
from harness import log

NAME = "Haiku Furniture Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

ANSWER_FORMAT = ('Answer with one JSON object and nothing else:\n'
                 '{"action": "accept" | "counter" | "reject", "price": <integer>, '
                 '"note": "<one short sentence to the buyer, no digits>"}')

OPEN_PROMPT = """You are the seller in a furniture shop. A buyer has just asked for a {item}.
Your reserve, the lowest price you may ever ask or sell for, is {reserve}. The buyer does not know it.
Name your opening asking price. Leave room to come down, since the buyer will bargain.

Answer with one JSON object and nothing else:
{{"price": <integer>, "note": "<one short sentence to the buyer, no digits>"}}"""

COUNTER_PROMPT = """You are the seller in a furniture shop, negotiating the price of a {item} with a buyer.
Your reserve, the lowest price you may ever ask or sell for, is {reserve}. The buyer does not know it.

So far:
{history}
The buyer now offers {offer}. Your current asking price is {ask}.

Decide:
- "accept": sell at the buyer's offer of {offer}.
- "counter": name a new asking price, at or above {reserve} and no higher than {ask}.
- "reject": end the negotiation without a deal.
A deal at or above your reserve is better than no deal. Concede step by step while the buyer keeps raising.
Reject only when the buyer clearly will not reach your reserve.

""" + ANSWER_FORMAT


def clean_note(note):
    """The note must carry no numbers: the buyer reads the asking price as the last number in the message."""
    note = re.sub(r"\d+(?:\.\d+)?", "", str(note or "")).strip()
    return re.sub(r"\s+", " ", note)


def as_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log("IN", f"task {task.id[:8]} {text!r}")

        if deal is None:  # first message: which item, and the opening ask
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            reserve = RESERVE[item]
            answer = await harness.ask(OPEN_PROMPT.format(item=item, reserve=reserve)) or {}
            ask = as_int(answer.get("price"))
            if ask is None:
                ask = math.ceil(reserve * 1.3)
                log("GUARD", f"no usable opening price from the harness, asking {ask}")
            elif ask < reserve:
                log("GUARD", f"opening ask {ask} below reserve {reserve}, asking {reserve}")
                ask = reserve
            note = clean_note(answer.get("note"))
            self.open[task.id] = {"item": item, "ask": ask, "history": [f"You asked {ask}."]}
            log("OUT", f"open {item} at {ask}")
            await up.requires_input(say(f"{item}: {note + ' ' if note else ''}Asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
            return
        offer, item, ask = as_int(m.group()), deal["item"], deal["ask"]
        reserve = RESERVE[item]
        deal["history"].append(f"The buyer offered {offer}.")

        if offer >= ask:  # the buyer met our own price
            log("OUT", f"offer {offer} meets ask {ask}, accepted by code")
            await self.close(up, say, task.id, item, offer, "Agreed.")
            return

        answer = await harness.ask(COUNTER_PROMPT.format(
            item=item, reserve=reserve, history="\n".join(deal["history"][:-1]), offer=offer, ask=ask)) or {}
        action, price, note = answer.get("action"), as_int(answer.get("price")), clean_note(answer.get("note"))

        if action == "accept":
            if offer >= reserve:
                await self.close(up, say, task.id, item, offer, note)
                return
            log("GUARD", f"harness accepted {offer} below reserve {reserve}, countering at {ask}")
            action, price = "counter", ask
        if action == "reject":
            log("OUT", f"reject at offer {offer}")
            del self.open[task.id]
            await up.reject(say(f"{note} No deal.".strip()))
            return
        if action != "counter" or price is None:
            log("GUARD", f"no usable answer from the harness ({answer!r}), holding at {ask}")
            price = ask
        if price < reserve:
            log("GUARD", f"counter {price} below reserve {reserve}, countering at {reserve}")
            price = reserve
        if price > ask:
            log("GUARD", f"counter {price} above the last ask {ask}, holding at {ask}")
            price = ask
        if price <= offer:  # countering at or below the offer means the offer is acceptable
            log("GUARD", f"counter {price} at or below offer {offer}, accepting {offer}")
            await self.close(up, say, task.id, item, offer, note)
            return
        deal["ask"] = price
        deal["history"].append(f"You countered {price}.")
        log("OUT", f"counter {price} to offer {offer}")
        await up.requires_input(say(f"{note} Counteroffer: {price}.".strip()))

    async def close(self, up, say, task_id, item, price, note):
        receipt = {"seller": NAME, "item": item, "price": price}
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(say(f"{note} Deal: {item} at {price}.".strip()))
        del self.open[task_id]
        log("OUT", f"deal {item} at {price}")

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
        capabilities=AgentCapabilities(),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf), then send offers as numbers on the same task. "
                        "The asking price is the last number in each reply; a deal ends with a receipt artifact.",
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
