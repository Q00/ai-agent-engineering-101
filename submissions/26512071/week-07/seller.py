"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

Started from the lab's rule-based seller_agent.py. The A2A side is unchanged; every
decision (asking price, counteroffers, accept, reject) comes from the harness:

    claude -p --model sonnet --tools "" --setting-sources "" --no-session-persistence

The harness answers in JSON. The reserve is in the prompt and is also enforced in code:
an ask below the reserve is raised to the reserve, and an accept below it becomes a
counteroffer at the reserve.

Challenge 1: the shop sells from catalog.json (3000 items). A first message that names
an item exactly ("I want to buy a lamp") goes straight to it. Otherwise two harness
calls: the first turns the request into a spec (kinds, sizes, books, seats), search.py
keeps the items that fit it and ranks them, and the second picks one of the top 20 by
id. Code checks that the id is one of the candidates, and the reserve comes from that
catalog entry.

Challenge 2: `--shop N` (1 to 20) turns this into one shop of a directory. Shop N sells
4 of the catalog's kinds (every kind is sold by several shops) with its reserves
multiplied by a seeded factor between 0.9 and 1.4, and its Agent Card says which kinds.
Without --shop it is the trade test's shop with the whole catalog.

Week 07: every receipt is {shop, item, price, task} plus sig_seller, an Ed25519
signature with shop_key.pem (made by market_kit.py or the market test) over the
canonical JSON of the four fields. `--market` sells only lamp, chair and bookshelf,
found by word in the first message: a market receipt counts only when its item is
the ordered item's name, so a catalog name like "... desk lamp 45x20" would not.
"""
import argparse
import asyncio
import base64
import json
import os
import random
import time
import re
import shutil
from collections import deque
import sys
import tempfile

import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from pathlib import Path

from search import Catalog

NAME = "Sonnet Shop"
CATALOG = Catalog()
SHOP_KINDS = 4
MARKET_ITEMS = ("lamp", "chair", "bookshelf")
MARKET_NAME = "Fair Furniture"
# Marshall Field, "give the lady what she wants": other words buyers use for the three items
SYNONYMS = {"bookcase": "bookshelf", "bookcases": "bookshelf", "bookshelves": "bookshelf",
            "shelf": "bookshelf", "shelves": "bookshelf", "light": "lamp", "lights": "lamp",
            "seat": "chair", "seats": "chair"}
# Ritz-Carlton, solve it on the spot: 111 market replies were "Send your offer as a number".
# A clear yes takes the current ask; a negation never does; anything else goes to the model.
YES = re.compile(r"^\W*(yes|yeah|yep|ok|okay|sure|deal|agreed|agree|accepted|i accept|accept|sounds good)\b", re.I)
NEG = re.compile(r"\b(no|not|never|cannot|can't|cant|won't|wont|don't|dont|decline|too|pass|walk)\b|n't\b", re.I)
TALKS = Path(__file__).with_name("talks.jsonl")  # kaizen: how every negotiation ended, per item
MARKET = False
KEY_FILE = Path(__file__).with_name("shop_key.pem")


def low_reviews():
    """How many low reviews have bought extra opening-price deals so far."""
    if not SEEN.exists():
        return 0
    return json.loads(SEEN.read_text(encoding="utf-8")).get("low", 0)


async def check_reviews():
    """Read this shop's reviews from the registry (at most once a minute); every review
    not seen before with LOW_STARS or fewer adds PER_LOW_REVIEW opening-price deals.
    The first read only marks what is already there as seen."""
    global _last_check
    if not (MARKET and NICK) or time.time() - _last_check < REVIEW_TTL:
        return
    _last_check = time.time()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            rows = (await c.get(REGISTRY)).json()
        rows = rows if isinstance(rows, list) else rows.get("shops", [])
        reviews = next((r.get("reviews", []) for r in rows if r.get("name") == NICK), [])
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log(f"[reviews] registry did not answer: {type(e).__name__}")
        return
    state = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else None
    keys = [json.dumps([r.get("stars"), r.get("review"), r.get("round")], ensure_ascii=False) for r in reviews]
    if state is None:
        SEEN.write_text(json.dumps({"seen": keys, "low": 0}, ensure_ascii=False), encoding="utf-8")
        log(f"[reviews] {len(keys)} existing reviews marked as seen")
        return
    new = [(k, r) for k, r in zip(keys, reviews) if k not in state["seen"]]
    low = [r for _, r in new if isinstance(r.get("stars"), (int, float)) and r["stars"] <= LOW_STARS]
    for r in low:
        log(f"[reviews] new {r['stars']}-star review {r.get('review', '')[:80]!r} -> {PER_LOW_REVIEW} more opening-price deals")
    if new:
        state["seen"] += [k for k, _ in new]
        state["low"] += len(low)
        SEEN.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def promo_left():
    if not (MARKET and PROMO):
        return 0
    done = SALES.read_text(encoding="utf-8").count('"promo": true') if SALES.exists() else 0
    return max(0, PROMO + PER_LOW_REVIEW * low_reviews() - done)


def reload_pricing():
    """BUSY from the SHOP_PRICING file, at most once a minute; a bad file keeps the current value."""
    global BUSY, _pricing_read
    if not PRICING or time.time() - _pricing_read < 60:
        return
    _pricing_read = time.time()
    try:
        busy = int(json.loads(Path(PRICING).read_text(encoding="utf-8"))["busy"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        log(f"[pricing] {PRICING} unusable ({type(e).__name__}), BUSY stays {BUSY}")
        return
    if busy > QUIET and busy != BUSY:
        log(f"[pricing] BUSY {BUSY} -> {busy}")
        BUSY = busy


def opening_mult():
    """The opening ask over the reserve: LIST when busy, 1.0 when quiet, linear in between."""
    now = time.time()
    while _visits and now - _visits[0] > DEMAND_WINDOW:
        _visits.popleft()
    return 1 + (LIST - 1) * min(1.0, max(0.0, (len(_visits) - QUIET) / (BUSY - QUIET)))


def record_talk(task_id, deal, outcome):
    if not (MARKET and NICK) or deal is None:  # NICK is set only by the market's caller, not by the tests
        return
    with TALKS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task_id,
                            "item": deal["item"]["name"], "outcome": outcome, "rounds": deal["rounds"],
                            "no_number": deal.get("no_number", 0), "first_ask": deal.get("first_ask"),
                            "last_ask": deal["ask"], "last_offer": deal.get("last_offer")}, ensure_ascii=False) + "\n")


def record_sale(entry):
    with SALES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt):
    """{shop, item, price, task} plus sig_seller over their canonical JSON."""
    key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(receipt))).decode())


def open_shop(n):
    """Shop n of the directory: its name, its kinds, and a catalog with its own reserves."""
    global NAME, CATALOG
    full = Catalog()
    kinds = [k for k in full.kinds if k not in ("lamp", "chair", "bookshelf")]  # the trade test's plain items
    mine = [kinds[(3 * n + j) % len(kinds)] for j in range(SHOP_KINDS)]
    factor = random.Random(n).uniform(0.9, 1.4)
    NAME = f"Sonnet Shop {n}"
    CATALOG = Catalog(items=[dict(i, reserve=int(i["reserve"] * factor)) for i in full.items if i["kind"] in mine])
    log(f"[shop] {NAME}: {', '.join(mine)}; reserves x{factor:.2f}; {len(CATALOG.items)} items")
# Challenge 1, pricing from the shop's own trades: in Round 1 it got 1 visit and one
# 3-star review ("Paid 103.0 of 105."), because it opened at about 1.5x the reserve and
# closed near the buyer's budget. Shop rank counts deals, not revenue.
PROMO = int(os.environ.get("SHOP_PROMO", "0"))  # opening-price deals, only when the kit sets it
PROMO_ASK = 1.05  # opening ask during the promo
# 10-21, back after 6 hours offline at rank 20: a price list on the market card, so a buyer's
# model can hold it against its budget before it visits (other cards only say "negotiable"),
# and under the list the opening ask follows demand: the list price when buyers keep coming,
# down to the reserve when the shop is quiet. Rank counts deals, so the list is the ceiling.
LIST = 1.05                   # list price over the reserve, shown on the card; no ask is above it
BUSY = int(os.environ.get("SHOP_BUSY", "30"))  # visits in the last DEMAND_WINDOW s that open at the list;
QUIET = 5                     # at QUIET or fewer open at the reserve; a huge SHOP_BUSY keeps every opening near the reserve
DEMAND_WINDOW = 600
_visits = deque()             # time of each first message
ALLIES = os.environ.get("SHOP_ALLIES", "")  # the alliance, disclosed on the card; same names as BUYER_ALLIES
# 10-22: every restart cost 15 min to 2 h of visits (a new tunnel URL each time), and the board now
# ranks by profit (revenue over the reserves). SHOP_PRICING names a JSON file, {"busy": 30}, read
# again at most once a minute, so the demand curve changes without a restart. Only BUSY moves:
# the list price is on the card, which is fixed at start.
PRICING = os.environ.get("SHOP_PRICING")
_pricing_read = 0.0
# Fair-price mode in the market, from 264 market reviews: shown ratings come from deals,
# 5-star deals were paid at about 0.81 of the buyer's budget and 4-star at 0.91, and the
# 2-star ones say the shop "held firm". So: any offer at or above the reserve closes,
# asks only come down, a reject becomes one more ask, and a slow harness never stalls a buyer.
STEP = 0.03         # each counter comes down at least 3% of the reserve
HARNESS_WAIT = 20   # seconds; after that the code makes the move
SALES = Path(__file__).with_name("sales.jsonl")  # every deal, for the before/after numbers
# Each new review of this shop with LOW_STARS or fewer stars buys PER_LOW_REVIEW more
# opening-price deals. The shop reads its own reviews from the market registry.
NICK = os.environ.get("SHOP_NICK")  # the shop's name in the market, set by the kit's caller
REGISTRY = "https://market.ourolabs.ai/api/shops"
LOW_STARS, PER_LOW_REVIEW, REVIEW_TTL = 3, 5, 60
SEEN = Path(__file__).with_name("reviews-seen.json")  # reviews already counted
_last_check = 0.0
CLOSE_GAP = 0.97  # accept an offer at or above the reserve within 3% of the current ask
MAX_ROUNDS = 10  # safety net so a task cannot run forever
HARNESS = [shutil.which("claude") or "claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="seller-harness-")  # empty dir: no project instructions

SPEC = """You work at a furniture shop. A buyer wrote:
{request}

Turn the request into a search spec. Kinds the shop sells: {kinds}.
Answer with one JSON object and nothing else; use null for anything the buyer does not say:
{{"kinds": [<kinds from the list that could fit>], "wooden": true | null,
 "max_height_cm": <int> | null, "min_height_cm": <int> | null, "max_width_cm": <int> | null,
 "books": <int> | null, "seats": <int> | null}}
Turn everyday references into numbers (a desk or a table is about 75 cm tall).
"""

PICK = """You are a furniture shop owner. A buyer wrote:
{request}

These catalog items matched a search for the request (id | description):
{candidates}

Pick the one item that best fits what the buyer describes, including any sizes or
amounts they mention. Listings are short and leave out most features: a feature the
listing does not mention (lumbar support, armrests, adjustable height) is not a reason
to pick none, so pick the closest item of the right kind. Pick none only when no
candidate is the kind of thing they want.
Answer with one JSON object and nothing else:
{{"id": "<one id from the list>" | null, "why": "<one short sentence>"}}
"""

PROMPT = """You are a shop owner negotiating with a buyer the price of this item: {item}
Your reserve price is {reserve}: never ask below it and never accept an offer below it.
Above the reserve, a higher price is better, but a deal is better than no deal.

Negotiation so far:
{history}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "ask" | "accept" | "reject", "price": <integer>, "message": "<one short sentence to the buyer, with no digits>"}}
The message must not contain any number (no sizes, no prices): the shop adds the price itself.
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
    if MARKET:  # only the three items an order can name, found by word
        words = re.findall(r"[a-z]+", request.lower())
        name = next((n for n in MARKET_ITEMS if n in words or n + "s" in words), None)
        name = name or next((SYNONYMS[w] for w in words if w in SYNONYMS), None)
        return CATALOG.by_name.get(name)
    spec = await ask_json(SPEC.format(request=request, kinds=", ".join(CATALOG.kinds)),
                          lambda d: isinstance(d.get("kinds"), list))
    log(f"[spec] {json.dumps(spec)}")
    candidates = CATALOG.search(request, spec=spec)
    if not candidates:
        return None
    log(f"[search] {len(candidates)} candidates: " + " ".join(c["id"] for c in candidates))
    by_id = {c["id"]: c for c in candidates}
    prompt = PICK.format(request=request, candidates="\n".join(f"{c['id']} | {c['description']}" for c in candidates))
    # code-side guard: only a candidate can be sold
    d = await ask_json(prompt, lambda d: d.get("id") is None or d.get("id") in by_id)
    return by_id.get(d["id"]) if d else None


async def ask_json(prompt, valid):
    """The harness's answer as a dict that passes valid(), or None after two tries."""
    for attempt in range(2):
        raw = await harness(prompt)
        log(f"[harness] {raw.strip()}")
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group())
            if isinstance(d, dict) and valid(d):
                return d
        except (AttributeError, TypeError, ValueError):
            pass
        log(f"[seller] unusable answer, attempt {attempt + 1}")
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
            await check_reviews()
            reload_pricing()
            item = await pick(text)
            if item is None:
                await up.reject(say("Sorry, nothing in our catalog fits that."))
                return
            _visits.append(time.time())
            log(f"[seller] task={task.id[:8]} picked {item['id']} {item['name']} reserve={item['reserve']}"
                + (f" demand={len(_visits)}/{DEMAND_WINDOW // 60}min open={opening_mult():.3f}x" if MARKET else ""))
            deal = self.open[task.id] = {"item": item, "ask": None, "rounds": 0,
                                         "history": [f"Buyer: {text}"]}
            offer = None
        else:
            m = re.search(r"\d+(?:\.\d+)?", text)
            if m is None and not MARKET:
                await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
                return
            deal["rounds"] += 1
            if m is not None:
                offer = int(float(m.group()))
                deal["history"].append(f"Buyer offers {offer}.")
            else:
                deal["no_number"] = deal.get("no_number", 0) + 1
                log(f"[no-number] task={task.id[:8]} {text[:200]!r}")
                if YES.search(text) and not NEG.search(text) and deal["ask"]:
                    offer = deal["ask"]  # a clear yes to my current ask
                    log(f"[no-number] read as yes -> offer {offer}")
                    deal["history"].append(f"Buyer accepts your ask of {offer}.")
                else:
                    offer = None  # a question or anything else: the model reads it
                    deal["history"].append(f"Buyer says (no number): {text[:200]}")
            deal["last_offer"] = offer if offer is not None else deal.get("last_offer")

        item, reserve = deal["item"], deal["item"]["reserve"]
        try:
            decision = await (asyncio.wait_for(decide(item, deal["history"]), HARNESS_WAIT) if MARKET
                              else decide(item, deal["history"]))
        except asyncio.TimeoutError:
            log(f"[harness] no answer in {HARNESS_WAIT} s, the code moves")
            decision = None
        if decision is None:  # harness failed or was slow: the fair-price rules below make the move
            action, price, message = "ask", deal["ask"] or round(reserve * LIST), ""
        else:
            action, price, message = decision
        if re.search(r"\d", message):  # 40 of 141 market asks carried sizes like "180 cm ... 150 books ... 340"
            log(f"[message] dropped, it had numbers: {message!r}")
            message = {"ask": "Here is my price.", "accept": "Thank you, it is a deal.",
                       "reject": "Sorry, I cannot go that low."}.get(action, "")

        promo = promo_left() > 0
        if MARKET:
            if offer is not None and (offer >= reserve or (deal["ask"] and offer >= deal["ask"])):
                action = "accept"                      # any offer at the reserve, or at my ask, closes
            elif action == "reject" and deal["rounds"] < MAX_ROUNDS:
                log("[fair] reject -> one more ask")    # a buyer who walks may still have had room
                action = "ask"
            if action == "ask":
                step = max(1, round(reserve * STEP))
                if deal["ask"] is None:
                    price = min(price, round(reserve * (PROMO_ASK if promo else opening_mult())))
                elif price > deal["ask"] - step:       # asks only come down, by at least one step
                    price = deal["ask"] - step
            if promo:
                message = f"New shop opening price. {message}"
        # close a near deal: in the market, buyers walked at offers 205 vs ask 208 and 270 vs 272
        if (action == "ask" and offer is not None and offer >= reserve
                and offer >= CLOSE_GAP * price):
            log(f"[close] offer {offer} within {1 - CLOSE_GAP:.0%} of ask {price} -> accept")
            action = "accept"
        # code-side guards on the reserve
        if action == "accept" and (offer is None or offer < reserve):
            log(f"[guard] accept of {offer} below reserve {reserve} -> ask {max(price, reserve)}")
            action, price = "ask", max(price, reserve)
        if action == "ask" and price < reserve:
            log(f"[guard] ask {price} below reserve {reserve} -> {reserve}")
            price = reserve
        if action == "ask" and deal["rounds"] >= MAX_ROUNDS:
            action = "reject"
        log(f"[seller] task={task.id[:8]} {item['id']} offer={offer} -> {action} {offer if action == 'accept' else price}"
            + (f" [promo, {promo_left()} left]" if promo else ""))

        if action == "accept":
            receipt = sign_receipt({"shop": NAME, "item": item["name"], "price": int(offer), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            # asks for an honest review, never for a score, and nothing depends on the review
            thanks = " New shop: an honest review helps us." if MARKET else ""
            await up.complete(say(f"{message}{thanks} Deal: {item['name']} at {offer}."))
            record_talk(task.id, deal, "deal")
            if MARKET and NICK:  # the market test runs --market too; its deals stay out
                record_sale({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task.id, "item": item["name"],
                             "price": int(offer), "reserve": reserve, "first_ask": deal.get("first_ask"),
                             "rounds": deal["rounds"], "promo": promo})
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"{message} No deal."))
            record_talk(task.id, deal, "reject")
            del self.open[task.id]
        else:
            deal["ask"] = price
            deal.setdefault("first_ask", price)
            deal["history"].append(f"You ask {price}.")
            # the asking price goes last: buyers read the last number as the ask
            await up.requires_input(say(f"{message} Asking price: {price}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        record_talk(task.id, self.open.pop(task.id, None), "buyer canceled")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=f"Sells {len(CATALOG.items)} pieces of furniture: {', '.join(CATALOG.kinds)}. "
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
            tags=["shopping", "negotiation"] + CATALOG.kinds,
            examples=["I want to buy a lamp", "something wooden to keep about 40 books", "90"],
        )],
    )
    if MARKET:  # the market card: plain facts a buyer's model can read at a glance, no instructions
        card.name = MARKET_NAME
        prices = ", ".join(f"{i['name']} {round(i['reserve'] * LIST)}" for i in CATALOG.items)
        card.description = (
            "Sells exactly three items: lamp, chair, bookshelf. "
            f"List prices: {prices}. The shop never asks more than the list price, and opens lower when it is quiet. "
            "An offer at or above the list price always closes at once. "
            + (f"Alliance, approved by the instructor: this shop's buyer prefers {ALLIES}; "
               "prices, budgets and reviews are not part of it. " if ALLIES else "") +
            "Every receipt is signed: sig_seller is Ed25519 over the canonical JSON of {shop, item, price, task}, "
            "so deals here count under the Round 2 rules. "
            "How to buy: send 'I want to buy a <item>', then send each offer as a plain number. "
            "The asking price is the last number in each reply; an offer the shop accepts closes the deal at that offer.")
        del card.skills[:]  # a protobuf repeated field: clear, then extend
        card.skills.extend([AgentSkill(
            id=f"buy-{n}", name=f"Buy a {n}",
            description=f"Negotiate the price of one {n}; a signed receipt with item '{n}' and a whole-number price closes the deal.",
            tags=[n, "furniture", "negotiation", "signed-receipt"],
            examples=[f"I want to buy a {n}", "120"]) for n in MARKET_ITEMS])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--shop", type=int, help="run as shop N of the challenge 2 directory")
    ap.add_argument("--market", action="store_true", help="sell only lamp, chair and bookshelf")
    args = ap.parse_args()
    if args.market:
        MARKET = True
        NAME = MARKET_NAME
        CATALOG = Catalog(items=[i for i in CATALOG.items if i["name"] in MARKET_ITEMS])
    if args.shop:
        open_shop(args.shop)
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
