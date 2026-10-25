"""The seller: an A2A v1.0 server (a2a-sdk 1.2.x, JSON-RPC binding) whose decisions come from the harness.

  python -u seller.py --port 9999                                   # trade test: lamp, chair, bookshelf
  python -u seller.py --port 9101 --catalog catalog.json            # challenge 1: ~3000 items
  python -u seller.py --port 9101 --catalog catalog.json --categories "desk,office chair" \
      --markup 1.1 --name "Desk Depot"                              # challenge 2: one of many shops

The A2A side is the lab's seller_agent.py. The harness picks the item, the opening ask,
every counteroffer, and when to accept or reject. The code only enforces the reserve:
it never sends an ask below the item's reserve and never closes below it.
Every input-required message ends with the current ask, so the last number is the price.
"""
import argparse
import json
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
from catalog import Index

BASIC = [  # the trade-test catalog
    {"id": "lamp", "name": "lamp", "category": "lamp", "description": "A desk lamp.", "reserve": 100},
    {"id": "chair", "name": "chair", "category": "chair", "description": "A wooden chair.", "reserve": 180},
    {"id": "bookshelf", "name": "bookshelf", "category": "bookshelf", "description": "A five-shelf bookshelf.", "reserve": 260},
]
MAX_ROUNDS = 8

SYSTEM = ("You are the sales agent of {shop}, a shop that negotiates prices with buyers. "
          "Each item has a reserve: the lowest price you may accept. Never ask or accept below it. "
          "Answer with exactly one JSON object and nothing else.")
OPEN = ('A buyer wrote: "{text}"\n\nCandidate items from the catalog (id | name | details | reserve):\n{cands}\n\n'
        "Pick the one item that best matches the buyer's request (kind of item, material, size, capacity). "
        "If several fit, pick the closest one; do not ask for more details. Use \"none\" only when no "
        "candidate is the kind of item the buyer asks for. "
        "Set an opening asking price at or above that item's reserve; shops usually open 20 to 40 percent above it.\n"
        'JSON: {{"item_id": "<id or none>", "ask": <whole number>, "message": "<one short sentence to the buyer, no numbers>"}}')
FOLLOW = ("You are selling: {name}. Your reserve (lowest price you may accept) is {reserve}.\n"
          "Your asks so far: {asks}. The buyer's offers so far: {offers}.\n"
          'The buyer now wrote: "{text}" (offer: {offer}).\n'
          "This is round {round}. Buyers usually walk away after about five rounds, and a deal at or above "
          "your reserve is better than no deal.\n"
          "Decide: accept the buyer's offer, counter with a new asking price (not below your reserve), "
          "or reject (end the negotiation).\n"
          'JSON: {{"action": "accept" | "counter" | "reject", "price": <your new ask if counter>, '
          '"message": "<one short sentence to the buyer, no numbers>"}}')


def log(msg):
    print(msg, flush=True)


def _clean(msg) -> str:
    """The harness's sentence without digits, so the last number in the reply is always the ask."""
    return re.sub(r"\s+", " ", re.sub(r"\d", "", str(msg or ""))).strip()


def _last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return int(float(nums[-1])) if nums else None


class Seller(AgentExecutor):
    def __init__(self, cfg, items):
        self.cfg = cfg
        self.items = {i["id"]: i for i in items}
        self.index = Index(items) if cfg.get("catalog") else None
        self.open = {}  # task id -> {"item", "reserve", "asks", "offers"}
        self.system = SYSTEM.format(shop=cfg["name"])

    def _candidates(self, text):
        if self.index is not None:
            return self.index.search(text, k=12)
        named = [i for i in self.items.values() if i["name"] in text.lower()]
        return named or list(self.items.values())

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        tid = task.id[:8]
        log(f"[{tid}] <- {text!r}")

        if deal is None:  # first message: which item, and the opening ask
            cands = self._candidates(text)
            lines = "\n".join(f"{i['id']} | {i['name']} | {i['description']} | reserve {i['reserve']}" for i in cands)
            d = await harness.decide(self.system, OPEN.format(text=text, cands=lines), log, f"{tid} harness")
            item = self.items.get(str(d.get("item_id")))
            if item is None or item not in cands:
                log(f"[{tid}] no matching item -> REJECTED")
                await up.reject(say("Sorry, we do not have that item."))
                return
            ask = d.get("ask") if isinstance(d.get("ask"), (int, float)) else round(item["reserve"] * 1.3)
            ask = max(int(ask), item["reserve"])  # code guard: never ask below the reserve
            self.open[task.id] = {"item": item, "asks": [ask], "offers": []}
            log(f"[{tid}] item {item['id']} ({item['name']}), reserve {item['reserve']}, ask {ask}")
            await up.requires_input(say(f"{item['name']}: {_clean(d.get('message'))} Asking {ask}."))
            return

        item, reserve = deal["item"], deal["item"]["reserve"]
        offer = _last_number(text)
        if offer is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['asks'][-1]}."))
            return
        deal["offers"].append(offer)
        rnd = len(deal["offers"])
        d = await harness.decide(self.system, FOLLOW.format(
            name=item["name"], reserve=reserve, asks=deal["asks"], offers=deal["offers"][:-1], text=text,
            offer=offer, round=rnd), log, f"{tid} harness")
        action = str(d.get("action", "counter")).lower()
        price = d.get("price") if isinstance(d.get("price"), (int, float)) else deal["asks"][-1]
        ask = min(max(int(price), reserve), deal["asks"][-1])  # not below the reserve, not above the last ask
        if action == "counter" and ask <= offer and offer >= reserve:
            log(f"[{tid}] [guard] counter {ask} is at or below the offer {offer}: accept the offer instead")
            action = "accept"
        if action == "accept" and offer < reserve:
            log(f"[{tid}] [guard] accept at {offer} is below the reserve {reserve}: counter at {ask} instead")
            action = "counter"
        if action == "counter" and rnd >= MAX_ROUNDS:
            action = "reject"

        if action == "accept":
            receipt = {"seller": self.cfg["name"], "item": item["name"], "item_id": item["id"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item['name']} at {offer}."))
            log(f"[{tid}] DEAL {item['id']} at {offer} (reserve {reserve})")
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"{_clean(d.get('message')) or 'No deal.'}"))
            log(f"[{tid}] REJECTED after {rnd} offers")
            del self.open[task.id]
        else:
            deal["asks"].append(ask)
            await up.requires_input(say(f"{_clean(d.get('message'))} Counteroffer: {ask}."))
            log(f"[{tid}] counter {ask}")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        log(f"[{task.id[:8]}] canceled by the buyer")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg, items):
    url = f"http://127.0.0.1:{cfg['port']}/"
    sells = ", ".join(sorted({i["category"] for i in items}))
    card = AgentCard(
        name=cfg["name"],
        description=f"Sells {sells}. Negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description=f"Describe the item you want ({sells}), then send offers as numbers. "
                        "The shop answers with a counteroffer (last number is the ask) or a receipt.",
            tags=["shopping", "negotiation"] + sorted({i["category"] for i in items})[:20],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg, items), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def load_items(args):
    if not args.catalog:
        return BASIC
    items = json.load(open(args.catalog, encoding="utf-8"))
    if args.categories:
        keep = {c.strip() for c in args.categories.split(",")}
        items = [i for i in items if i["category"] in keep]
    if args.markup != 1.0:
        items = [dict(i, reserve=int(round(i["reserve"] * args.markup))) for i in items]
    return items


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--catalog")
    ap.add_argument("--categories")
    ap.add_argument("--markup", type=float, default=1.0)
    ap.add_argument("--name", default="Harness Shop")
    args = ap.parse_args()
    items = load_items(args)
    cfg = {"name": args.name, "port": args.port, "catalog": bool(args.catalog)}
    log(f"[seller] {cfg['name']} on :{args.port}, {len(items)} items, harness: {harness.NAME}")
    uvicorn.run(build(cfg, items), host="127.0.0.1", port=args.port, log_level="warning")
