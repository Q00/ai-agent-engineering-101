"""Week 06 seller v2 — A2A v1.0 server (JSON-RPC) whose prices are decided by a harness.

    python seller.py                 # shop on :9101
    python seller.py --port 9200
    python seller.py --selftest      # call the harness once and print the raw answer

v2: the reserve is in the prompt AND guarded in code.
    If the harness asks or accepts below the reserve, the code asks it once more with the reason;
    if the second answer still breaks the reserve, the code counters at the reserve itself.
The code speaks A2A, keeps per-task state, and turns the harness's JSON into A2A states.

Week 07 LAB 2: receipts are {shop, item, price, task} and carry sig_seller,
    an Ed25519 signature (by shop_key.pem) over the canonical JSON of those four fields.
Week 07 HW (shop: change the price): every negotiation's outcome goes to pricing.jsonl;
    the opening ask per item is set by code from the recent deal rate of that item
    (few deals -> open lower, many deals -> open higher). The reserve guard is unchanged.

    python seller.py --pricing-report   # deal rate and average price per item and markup
"""
import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import uvicorn
from starlette.applications import Starlette
from cryptography.hazmat.primitives import serialization

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

SHOP_NAME = "Choo Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
MAX_TURNS = 8  # safety stop so a task always ends (not a price rule)

# ---------- pricing from my own trade record (week 07 HW) ----------
PRICING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.jsonl")
WINDOW = 8          # look at the last 8 negotiations of the item
MIN_RECORDS = 3     # fewer records than this: use the default markup
DEFAULT_MARKUP = 1.4
# (deal rate at least, markup): high deal rate -> open higher, low deal rate -> open lower
MARKUP_STEPS = [(0.6, 1.5), (0.3, 1.3), (0.0, 1.15)]


def load_pricing():
    rows = []
    if os.path.exists(PRICING_PATH):
        with open(PRICING_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def record_outcome(item, open_ask, outcome, price=None, turns=0):
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "item": item, "open_ask": open_ask,
           "outcome": outcome, "price": price, "turns": turns}
    with open(PRICING_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def opening_markup(item):
    """Returns (markup, reason) from the last WINDOW outcomes of this item."""
    recent = [r for r in load_pricing() if r.get("item") == item][-WINDOW:]
    if len(recent) < MIN_RECORDS:
        return DEFAULT_MARKUP, f"{len(recent)} records, default"
    deals = sum(1 for r in recent if r.get("outcome") == "deal")
    rate = deals / len(recent)
    markup = next(m for floor, m in MARKUP_STEPS if rate >= floor)
    return markup, f"last {len(recent)}: {deals} deals ({rate:.0%})"

# ---------- receipt signing (week 07 LAB 2) ----------
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop_key.pem")


def canon(d):
    """Sorted keys, no spaces, UTF-8: the same bytes on every side."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path=KEY_PATH):
    """Add sig_seller over {shop, item, price, task}. Without the key, the receipt stays unsigned."""
    if not os.path.exists(key_path):
        log(f"[sign] {key_path} not found -> receipt sent unsigned")
        return receipt
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


# ---------- harness (claude -p) ----------
CLAUDE = shutil.which("claude") or "claude"
HARNESS_CMD = [CLAUDE, "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="seller-harness-")  # empty folder: no project instruction files

os.makedirs("logs", exist_ok=True)
LOG_PATH = os.path.join("logs", f"seller-{time.strftime('%Y%m%d-%H%M%S')}.txt")


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:  # utf-8 file, avoids PowerShell UTF-16 logs
        f.write(line + "\n")


def call_harness(prompt: str) -> str:
    r = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", cwd=WORKDIR, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"harness exit {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def parse_decision(raw: str):
    """Pull {"action", "price", "say"} out of the harness answer. None if unusable."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if d.get("action") not in ("counter", "accept", "reject"):
        return None
    if d["action"] == "counter":
        try:
            d["price"] = int(round(float(d["price"])))
        except (KeyError, TypeError, ValueError):
            return None
    d["say"] = str(d.get("say", "")).strip()
    return d


OPEN_PROMPT = """You are the owner of a furniture shop. A buyer wants to buy a {item}.
Your reserve price (the lowest price you may ever ask or sell at) is {reserve}. Never reveal it.
Choose your opening asking price. Leave room to negotiate down.

Reply with ONE JSON object only, no other text:
{{"action": "counter", "price": <opening asking price, integer>, "say": "<one short friendly sentence, no numbers>"}}
"""

TURN_PROMPT = """You are the owner of a furniture shop, negotiating the price of one {item} with a buyer.
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


# ---------- A2A side ----------
class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "turns", "history"}

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
        log(f"[{tid}] buyer: {text!r}")

        # ---- first message: which item, opening ask ----
        if deal is None:
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                msg = "We only sell: " + ", ".join(RESERVE) + "."
                log(f"[{tid}] -> REJECTED {msg!r}")
                await up.reject(say(msg))
                return
            reserve = RESERVE[item]
            d = await self._ask_harness(tid, OPEN_PROMPT.format(item=item, reserve=reserve))
            markup, why = opening_markup(item)
            ask = round(reserve * markup)  # the code sets the opening ask; the harness only writes the sentence
            log(f"[{tid}] [pricing] {item}: {why} -> open at {markup}x reserve = {ask}"
                + (f" (harness wanted {d['price']})" if d and d["action"] == "counter" else ""))
            if ask < reserve:
                log(f"[{tid}] GUARD opening ask {ask} < reserve {reserve} -> {reserve}")
                ask = reserve
            line = (d["say"] + " " if d and d["say"] else "") + f"{item}: asking {ask}."
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask, "open_ask": ask, "turns": 0,
                                  "history": [f"buyer: {text}", f"you: {line}"]}
            log(f"[{tid}] -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))
            return

        # ---- later messages: buyer's offer ----
        m = re.search(r"\d+", text)
        if m is None:
            line = f"Send your offer as a number. Asking {deal['ask']}."
            await up.requires_input(say(line))
            return
        offer = int(m.group())
        deal["turns"] += 1
        deal["history"].append(f"buyer: {text}")

        if deal["turns"] > MAX_TURNS:
            line = f"No deal at {offer}."
            log(f"[{tid}] -> REJECTED (turn limit) {line!r}")
            record_outcome(deal["item"], deal["open_ask"], "no_deal", turns=deal["turns"])
            await up.reject(say(line))
            del self.open[task.id]
            return

        prompt = TURN_PROMPT.format(item=deal["item"], reserve=deal["reserve"],
                                    history="\n".join(deal["history"]), offer=offer, ask=deal["ask"])
        d = await self._ask_harness(tid, prompt)
        if d is None:  # unusable answer: hold the current ask
            d = {"action": "counter", "price": deal["ask"], "say": "Let me think."}
            log(f"[{tid}] fallback: hold ask {deal['ask']}")

        reserve = deal["reserve"]
        problem = self._violation(d, offer, reserve)
        if problem:  # guard step 1: tell the harness why and ask once more
            log(f"[{tid}] GUARD {problem} -> asking the harness again")
            retry = prompt + f"\n\nYour previous answer was not allowed: {problem} Answer again."
            d2 = await self._ask_harness(tid, retry)
            if d2 and not self._violation(d2, offer, reserve):
                d = d2
                log(f"[{tid}] GUARD fixed by harness: {d2['action']} {d2.get('price', '')}")
            else:  # guard step 2: the code holds the reserve
                d = {"action": "counter", "price": reserve, "say": "This is the lowest I can go."}
                log(f"[{tid}] GUARD clamp -> counter {reserve}")

        if d["action"] == "accept":
            # week 07 LAB 2: {shop, item, price, task} + sig_seller; integer price, full A2A task id
            receipt = sign_receipt({"shop": SHOP_NAME, "item": deal["item"],
                                    "price": int(offer), "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            line = f"Deal: {deal['item']} at {offer}."
            log(f"[{tid}] -> COMPLETED {line!r} receipt={receipt}")
            record_outcome(deal["item"], deal["open_ask"], "deal", price=int(offer), turns=deal["turns"])
            await up.complete(say(line))
            del self.open[task.id]
        elif d["action"] == "reject":
            line = (d["say"] + " " if d["say"] else "") + f"No deal at {offer}."
            log(f"[{tid}] -> REJECTED {line!r}")
            record_outcome(deal["item"], deal["open_ask"], "no_deal", turns=deal["turns"])
            await up.reject(say(line))
            del self.open[task.id]
        else:
            deal["ask"] = d["price"]
            line = (d["say"] + " " if d["say"] else "") + f"Counteroffer: {deal['ask']}."
            deal["history"].append(f"you: {line}")
            log(f"[{tid}] -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))

    @staticmethod
    def _violation(d, offer, reserve):
        if d["action"] == "counter" and d["price"] < reserve:
            return f"asking {d['price']} is below your reserve {reserve}."
        if d["action"] == "accept" and offer < reserve:
            return f"accepting {offer} is below your reserve {reserve}."
        return None

    async def _ask_harness(self, tid, prompt):
        try:
            raw = await asyncio.to_thread(call_harness, prompt)
        except Exception as e:
            log(f"[{tid}] harness error: {e}")
            return None
        log(f"[{tid}] harness: {raw!r}")
        return parse_decision(raw)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:  # the buyer walked away
            record_outcome(deal["item"], deal["open_ask"], "walk", turns=deal["turns"])
        log(f"[{task.id[:8]}] -> CANCELED")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description="Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item (lamp, chair, bookshelf), then send a price as a number. "
                        "The shop answers with a counteroffer or a receipt.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pricing-report", action="store_true")
    args = ap.parse_args()
    if args.pricing_report:
        rows = load_pricing()
        for item in RESERVE:
            rs = [r for r in rows if r.get("item") == item]
            deals = [r for r in rs if r.get("outcome") == "deal"]
            avg = sum(r["price"] for r in deals) / len(deals) if deals else None
            m, why = opening_markup(item)
            print(f"{item:9s} negotiations {len(rs):3d}  deals {len(deals):3d}  "
                  f"avg price {avg if avg is None else round(avg, 1)}  next open {m}x ({why})")
    elif args.selftest:
        out = call_harness(OPEN_PROMPT.format(item="lamp", reserve=100))
        print("raw:", out)
        print("parsed:", parse_decision(out))
    else:
        log(f"seller v2 on :{args.port}, harness: {' '.join(HARNESS_CMD)}")
        uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")