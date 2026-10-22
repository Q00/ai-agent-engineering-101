"""Week 06 — the seller: my harness behind an A2A v1.0 server.

    python seller.py --port 9999

The A2A code moves messages; `claude -p` decides the opening ask, every
counteroffer, and whether to close or walk away. Two things the harness does
*not* decide, and the report says so: the reserve is enforced in code, and the
item is resolved in code before the harness sees anything.

Reserve in code, not only in the prompt. The prompt states it, but week 05
ended with a buyer that quoted its own budget correctly and then exceeded it,
so a limit that only lives in a prompt is a limit the model may drop. Here
every ask is clamped to >= reserve and no offer below reserve can close a
deal, whatever the harness answers. The trade test fails a single ask below
reserve, so this is also the only way to pass it reliably.

Challenge 1: the catalog is ~3000 items. It never goes into a prompt. A buyer
describes what it wants, `search()` scores the catalog in code and sends the
harness the best few candidates; the harness picks one of those.
"""
import argparse
import asyncio
import json
import math
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import (get_message_text, new_data_part, new_task_from_user_message,
                         new_text_part)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import harness
import receipts

OPEN_MULT_MAX = 2.0        # the harness may open at most this times the reserve
MAX_ROUNDS = 6             # counters before the seller may end the negotiation
CANDIDATES = 6             # catalog rows the harness is shown, out of ~3000

STOP = {"a", "an", "the", "some", "something", "to", "for", "of", "with", "and",
        "or", "my", "me", "i", "want", "buy", "need", "looking", "about", "that",
        "is", "it", "in", "on", "not", "no", "than", "keep", "hold", "would",
        "like", "can", "you", "do", "have", "any", "please", "one"}


def load_catalog(path: str) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP}


def search(catalog: list, text: str, k: int = CANDIDATES) -> list:
    """Narrow ~3000 rows to a handful, in code.

    Scoring is deliberately dull: word overlap with the name weighted above the
    description, plus a nudge for a numeric hint ("about 40 books") landing
    inside a capacity the description mentions. The point of challenge 1 is
    that the harness never sees the catalog, not that the search is clever.
    """
    q = tokens(text)
    if not q:
        return catalog[:k]
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    scored = []
    for it in catalog:
        name_t = tokens(it["name"])
        desc_t = tokens(it["description"])
        score = 3 * len(q & name_t) + len(q & desc_t)
        if it["name"].lower() in (text or "").lower():     # named outright
            score += 10
        for n in nums:
            for d in re.findall(r"\d+", it["description"]):
                if 0.5 * n <= int(d) <= 2 * n:
                    score += 2
        if score:
            scored.append((score, it))
    scored.sort(key=lambda s: (-s[0], s[1]["reserve"]))
    return [it for _, it in scored[:k]] or catalog[:k]


# ------------------------------------------------------------------ decisions

def kinds_of(catalog: list) -> list:
    """The ~25 distinct kinds. Small enough to put in a prompt; the 3000 rows
    are not."""
    seen, out = set(), []
    for it in catalog:
        k = it.get("kind") or it["name"]
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def pick_kind(catalog: list, text: str, log) -> str | None:
    """Stage one. Word overlap alone picks on surface words: 'a light that
    hangs over the dining table' scored a dining table above a pendant lamp,
    and 'a padded seat with arms, for reading in the evening' scored a reading
    light above an armchair -- in both the right row was not among the six the
    harness was shown, so it could only choose the least wrong one. Asking the
    harness for the kind first, from a list that does fit in a prompt, puts the
    judgement before the narrowing instead of after it."""
    ks = kinds_of(catalog)
    listing = "\n".join(f"{i}. {k}" for i, k in enumerate(ks))
    d = harness.ask(
        "A shopper described what they want. Which kind of product is it? "
        "Answer with the number of the single best kind, or -1 if none fit.\n\n"
        f"Shopper: {text!r}\n\nKinds:\n{listing}\n\n"
        'Reply with one JSON object and nothing else: {"kind": <number>}.',
        want=("kind",), tag=" kind")
    i = harness.as_int(d.get("kind"))
    if i is None or not (0 <= i < len(ks)):
        log(f"[seller] no usable kind from the harness ({d!r}); searching the whole catalog")
        return None
    log(f"[seller] harness kind -> {ks[i]}")
    return ks[i]


def pick_item(catalog: list, text: str, log) -> dict | None:
    """Code narrows, harness chooses -- twice. Returns a catalog row or None."""
    named = next((c for c in catalog if c["name"].lower() in (text or "").lower()), None)
    if named is not None:                 # a buyer that names the item needs no model
        log(f"[seller] named outright -> {named['name']}")
        return named

    kind = pick_kind(catalog, text, log)
    if kind is not None:
        pool = [c for c in catalog if (c.get("kind") or c["name"]) == kind]
        if not pool:
            # A shop that sells something whatever it is asked for makes a
            # directory pointless. It has to be able to say no.
            log(f"[seller] does not stock {kind!r}; declining")
            return None
    else:
        pool = catalog
    log(f"[seller] pool for search(): {len(pool)} of {len(catalog)} rows")
    cands = search(pool, text)
    if not cands:
        return None
    listing = "\n".join(f'{i}. {c["name"]}: {c["description"]}' for i, c in enumerate(cands))
    d = harness.ask(
        "A shopper described what they want. Choose the single best match from the "
        "numbered list, or -1 if none of them fit.\n\n"
        f"Shopper: {text!r}\n\nCandidates:\n{listing}\n\n"
        'Reply with one JSON object and nothing else: {"choice": <number>, "why": "<short>"}.',
        want=("choice",), tag=" pick")
    i = harness.as_int(d.get("choice"))
    if i is None or not (0 <= i < len(cands)):
        log(f"[seller] harness gave no usable choice ({d!r}); falling back to best search hit")
        return cands[0]
    log(f"[seller] harness picked {i}: {cands[i]['name']} ({d.get('why')})")
    return cands[i]


def opening_ask(item: dict, log) -> int:
    reserve = item["reserve"]
    d = harness.ask(
        "You are a shop deciding its opening asking price.\n"
        f"Item: {item['name']} - {item['description']}\n"
        f"Your reserve (the lowest you may ever accept) is {reserve}. "
        f"Open above it, with room to come down, and no higher than {int(reserve * OPEN_MULT_MAX)}.\n"
        'Reply with one JSON object and nothing else: {"ask": <whole number>}.',
        want=("ask",), tag=" open")
    ask = harness.as_int(d.get("ask"))
    if ask is None:
        ask = math.ceil(reserve * 1.3)
        log(f"[seller] no usable ask from the harness; falling back to {ask}")
    clamped = max(reserve, min(ask, int(reserve * OPEN_MULT_MAX)))
    if clamped != ask:
        log(f"[seller] clamped opening ask {ask} -> {clamped} (reserve {reserve})")
    return clamped


def respond(item: dict, ask: int, offer: int, rounds: int, log) -> tuple:
    """Returns (action, number). action is accept | counter | reject.

    The harness decides. Code then refuses anything that would break the
    reserve, which is why `accept` below reserve is downgraded rather than
    obeyed.
    """
    reserve = item["reserve"]
    d = harness.ask(
        "You are a shop in a price negotiation. Decide the next move.\n"
        f"Item: {item['name']}\n"
        f"Your reserve (the lowest you may ever accept) is {reserve}. Never go below it.\n"
        f"Your current asking price is {ask}. The buyer just offered {offer}. "
        f"This is round {rounds + 1} of at most {MAX_ROUNDS}.\n"
        "Accept if the offer is at or above your reserve. Otherwise counter with a "
        "price between your reserve and your current ask, moving down a little to keep "
        "the buyer talking. Reject only if the buyer will clearly never reach your reserve.\n"
        'Reply with one JSON object and nothing else: '
        '{"action": "accept" | "counter" | "reject", "price": <whole number>}.',
        want=("action",), tag=" move")
    action = str(d.get("action", "")).lower().strip()
    price = harness.as_int(d.get("price"))

    if action not in ("accept", "counter", "reject"):
        action = "accept" if offer >= reserve else "counter"
        log(f"[seller] no usable action from the harness; falling back to {action}")

    if action == "accept" and offer < reserve:
        # The model agreed to sell below the reserve. Code does not.
        log(f"[seller] REFUSED harness accept at {offer} < reserve {reserve}; countering instead")
        action = "counter"
        price = None
    if action == "accept":
        return "accept", offer
    if action == "reject":
        return "reject", offer

    if price is None:
        price = max(reserve, (offer + ask) // 2)
    clamped = max(reserve, min(price, ask))
    if clamped != price:
        log(f"[seller] clamped counter {price} -> {clamped} (reserve {reserve}, ask {ask})")
    return "counter", clamped


# ------------------------------------------------------------------ A2A server

class Seller(AgentExecutor):
    def __init__(self, cfg, catalog):
        self.cfg = cfg
        self.catalog = catalog
        # One key for the life of the shop: the market ties a shop's trades and
        # reviews to the public key the kit registered, so regenerating it would
        # throw away the shop's reputation.
        self.key = receipts.load_key(cfg.get("key_path", "shop_key.pem"))
        self.open = {}      # task id -> {"item", "ask", "rounds"}
        self.done = set()   # finished task ids, so a late message is an error

    def log(self, msg):
        harness.log(msg)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None and task.id in self.done:
            # The trade test sends one more message to a finished task and wants
            # an error. The SDK would happily reopen it.
            raise ValueError(f"task {task.id} is finished; start a new task")
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        loop = asyncio.get_running_loop()

        if deal is None:
            self.log(f"[seller] new task {task.id[:8]} | {text[:120]!r}")
            item = await loop.run_in_executor(None, pick_item, self.catalog, text, self.log)
            if item is None:
                await up.reject(say("I could not tell which item you mean. "
                                    "Describe it, or name it."))
                self.done.add(task.id)
                return
            ask = await loop.run_in_executor(None, opening_ask, item, self.log)
            self.open[task.id] = {"item": item, "ask": ask, "rounds": 0}
            self.log(f"[seller] {item['name']} reserve {item['reserve']} opening ask {ask}")
            await up.requires_input(say(
                f"{item['name']}: {item['description']} Asking {ask}. "
                f"Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say(
                f"{deal['item']['name']}: still asking {deal['ask']}. "
                f"Send your offer as a number."))
            return
        offer = int(m.group())
        item, reserve = deal["item"], deal["item"]["reserve"]
        action, price = await loop.run_in_executor(
            None, respond, item, deal["ask"], offer, deal["rounds"], self.log)

        if action == "accept":
            assert price >= reserve, "code guard: never sell below the reserve"
            # Week 07: the market only counts a deal whose receipt is signed
            # over {shop, item, price, task}. `task` is the A2A task id, and
            # `price` has to be a whole number -- a data Part would otherwise
            # hand the verifier 120.0 against a signature made over 120.
            receipt = receipts.sign_receipt(
                {"shop": self.cfg["name"], "item": item["name"],
                 "price": int(price), "task": task.id}, self.key)
            receipt["item_id"] = item["id"]      # extra keys are not signed
            self.log(f"[seller] DEAL {item['name']} at {price} (reserve {reserve}) "
                     f"signed task={task.id[:8]}")
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item['name']} at {price}."))
            self.open.pop(task.id, None)
            self.done.add(task.id)
            return

        deal["rounds"] += 1
        if action == "reject" or deal["rounds"] >= MAX_ROUNDS:
            self.log(f"[seller] ending at offer {offer} (round {deal['rounds']})")
            await up.reject(say(f"No deal at {offer}. My floor is firm."))
            self.open.pop(task.id, None)
            self.done.add(task.id)
            return

        deal["ask"] = price
        assert price >= reserve, "code guard: never ask below the reserve"
        self.log(f"[seller] counter {price} to offer {offer} (reserve {reserve})")
        await up.requires_input(say(f"{offer} is below what I can do. Counteroffer: {price}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        self.done.add(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id,
                          context_id=task.context_id).cancel()


def build(cfg, catalog):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url,
                                             protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            # Challenge 2: what a shop advertises is the only thing a buyer can
            # choose on before talking to it. With 20 identical cards the
            # directory is not a directory -- the first run bought every item
            # from Shop 00 for exactly that reason.
            description=cfg.get("skill_description")
            or ("Name or describe what you want; the shop answers with an asking "
                "price, then counteroffers, a receipt, or a refusal."),
            tags=["shopping", "negotiation", "furniture", "lighting", "storage"],
            examples=["I want to buy a lamp",
                      "something wooden to keep about 40 books, not taller than my desk",
                      "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg, catalog),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--name", default="Harness Shop")
    ap.add_argument("--stocks", default="",
                    help="comma-separated kinds this shop advertises on its Agent Card")
    args = ap.parse_args()
    catalog = load_catalog(args.catalog)
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    cfg = {"name": args.name, "port": args.port,
           "description": ("Sells " + ", ".join(stocks) + ". " if stocks else
                           "Sells furniture, lighting and storage from a large catalog. ")
                          + "Describe what you need; the shop negotiates the price.",
           "skill_description": ("Stocks " + ", ".join(stocks) + ". " if stocks else "")
                                + "Name or describe what you want; the shop answers with "
                                  "an asking price, then counteroffers, a receipt, or a "
                                  "refusal."}
    harness.log(f"[seller] {args.name} on :{args.port}, catalog {len(catalog)} items, "
                f"harness {harness.MODEL}")
    uvicorn.run(build(cfg, catalog), host="127.0.0.1", port=args.port, log_level="warning")
