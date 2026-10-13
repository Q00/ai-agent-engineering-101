"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Started from the lab's rule-based seller_agent.py. The A2A side is unchanged; every
decision (asking price, counteroffers, accept, reject) comes from the harness:

    claude -p --model sonnet --tools "" --setting-sources "" --no-session-persistence

The harness answers in JSON. The reserve is in the prompt and is also enforced in code:
an ask below the reserve is raised to the reserve, and an accept below it becomes a
counteroffer at the reserve.
"""
import argparse
import asyncio
import json
import re
import sys
import tempfile

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

NAME = "Sonnet Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
MAX_ROUNDS = 10  # safety net so a task cannot run forever
HARNESS = ["claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="seller-harness-")  # empty dir: no project instructions

PROMPT = """You are a shop owner negotiating the price of one {item} with a buyer.
Your reserve price is {reserve}: never ask below it and never accept an offer below it.
Above the reserve, a higher price is better, but a deal is better than no deal.

Negotiation so far:
{history}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "ask" | "accept" | "reject", "price": <integer>, "message": "<one short sentence to the buyer>"}}
- "ask": you name your asking price (an opening price or a counteroffer) in "price".
- "accept": you accept the buyer's latest offer; "price" is that offer.
- "reject": you end the negotiation without a deal.
"""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


async def harness(prompt):
    proc = await asyncio.create_subprocess_exec(
        *HARNESS, cwd=WORKDIR,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=150)
    return out.decode()


async def decide(item, history):
    prompt = PROMPT.format(item=item, reserve=RESERVE[item], history="\n".join(history))
    for attempt in range(2):
        raw = await harness(prompt)
        log(f"[harness] {raw.strip()}")
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group())
            if d["action"] in ("ask", "accept", "reject") and isinstance(d["price"], (int, float)):
                return d["action"], int(d["price"]), str(d.get("message", ""))
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        log(f"[seller] unparsable answer, attempt {attempt + 1}")
    return None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "rounds", "history"}

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
            deal = self.open[task.id] = {"item": item, "ask": None, "rounds": 0,
                                         "history": [f"Buyer: {text}"]}
            offer = None
        else:
            m = re.search(r"\d+(?:\.\d+)?", text)
            if m is None:
                await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
                return
            offer = int(float(m.group()))
            deal["rounds"] += 1
            deal["history"].append(f"Buyer offers {offer}.")

        item, reserve = deal["item"], RESERVE[deal["item"]]
        decision = await decide(item, deal["history"])
        if decision is None:  # harness failed twice: hold the current ask
            action, price, message = "ask", deal["ask"] or reserve, ""
        else:
            action, price, message = decision

        # code-side guards on the reserve
        if action == "accept" and (offer is None or offer < reserve):
            log(f"[guard] accept of {offer} below reserve {reserve} -> ask {max(price, reserve)}")
            action, price = "ask", max(price, reserve)
        if action == "ask" and price < reserve:
            log(f"[guard] ask {price} below reserve {reserve} -> {reserve}")
            price = reserve
        if action == "ask" and deal["rounds"] >= MAX_ROUNDS:
            action = "reject"
        log(f"[seller] task={task.id[:8]} {item} offer={offer} -> {action} {price}")

        if action == "accept":
            receipt = {"seller": NAME, "item": item, "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"{message} Deal: {item} at {offer}."))
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"{message} No deal."))
            del self.open[task.id]
        else:
            deal["ask"] = price
            deal["history"].append(f"You ask {price}.")
            # the asking price goes last: buyers read the last number as the ask
            await up.requires_input(say(f"{message} Asking price: {price}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells desk lamps, chairs and bookshelves. A model negotiates the price with the buyer.",
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
    args = ap.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
