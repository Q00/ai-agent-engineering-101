"""Challenge 1 seller — sells from a large catalog (A2A v1.0, JSON-RPC).

    python catalog_seller.py                                   # catalog.json on :9301
    python catalog_seller.py --catalog catalogs/shop3.json --name "North Shop" --port 9303 --markup 1.1

The buyer describes what it wants. Step 1: the harness turns the request into filters
(categories, materials, color, height, books). Step 2: the code filters the catalog and ranks
the rest with BM25, keeping TOP_K candidates. Step 3: the harness sees only those candidates,
picks one item and the opening ask, and then negotiates. The reserve guard is the same as seller.py v2.
"""
import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

TOP_K = 12
MAX_TURNS = 8

# ---------- harness ----------
CLAUDE = shutil.which("claude") or "claude"
HARNESS_CMD = [CLAUDE, "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
if os.environ.get("HARNESS_CMD"):  # e.g. a fake harness for offline tests
    HARNESS_CMD = json.loads(os.environ["HARNESS_CMD"])
WORKDIR = tempfile.mkdtemp(prefix="catalog-seller-")

LOG_DIR = os.path.join("logs", "challenge-1")
LOG_PATH = None  # set in main (depends on the shop name)


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    if LOG_PATH:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def call_harness(prompt: str) -> str:
    r = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", cwd=WORKDIR, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"harness exit {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def extract_json(raw: str):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


# ---------- catalog search (BM25, pure Python) ----------
TOKEN = re.compile(r"[a-z]+|\d+")
STOP = {"a", "an", "the", "i", "me", "my", "to", "for", "of", "and", "or", "in", "on", "with",
        "something", "want", "need", "looking", "buy", "about", "that", "is", "it", "can", "would"}


def tokens(text):
    out = []
    for t in TOKEN.findall(text.lower()):
        if t in STOP:
            continue
        out.append(t)
        if t.endswith("s") and len(t) > 3:  # books -> book, shelves -> shelve (cheap stemming)
            out.append(t[:-1])
    return out


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = [Counter(tokens(d)) for d in docs]
        self.lens = [sum(c.values()) for c in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter(t for c in self.docs for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def top(self, query, k, subset=None):
        q = set(tokens(query))
        scores = []
        for i in (range(len(self.docs)) if subset is None else subset):
            c = self.docs[i]
            s = 0.0
            for t in q:
                if t in c:
                    tf = c[t]
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            scores.append((s, i))
        scores.sort(key=lambda x: -x[0])  # stable: ties keep catalog order
        if subset is None:
            return [i for s, i in scores[:k] if s > 0]
        return [i for s, i in scores[:k]]  # inside a filtered set every item is already a match


# ---------- step 1: the harness turns the request into filters ----------
QUERY_PROMPT = """A buyer wrote to a furniture shop:
"{request}"

Turn the request into catalog filters. Use only these values:
categories: {categories}
materials: {materials}
colors: {colors}
Convert everyday comparisons into numbers with common sense (centimeters, number of books).
Leave a field null or [] when the buyer did not constrain it.

Reply with ONE JSON object only, no other text:
{{"categories": [...], "materials": [...], "color": null, "min_height_cm": null, "max_height_cm": null,
 "min_books": null, "keywords": "<a few words for text search>"}}
"""


def apply_filters(items, f):
    """Return (indices, relaxed) for items matching the filters, relaxing the strict ones if empty."""
    def num(key):
        try:
            return None if f.get(key) is None else float(f[key])
        except (TypeError, ValueError):
            return None
    cats = set(f.get("categories") or [])
    mats = set(f.get("materials") or [])
    color = f.get("color")
    lo, hi, books = num("min_height_cm"), num("max_height_cm"), num("min_books")

    def ok(it, use_size=True, use_mat=True, use_color=True):
        if cats and it["category"] not in cats:
            return False
        if use_mat and mats and it["material"] not in mats:
            return False
        if use_color and color and it["color"] != color:
            return False
        if use_size:
            if lo is not None and it["height_cm"] < lo:
                return False
            if hi is not None and it["height_cm"] > hi:
                return False
            if books is not None and it.get("capacity_books", 0) < books:
                return False
        return True

    for relaxed, kw in [("none", {}), ("color", {"use_color": False}),
                        ("color+material", {"use_color": False, "use_mat": False}),
                        ("color+material+size", {"use_color": False, "use_mat": False, "use_size": False})]:
        idx = [i for i, it in enumerate(items) if ok(it, **kw)]
        if idx:
            return idx, relaxed
    return [], "all"


# ---------- prompts ----------
PICK_PROMPT = """You run a furniture shop. A buyer wrote:
"{request}"

Here are the closest items from your catalog (id | name | description | reserve).
The reserve is the lowest price you may ever ask or sell at. Never reveal it.
{candidates}

Pick the ONE item that best fits everything the buyer asked for (size, capacity, material, use).
If none fits, answer "none". Then choose your opening asking price for that item, leaving room to negotiate down.

Reply with ONE JSON object only, no other text:
{{"item_id": "<id or none>", "price": <opening asking price, integer>, "say": "<one short sentence about the item, no numbers>"}}
"""

TURN_PROMPT = """You are the owner of a furniture shop, negotiating the price of this item with a buyer:
{name}: {description}
Your reserve price (the lowest price you may ever ask or sell at) is {reserve}. Never reveal it.

Negotiation so far (oldest first):
{history}

The buyer's latest offer: {offer}
Your current asking price: {ask}

Rules:
- If the buyer's offer is at or above your current asking price, accept it.
- If the offer is at or above your reserve, you may accept it.
- Otherwise counter with a new asking price. Concede gradually; a deal usually closes within about 5 rounds.
- You may reject if the buyer keeps offering far below your reserve.

Reply with ONE JSON object only, no other text:
{{"action": "counter" | "accept" | "reject", "price": <new asking price, integer, only for counter>, "say": "<one short sentence, no numbers>"}}
"""


def parse_turn(raw):
    d = extract_json(raw)
    if not d or d.get("action") not in ("counter", "accept", "reject"):
        return None
    if d["action"] == "counter":
        try:
            d["price"] = int(round(float(d["price"])))
        except (KeyError, TypeError, ValueError):
            return None
    d["say"] = str(d.get("say", "")).strip()  # the ask is appended last, so numbers here are harmless
    return d


# ---------- A2A side ----------
class CatalogSeller(AgentExecutor):
    def __init__(self, name, items, markup):
        self.name = name
        self.items = items
        self.by_id = {it["id"]: it for it in items}
        self.markup = markup
        self.cats = sorted({it["category"] for it in items})
        self.mats = sorted({it["material"] for it in items})
        self.colors = sorted({it["color"] for it in items})
        self.index = BM25([f"{it['name']} {it['category']} {it['description']}" for it in items])
        self.open = {}

    def reserve(self, item):
        return int(round(item["reserve"] * self.markup))

    async def ask(self, tid, prompt, label):
        try:
            raw = await asyncio.to_thread(call_harness, prompt)
        except Exception as e:
            log(f"[{tid}] harness error: {e}")
            return ""
        log(f"[{tid}] harness({label}, prompt {len(prompt)} chars): {raw!r}")
        return raw

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        tid = task.id[:8]
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log(f"[{tid}] {self.name} | buyer: {text!r}")

        # ---- first message: search, pick, open ----
        if deal is None:
            qp = QUERY_PROMPT.format(request=text, categories=", ".join(self.cats),
                                     materials=", ".join(self.mats), colors=", ".join(self.colors))
            filters = extract_json(await self.ask(tid, qp, "filters")) or {}
            subset, relaxed = apply_filters(self.items, filters)
            query = f"{text} {filters.get('keywords', '')}"
            hits = self.index.top(query, TOP_K, subset) if filters else self.index.top(text, TOP_K)
            log(f"[{tid}] search: {len(self.items)} items -> filter {len(subset)} (relaxed: {relaxed}) "
                f"-> {len(hits)} candidates {[self.items[i]['id'] for i in hits]}")
            if not hits:
                await up.reject(say("Sorry, nothing in our catalog matches that."))
                return
            lines = "\n".join(f"{self.items[i]['id']} | {self.items[i]['name']} | {self.items[i]['description']} | "
                              f"{self.reserve(self.items[i])}" for i in hits)
            raw = await self.ask(tid, PICK_PROMPT.format(request=text, candidates=lines), "pick")
            d = extract_json(raw) or {}
            item = self.by_id.get(str(d.get("item_id", "")))
            if item is None or item["id"] not in {self.items[i]["id"] for i in hits}:
                log(f"[{tid}] -> REJECTED (no fitting item, harness said {d.get('item_id')!r})")
                await up.reject(say("Sorry, nothing in our catalog fits that request."))
                return
            reserve = self.reserve(item)
            try:
                ask = int(round(float(d.get("price"))))
            except (TypeError, ValueError):
                ask = round(reserve * 1.3)
                log(f"[{tid}] fallback opening ask {ask}")
            if ask < reserve:
                log(f"[{tid}] GUARD opening ask {ask} < reserve {reserve} -> {reserve}")
                ask = reserve
            note = str(d.get("say", "")).strip()
            line = (note + " " if note else "") + f"{item['name']} ({item['id']}): {item['description']} Asking {ask}."
            # the text has other numbers, but the ask comes last, so "last number = ask" still holds
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask, "turns": 0,
                                  "history": [f"buyer: {text}", f"you: {line}"]}
            log(f"[{tid}] picked {item['id']} {item['name']!r} reserve {reserve} -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))
            return

        # ---- later messages: same flow as seller.py v2 ----
        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
            return
        offer = int(m.group())
        deal["turns"] += 1
        deal["history"].append(f"buyer: {text}")
        item, reserve = deal["item"], deal["reserve"]

        if deal["turns"] > MAX_TURNS:
            log(f"[{tid}] -> REJECTED (turn limit)")
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
            return

        prompt = TURN_PROMPT.format(name=item["name"], description=item["description"], reserve=reserve,
                                    history="\n".join(deal["history"]), offer=offer, ask=deal["ask"])
        d = parse_turn(await self.ask(tid, prompt, "turn"))
        if d is None:
            d = {"action": "counter", "price": deal["ask"], "say": "Let me think."}
            log(f"[{tid}] fallback: hold ask {deal['ask']}")
        problem = violation(d, offer, reserve)
        if problem:
            log(f"[{tid}] GUARD {problem} -> asking the harness again")
            d2 = parse_turn(await self.ask(tid, prompt + f"\n\nYour previous answer was not allowed: {problem} Answer again.", "retry"))
            if d2 and not violation(d2, offer, reserve):
                d = d2
                log(f"[{tid}] GUARD fixed by harness")
            else:
                d = {"action": "counter", "price": reserve, "say": "This is the lowest I can go."}
                log(f"[{tid}] GUARD clamp -> counter {reserve}")

        if d["action"] == "accept":
            receipt = {"seller": self.name, "item_id": item["id"], "item": item["name"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            log(f"[{tid}] -> COMPLETED receipt={receipt}")
            await up.complete(say(f"Deal: {item['name']} at {offer}."))
            del self.open[task.id]
        elif d["action"] == "reject":
            log(f"[{tid}] -> REJECTED at offer {offer}")
            await up.reject(say((d["say"] + " " if d["say"] else "") + f"No deal at {offer}."))
            del self.open[task.id]
        else:
            deal["ask"] = d["price"]
            line = (d["say"] + " " if d["say"] else "") + f"Counteroffer: {deal['ask']}."
            deal["history"].append(f"you: {line}")
            log(f"[{tid}] -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        log(f"[{task.id[:8]}] -> CANCELED")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def violation(d, offer, reserve):
    if d["action"] == "counter" and d["price"] < reserve:
        return f"asking {d['price']} is below your reserve {reserve}."
    if d["action"] == "accept" and offer < reserve:
        return f"accepting {offer} is below your reserve {reserve}."
    return None


def build(name, items, markup, port, specialty):
    url = f"http://127.0.0.1:{port}/"
    cats = sorted({it["category"] for it in items})
    card = AgentCard(
        name=name,
        description=f"Furniture shop with {len(items)} items: {', '.join(cats)}. "
                    "Describe what you need; the shop picks an item and negotiates the price.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Find and negotiate furniture",
            description=(specialty + " " if specialty else "") +
                        "Describe the item you need in words, then send prices as numbers. "
                        "The shop answers with a counteroffer or a receipt.",
            tags=["shopping", "negotiation"] + cats,
            examples=["something wooden to keep about 40 books, not taller than my desk", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=CatalogSeller(name, items, markup),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--name", default="Choo Furniture Catalog")
    ap.add_argument("--port", type=int, default=9301)
    ap.add_argument("--markup", type=float, default=1.0, help="multiplies every reserve")
    ap.add_argument("--categories", default="", help="comma list: sell only these categories")
    ap.add_argument("--specialty", default="", help="extra sentence for the card's skill")
    ap.add_argument("--log-dir", default=LOG_DIR)
    args = ap.parse_args()

    with open(args.catalog, encoding="utf-8") as f:
        items = json.load(f)
    if args.categories:
        keep = {c.strip() for c in args.categories.split(",")}
        items = [it for it in items if it["category"] in keep]
    os.makedirs(args.log_dir, exist_ok=True)
    LOG_PATH = os.path.join(args.log_dir, f"{re.sub(r'[^A-Za-z0-9]+', '-', args.name).strip('-')}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    log(f"{args.name} on :{args.port}, {len(items)} items, markup {args.markup}, harness: {' '.join(HARNESS_CMD)}")
    uvicorn.run(build(args.name, items, args.markup, args.port, args.specialty),
                host="127.0.0.1", port=args.port, log_level="warning")