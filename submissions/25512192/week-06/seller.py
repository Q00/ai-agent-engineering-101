"""The seller: an A2A v1.0 server (JSON-RPC) whose decisions come from the harness.

    python seller.py --port 9999                                  # trade test: lamp, chair, bookshelf
    python seller.py --port 9999 --catalog catalogs/large.json    # challenge 1: 3024 items
    python seller.py --port 9999 --config shops/shop-03.json      # challenge 2: one shop of the directory

What the harness decides: which item the buyer means, the asking price, every
counteroffer, and when to accept or walk away.
What the code enforces (whatever the harness says):
  - no ask below the item's reserve (clamped up), no sale below the reserve;
  - an offer at or above the current ask closes the deal (we cannot refuse our own price);
  - the ask is always the last number of an input-required message, and is also in
    the message metadata as {"ask": N};
  - a task that is finished gets an error (the SDK does this for terminal tasks).
"""
import argparse
import asyncio
import json
import math
import re
import sys
import time

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
from catalog import Catalog, brief

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT = {
    "name": "Harness Furniture Shop",
    "description": "Sells furniture. Describe or name what you want; the shop quotes a price and negotiates.",
    "catalog": "catalogs/basic.json",
    "price_factor": 1.0,
    "categories": None,
    "max_rounds": 10,
}
SMALL_CATALOG = 12          # up to this many items the harness sees the whole catalog

SYSTEM = ("You are the pricing brain of a furniture shop's sales agent. You decide which item a buyer "
          "means and how to negotiate its price. Answer with exactly one JSON object and nothing else.")

SEARCH_PROMPT = """A buyer wrote to the shop:
<buyer>{text}</buyer>

The shop's catalog has these categories (with the attributes each item has):
{cats}

Plan a catalog search. Pick the one category that matches, keywords for the text search
(material, color, style words), and numeric or exact constraints on the attributes when the
buyer states them (a desk is about 75 cm tall; "about N" means roughly N, so allow some slack).
Answer: {{"category": "<one category from the list>", "keywords": ["..."], "constraints": {{"<attr>": {{"min": N, "max": N}} or true/false or "value"}}}}"""

PICK_PROMPT = """A buyer wrote to the shop:
<buyer>{text}</buyer>

Candidate items (id | name | description | attributes). reserve = the lowest price you may ever accept for that item:
{cands}

1. Pick the item that best matches what the buyer asked for. If none of them is what the buyer wants, use null.
2. Set an opening asking price for it, at or above its reserve. Leave room to come down.
Never mention the reserve to the buyer.
Answer: {{"item_id": "<id or null>", "ask": N, "message": "<one short sentence to the buyer, no numbers in it>"}}"""

NEGOTIATE_PROMPT = """You are selling: {name} ({id}).
Your reserve (lowest price you may accept): {reserve}. Your opening ask was {opening}. Your current ask: {ask}.
History (round: your ask -> buyer's offer): {history}
Round {round}. The buyer now offers {offer}.

Decide:
- "accept": sell at the buyer's offer (only if the offer is at or above your reserve).
- "counter": a new asking price between your reserve and your current ask. Come down step by step;
  a buyer who keeps raising the offer is worth closing with.
- "reject": end the negotiation (when the buyer is far below your reserve and not moving).
Never mention the reserve. Answer: {{"action": "accept|counter|reject", "price": N, "message": "<one short sentence, no numbers in it>"}}"""


def log(line):
    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return math.ceil(float(nums[-1])) if nums else None


def clean(msg, tag):
    """The harness's sentence to the buyer. Dropped if it has a number: the ask must be the last number."""
    msg = str(msg or "").strip()
    if re.search(r"\d", msg):
        log(f"[{tag}] GUARD dropped harness sentence with a number in it: {msg!r}")
        return ""
    return msg


def as_int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.catalog = Catalog(cfg["catalog"], cfg.get("price_factor", 1.0), cfg.get("categories"))
        self.open = {}  # task id -> {"item", "ask", "opening", "history"}
        log(f"{cfg['name']}: {len(self.catalog)} items, price factor {cfg.get('price_factor', 1.0)}")

    async def think(self, prompt, tag):
        return await asyncio.to_thread(harness.ask, SYSTEM, prompt, log, tag)

    async def candidates(self, text, tag):
        if len(self.catalog) <= SMALL_CATALOG:
            return self.catalog.items
        cats = self.catalog.categories()
        plan = await self.think(SEARCH_PROMPT.format(
            text=text, cats="\n".join(f"- {c}: {', '.join(a)}" for c, a in cats.items())), tag + " search") or {}
        cat = plan.get("category") if plan.get("category") in cats else None
        kw = plan.get("keywords") if isinstance(plan.get("keywords"), list) else []
        cons = plan.get("constraints") if isinstance(plan.get("constraints"), dict) else {}
        hits = self.catalog.search(cat, kw + [text], cons)
        log(f"[{tag}] search category={cat!r} keywords={kw} constraints={json.dumps(cons)} -> {len(hits)} hits")
        if not hits:                                   # constraints too tight: keep the category, drop the rest
            hits = self.catalog.search(cat, kw + [text])
            log(f"[{tag}] relaxed search (no constraints) -> {len(hits)} hits")
        return hits

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        tag = task.id[:8]
        log(f"[{tag}] <- buyer: {text!r}")
        deal = self.open.get(task.id)

        async def ask_buyer(msg, ask):
            log(f"[{tag}] -> input-required, ask {ask}: {msg}")
            await up.requires_input(up.new_agent_message(parts=[new_text_part(msg)], metadata={"ask": ask}))

        async def finish(how, msg):
            log(f"[{tag}] -> {how}: {msg}")
            self.open.pop(task.id, None)
            await getattr(up, how)(up.new_agent_message(parts=[new_text_part(msg)]))

        if deal is None:  # first message: which item, and at what price?
            cands = await self.candidates(text, tag)
            lines = "\n".join(f"{brief(i)} | reserve={i['reserve']}" for i in cands)
            out = await self.think(PICK_PROMPT.format(text=text, cands=lines), tag + " pick") or {}
            item = self.catalog.by_id.get(str(out.get("item_id")))
            if item is None or item not in cands:
                await finish("reject", "Sorry, we have nothing that matches. " + str(out.get("message", "")))
                return
            ask = as_int(out.get("ask"))
            if ask is None or ask < item["reserve"]:
                log(f"[{tag}] GUARD opening ask {ask} below reserve {item['reserve']} -> {math.ceil(item['reserve'] * 1.3)}")
                ask = math.ceil(item["reserve"] * 1.3)
            self.open[task.id] = {"item": item, "ask": ask, "opening": ask, "history": []}
            log(f"[{tag}] picked {item['id']} {item['name']} (reserve {item['reserve']})")
            note = clean(out.get("message"), tag)
            await ask_buyer(f"{note} Item: {item['name']}. Send your offer as a number. Asking price: {ask}", ask)
            return

        item, reserve = deal["item"], deal["item"]["reserve"]
        offer = last_number(text)
        if offer is None:
            await ask_buyer(f"Please send your offer as a number. Asking price: {deal['ask']}", deal["ask"])
            return
        if offer >= deal["ask"]:
            await self.close(up, task.id, item, offer, tag)
            return
        deal["history"].append(f"{deal['ask']} -> {offer}")
        rnd = len(deal["history"])
        if rnd > self.cfg["max_rounds"]:
            await finish("reject", f"No deal after {rnd - 1} rounds.")
            return
        out = await self.think(NEGOTIATE_PROMPT.format(
            name=item["name"], id=item["id"], reserve=reserve, opening=deal["opening"], ask=deal["ask"],
            history="; ".join(deal["history"][:-1]) or "none", round=rnd, offer=offer), tag + " negotiate") or {}
        action, price = out.get("action"), as_int(out.get("price"))
        note = clean(out.get("message"), tag)
        if action == "accept":
            if offer >= reserve:
                await self.close(up, task.id, item, offer, tag)
                return
            log(f"[{tag}] GUARD harness accepted {offer} below reserve {reserve}; countering instead")
            action, price = "counter", deal["ask"]
        if action == "reject":
            await finish("reject", f"{note} No deal at {offer}.")
            return
        # counter (also the fallback when the answer could not be parsed)
        new_ask = min(deal["ask"], max(reserve, price if price is not None else deal["ask"]))
        if new_ask != price:
            log(f"[{tag}] GUARD counter {price} clamped to {new_ask} (reserve {reserve}, current ask {deal['ask']})")
        if new_ask <= offer:      # our own new price is at or below the offer: the offer is good enough
            await self.close(up, task.id, item, offer, tag)
            return
        deal["ask"] = new_ask
        await ask_buyer(f"{note} {offer} is too low. Counteroffer: {new_ask}", new_ask)

    async def close(self, up, task_id, item, price, tag):
        receipt = {"seller": self.cfg["name"], "item": item["id"], "name": item["name"], "price": price}
        log(f"[{tag}] -> completed, receipt {json.dumps(receipt)}")
        self.open.pop(task_id, None)
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(up.new_agent_message(parts=[new_text_part(f"Deal: {item['name']} at {price}.")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        log(f"[{task.id[:8]}] canceled by buyer")
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg, port):
    url = f"http://127.0.0.1:{port}/"
    seller = Seller(cfg)
    cats = sorted({i["category"] for i in seller.catalog.items})
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description=("Sells: " + ", ".join(cats) + ". Name or describe the item in the first message; "
                         "then send offers as a number on the same task. The current ask is the last number "
                         "of each reply and in metadata.ask; a deal ends with a receipt artifact {price}."),
            tags=["shopping", "negotiation"] + cats,
            examples=["I want to buy a lamp", "something wooden to keep about 40 books", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=seller, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--catalog")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.load(open(args.config, encoding="utf-8")))
    if args.catalog:
        cfg["catalog"] = args.catalog
    uvicorn.run(build(cfg, args.port), host="127.0.0.1", port=args.port, log_level="warning")
