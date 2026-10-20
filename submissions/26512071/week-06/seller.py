"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Started from the lab's rule-based seller_agent.py. The A2A side is unchanged; every
decision (asking price, counteroffers, accept, reject) comes from the harness:

    claude -p --model sonnet --tools "" --setting-sources "" --no-session-persistence

The harness answers in JSON. The reserve is in the prompt and is also enforced in code:
an ask below the reserve is raised to the reserve, and an accept below it becomes a
counteroffer at the reserve.

Challenge 1: the shop sells from catalog.json (3000 items). A first message that names
an item exactly ("I want to buy a lamp") goes straight to it. Otherwise search.py
narrows the catalog to 20 candidates and the harness picks one by id; code checks that
the id is one of the candidates, and the reserve comes from that catalog entry.
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

from search import Catalog

NAME = "Sonnet Shop"
CATALOG = Catalog()
MAX_ROUNDS = 10  # safety net so a task cannot run forever
HARNESS = ["claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="seller-harness-")  # empty dir: no project instructions

PICK = """You are a furniture shop owner. A buyer wrote:
{request}

These catalog items matched a search for the request (id | description):
{candidates}

Pick the one item that best fits what the buyer describes, including any sizes or
amounts they mention. If none of them is the kind of thing they want, pick none.
Answer with one JSON object and nothing else:
{{"id": "<one id from the list>" | null, "why": "<one short sentence>"}}
"""

PROMPT = """You are a shop owner negotiating with a buyer the price of this item: {item}
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


async def pick(request):
    """The catalog item the buyer asks for, or None."""
    hit = CATALOG.exact(request)
    if hit:
        return hit
    candidates = CATALOG.search(request)
    if not candidates:
        return None
    log(f"[search] {len(candidates)} candidates: " + " ".join(c["id"] for c in candidates))
    by_id = {c["id"]: c for c in candidates}
    prompt = PICK.format(request=request, candidates="\n".join(f"{c['id']} | {c['description']}" for c in candidates))
    for attempt in range(2):
        raw = await harness(prompt)
        log(f"[harness] {raw.strip()}")
        m = re.search(r"\{.*?\}", raw, re.S)
        try:
            d = json.loads(m.group())
            if d["id"] is None or d["id"] in by_id:  # code-side guard: only a candidate can be sold
                return by_id.get(d["id"])
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        log(f"[seller] unusable pick, attempt {attempt + 1}")
    return None


async def decide(item, history):
    prompt = PROMPT.format(item=item["description"], reserve=item["reserve"], history="\n".join(history))
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
            item = await pick(text)
            if item is None:
                await up.reject(say("Sorry, nothing in our catalog fits that."))
                return
            log(f"[seller] task={task.id[:8]} picked {item['id']} {item['name']} reserve={item['reserve']}")
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

        item, reserve = deal["item"], deal["item"]["reserve"]
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
        log(f"[seller] task={task.id[:8]} {item['id']} offer={offer} -> {action} {price}")

        if action == "accept":
            receipt = {"seller": NAME, "item": item["name"], "id": item["id"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"{message} Deal: {item['name']} at {offer}."))
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
        description=f"Sells {len(CATALOG.items)} pieces of furniture, from lamps to wardrobes. "
                    "Describe what you need; a model picks the item and negotiates the price.",
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
            examples=["I want to buy a lamp", "something wooden to keep about 40 books", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
