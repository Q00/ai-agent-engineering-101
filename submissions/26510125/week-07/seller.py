"""Week 06 — the seller: an A2A v1.0 server (JSON-RPC) whose prices come from the harness.

    python seller.py --port 9999
    python seller.py --port 9101 --config shops/shop_01.json   # challenge 2: one of many shops

The A2A side is the lab's seller_agent.py. What changed: the rules are gone.
  * which item      — the buyer may name it ("lamp") or describe it ("something
                      wooden to keep about 40 books"); code narrows the catalog
                      to a few candidates by keyword search, the harness picks one
  * the opening ask — the harness, given the item and its reserve
  * each reply      — the harness: counter / accept / reject
Code enforces the two invariants the trade test checks, whatever the harness
says: never ask below the item's reserve, never sell below it. Every message
the seller sends ends with the current ask as its last number.
"""
import argparse
import base64
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import harness

# ---------------------------------------------------------------- receipt signing (week 07)

def canon(d: dict) -> bytes:
    """The bytes both sides sign and verify: keys sorted, no spaces, UTF-8."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load_key(path: str):
    """shop_key.pem: created by market_kit.py / the market test; generated here only if absent."""
    p = Path(path)
    if not p.exists():
        key = Ed25519PrivateKey.generate()
        p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
        print(f"[keys] created {p} (never commit it)", flush=True)
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def sign_receipt(key, shop: str, item: str, price: int, task: str) -> dict:
    """{shop, item, price, task} + sig_seller. price is signed as a whole number: A2A data
    Parts carry numbers as doubles, so the verifier turns 120.0 back into 120 before checking."""
    body = {"shop": shop, "item": item, "price": int(price), "task": task}
    body["sig_seller"] = base64.b64encode(key.sign(canon({k: body[k] for k in ("shop", "item", "price", "task")}))).decode()
    return body


# ---------------------------------------------------------------- pricing from own trades and reviews (challenge 1)

class Pricing:
    """The shop reads its own trades (shop_trades.json, written here after every negotiation) and
    its reviews on the market board, and moves the OPENING-ASK factor per item. Reserves never move,
    and `ask >= reserve` is still enforced in code after the harness answers. The factor goes down
    when recent negotiations for the item ended without a deal or reviews are poor, and follows the
    price level at which deals actually closed. Network trouble never stops the shop."""
    BASE, LOW, HIGH = 1.5, 1.05, 1.9

    def __init__(self, name, path, market):
        self.name = name
        self.path = Path(path or "shop_trades.json")
        self.market = (market or "https://market.ourolabs.ai").rstrip("/")
        self._reviews, self._reviews_at = None, 0.0

    def trades(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def record(self, item, deal, outcome, price):
        t = self.trades()
        t.append({"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "item": item["name"], "reserve": item["reserve"],
                  "opening": deal.get("opening"), "final_ask": deal.get("ask"), "rounds": deal.get("rounds", 0),
                  "outcome": outcome, "price": price})
        try:
            self.path.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def reviews(self):
        if time.time() - self._reviews_at < 600 and self._reviews is not None:
            return self._reviews
        try:
            with urllib.request.urlopen(f"{self.market}/api/board?round=2", timeout=5) as r:
                board = json.load(r)
            self._reviews = [x for x in board.get("reviews", []) if x.get("shop") == self.name]
        except Exception:
            self._reviews = self._reviews or []
        self._reviews_at = time.time()
        return self._reviews

    def suggest(self, item):
        """(low, high, why): the opening-ask range to give the harness, and the reasons."""
        recent = [t for t in self.trades() if t["item"] == item["name"]][-6:]
        f, why = self.BASE, []
        if recent:
            deals = [t for t in recent if t["outcome"] == "deal" and t.get("price")]
            lost = len(recent) - len(deals)
            if deals:
                ratio = statistics.mean(t["price"] / t["reserve"] for t in deals)
                f = (f + ratio * 1.1) / 2                      # drift toward where deals actually close
                why.append(f"{len(deals)} deal(s) closed at ~{ratio:.2f}x reserve")
            if lost >= 2 and lost >= len(deals):
                f -= 0.15 * lost                                # a streak of no-deals: open lower
                why.append(f"{lost} of the last {len(recent)} ended without a deal")
        rs = self.reviews() or []
        stars = [x["stars"] for x in rs if isinstance(x.get("stars"), (int, float))]
        if stars:
            avg = statistics.mean(stars)
            if avg < 3:
                f -= 0.1
            why.append(f"my reviews average {avg:.1f} over {len(stars)}")
        f = max(self.LOW, min(self.HIGH, f))
        low = math.ceil(item["reserve"] * max(self.LOW, f - 0.1))
        high = math.ceil(item["reserve"] * (f + 0.1))
        return low, high, "; ".join(why) or "no past trades for this item yet"


DEFAULT = {
    "key": "shop_key.pem",
    "trades": "shop_trades.json",
    "market_url": "https://market.ourolabs.ai",
    "name": "Haiku Furniture",
    "description": "Sells lamps, chairs, bookshelves, desks, tables and more from a large catalog. Negotiates the price.",
    "skill_description": "Name an item or describe what you need, then send a price. The shop answers with a counteroffer or a receipt.",
    "catalog": "catalog.json",
    "price_factor": 1.0,       # challenge 2: shops differ in price
    "categories": None,        # challenge 2: shops differ in what they stock (None = everything)
    "max_rounds": 6,
}
STOP = {"a", "an", "the", "i", "want", "to", "buy", "need", "some", "something", "for", "my", "with", "and", "of",
        "that", "is", "not", "than", "about", "please", "would", "like", "looking", "me", "it", "in", "on", "at"}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- catalog search (challenge 1)

def tokens(text: str):
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP]


def load_catalog(cfg):
    items = json.loads(Path(cfg["catalog"]).read_text(encoding="utf-8"))
    if cfg.get("categories"):
        items = [i for i in items if any(c in i["name"] for c in cfg["categories"])]
    for i in items:
        i["reserve"] = max(1, math.ceil(i["reserve"] * cfg.get("price_factor", 1.0)))
    return items


KINDS = ["lamp", "chair", "bookshelf", "desk", "table", "sofa", "cabinet", "stool", "mirror", "rug", "bed frame", "nightstand"]
SYNONYMS = {  # what buyers say -> the catalog's words
    "books": ["bookshelf", "shelves"], "book": ["bookshelf"], "shelf": ["bookshelf", "shelves"], "shelving": ["bookshelf"],
    "light": ["lamp"], "lighting": ["lamp"], "reading": ["lamp"], "bedside": ["nightstand", "lamp"],
    "sit": ["chair", "stool"], "seat": ["chair", "stool"], "seating": ["chair", "sofa"], "couch": ["sofa"],
    "wooden": ["oak", "pine", "walnut", "birch", "bamboo"], "wood": ["oak", "pine", "walnut", "birch", "bamboo"],
    "metal": ["steel", "aluminum"], "small": ["compact", "narrow", "low"], "big": ["wide", "tall"], "large": ["wide", "tall"],
    "storage": ["cabinet", "shelves", "drawer"], "work": ["desk"], "office": ["desk"], "dining": ["table"], "eat": ["table"],
    "carpet": ["rug"], "sleep": ["bed"], "mattress": ["bed"],
}


def search(catalog, text: str, k: int = 8):
    """Narrow the catalog for the harness: exact-name hit first, else weighted keyword
    overlap with synonyms, and the top-k spread over item kinds so the harness can
    still choose the right kind when a stray word ("...than my desk") ranks another."""
    low = text.lower()
    exact = [i for i in catalog if re.search(rf"\b{re.escape(i['name'])}\b", low)]
    if exact:
        return sorted(exact, key=lambda i: len(i["name"]))[:1]
    q = set(tokens(text))
    for w in list(q):
        q.update(SYNONYMS.get(w, []))
    # a kind word right after "my"/"than"/"like" describes the buyer's room, not the item
    demoted = {m.group(1) for m in re.finditer(r"\b(?:my|than|like|beside|next to)\s+(\w+)", low)}
    scored = []
    for i in catalog:
        kind = next((kd for kd in KINDS if i["name"].endswith(kd)), "")
        words = set(tokens(i["name"] + " " + i["description"]))
        score = len(q & words) + 2 * len(q & set(tokens(i["name"])))
        if kind and any(kw in q for kw in kind.split()):
            score += 4 if kind.split()[-1] not in demoted else -2
        if score > 0:
            scored.append((score, i))
    scored.sort(key=lambda s: -s[0])
    picked, per_kind = [], {}
    for _, i in scored:                                  # at most k, no kind takes more than half
        kind = next((kd for kd in KINDS if i["name"].endswith(kd)), i["name"])
        if per_kind.get(kind, 0) >= max(2, k // 2):
            continue
        picked.append(i); per_kind[kind] = per_kind.get(kind, 0) + 1
        if len(picked) >= k:
            break
    return picked


# ---------------------------------------------------------------- harness decisions

def choose_item(candidates, text):
    if len(candidates) == 1:
        return candidates[0], "exact name"
    cands = "\n".join(f'- id={c["id"]} name="{c["name"]}" desc="{c["description"]}"' for c in candidates)
    data = harness.ask_json(
        f"You are a shop assistant. A buyer wrote: {text!r}\n"
        f"Which ONE catalog item fits best? Candidates:\n{cands}\n"
        'Reply with exactly one JSON object: {"id": "<id>"} or {"id": null} if none fits.', log)
    if data and data.get("id"):
        hit = next((c for c in candidates if c["id"] == data["id"]), None)
        if hit:
            return hit, "harness pick"
    harness.CALLS["fallbacks"] += 1
    return (candidates[0], "fallback: top search hit") if candidates else (None, "none")


def opening_ask(item, pricing=None):
    hint, low = "", None
    if pricing is not None:                                  # challenge 1: own trades + reviews -> opening range
        low, high, why = pricing.suggest(item)
        log(f"    [price] {item['name']}: suggested opening {low}-{high} ({why})")
        hint = (f" Your recent experience with this item: {why}. Based on it, open between {low} and {high} "
                f"unless you have a strong reason not to.")
    data = harness.ask_json(
        f"You are the seller of a used {item['name']} ({item['description']}). "
        f"Your reserve price is {item['reserve']}: you must never ask or sell below it, and you want clearly more."
        f"{hint} Choose an opening asking price. Reply with exactly one JSON object: {{\"ask\": <integer>}}.", log)
    ask = _int(data and data.get("ask"))
    if ask is None:
        harness.CALLS["fallbacks"] += 1
        ask = low or math.ceil(item["reserve"] * 1.3)
    return max(ask, item["reserve"])            # code enforces the reserve


def respond(item, deal, offer):
    hist = "; ".join(f"you asked {a}, buyer offered {o}" for a, o in deal["history"])
    data = harness.ask_json(
        f"You are the seller of a used {item['name']}. Reserve price {item['reserve']} (never sell or ask below it). "
        f"Negotiation so far: {hist or 'none'}. Your current ask is {deal['ask']}. The buyer now offers {offer}. "
        f"Round {deal['rounds'] + 1} of at most {deal['max_rounds']}. "
        'Decide: accept the offer, counter with a new ask, or reject and end. Reply with exactly one JSON object: '
        '{"action": "accept"} or {"action": "counter", "price": <integer>} or {"action": "reject"}.', log)
    action = (data or {}).get("action")
    price = _int((data or {}).get("price"))
    if action not in ("accept", "counter", "reject"):
        harness.CALLS["fallbacks"] += 1
        action = "accept" if offer >= item["reserve"] else "counter"
        price = max(item["reserve"], (offer + deal["ask"]) // 2)
    if action == "accept" and offer < item["reserve"]:        # code enforces the reserve
        log(f"    [guard] harness accepted {offer} < reserve {item['reserve']}; countering instead")
        action, price = "counter", item["reserve"]
    if action == "counter":
        if price is None or price < item["reserve"]:
            log(f"    [guard] counter {price} below reserve {item['reserve']}; raised to the reserve")
            price = item["reserve"]
        price = min(price, deal["ask"]) if price <= deal["ask"] else price   # may lower, never below reserve
    return action, price


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- A2A executor

class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.catalog = load_catalog(cfg)
        self.key = load_key(cfg["key"])
        self.pricing = Pricing(cfg["name"], cfg.get("trades"), cfg.get("market_url"))
        self.open = {}   # task id -> deal

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log(f"[{task.id[:8]}] <- {text[:200]!r}")

        if deal is None:                                   # first message: which item, and the opening ask
            candidates = search(self.catalog, text)
            item, how = choose_item(candidates, text)
            if item is None:
                log(f"[{task.id[:8]}] no item matches; rejecting")
                await up.reject(say("Sorry, nothing in the catalog matches that."))
                return
            ask = opening_ask(item, self.pricing)
            self.open[task.id] = {"item": item, "ask": ask, "opening": ask, "rounds": 0, "history": [],
                                  "max_rounds": self.cfg["max_rounds"]}
            log(f"[{task.id[:8]}] item={item['name']!r} ({how}, reserve {item['reserve']}) opening ask {ask}")
            await up.requires_input(say(f"{item['name']}: asking {ask}."))
            return

        item = deal["item"]
        m = re.findall(r"\d+(?:\.\d+)?", text)
        if not m:
            await up.requires_input(say(f"Please send your offer as a number. Current ask: {deal['ask']}."))
            return
        offer = math.ceil(float(m[-1]))
        action, price = respond(item, deal, offer)
        deal["history"].append((deal["ask"], offer))
        deal["rounds"] += 1
        log(f"[{task.id[:8]}] offer {offer} -> {action}" + (f" {price}" if price else ""))

        if action == "accept":
            # week 07: the receipt is {shop, item, price, task} + sig_seller; task = this A2A task's id
            receipt = sign_receipt(self.key, self.cfg["name"], item["name"], offer, task.id)
            log(f"[{task.id[:8]}] receipt signed: {json.dumps({k: receipt[k] for k in ('shop', 'item', 'price', 'task')})}")
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item['name']} at {offer}."))
            self.pricing.record(item, deal, "deal", offer)
            del self.open[task.id]
        elif action == "reject" or deal["rounds"] >= deal["max_rounds"]:
            await up.reject(say(f"No deal. Final ask was {deal['ask']}."))
            self.pricing.record(item, deal, "no_deal", None)
            del self.open[task.id]
        else:
            deal["ask"] = price
            await up.requires_input(say(f"{offer} is too low for the {item['name']}. Counteroffer: {price}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            self.pricing.record(deal["item"], deal, "walked", None)
        log(f"[{task.id[:8]}] canceled by the buyer")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"], description=cfg["description"], version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase", description=cfg["skill_description"],
                           tags=["shopping", "negotiation"] + (cfg.get("categories") or ["furniture", "lamp", "chair", "bookshelf"]),
                           examples=["I want to buy a lamp", "something wooden to keep about 40 books", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
    cfg["port"] = args.port
    log(f"seller {cfg['name']!r} on :{cfg['port']} · harness: {harness.COMMAND} · catalog {cfg['catalog']}")
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
