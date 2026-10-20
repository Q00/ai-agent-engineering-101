"""Harness-driven seller behind A2A v1.0. The harness decides every move; this file only speaks A2A.

    python seller.py --port 9301
"""
import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

import httpx

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import CMD, ask_json

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
SHOP_NAME = os.environ.get("SHOP_NAME", "bjeon01")
KEY_FILE = Path(__file__).resolve().parent / "shop_key.pem"   # the kit and the market test use this file
HISTORY = Path(os.environ.get("SHOP_HISTORY", Path(__file__).resolve().parent / "shop_history.jsonl"))
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
_reviews_cache = {"at": 0.0, "reviews": []}
STOCK_PER_HOUR = 5    # real limit: at most this many of each item sold per hour (a boutique sells few pieces)
# Boutique floor: the lowest price this shop accepts, above the market reserve (the reserve never moves and the
# code still guards it). The lamp floor stays <= 120 so a buyer with 120 (the market test) can still buy.
FLOOR = {"lamp": 115, "chair": 210, "bookshelf": 300}
MAX_OFFERS = 6        # real limit: the negotiation ends after this many buyer offers


def stock_left(item):
    """How many of this item I can still sell this hour (counted from my own deal history)."""
    if not HISTORY.exists():
        return STOCK_PER_HOUR
    hour = time.strftime("%Y-%m-%d %H")
    sold = 0
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            h = json.loads(line)
        except json.JSONDecodeError:
            continue
        if h.get("item") == item and h.get("outcome") == "deal" and str(h.get("time", "")).startswith(hour):
            sold += 1
    return max(0, STOCK_PER_HOUR - sold)


def own_reviews():
    """My shop's reviews from the market's public shop list (read only, cached 5 minutes)."""
    if time.time() - _reviews_cache["at"] > 300:
        try:
            shops = httpx.get(f"{MARKET}/api/shops", timeout=10).json()["shops"]
            me = next((x for x in shops if x["name"] == SHOP_NAME), {})
            _reviews_cache["reviews"] = me.get("reviews") or []
        except Exception:
            pass
        _reviews_cache["at"] = time.time()
    return _reviews_cache["reviews"]


def pricing(item, reserve):
    """Challenge 1: the opening ask from my own trades and reviews (the reserve never moves).
    Boutique positioning: open 40 to 55 percent over the reserve. The more of my recent negotiations end
    in a deal, the higher I open; fewer deals or price complaints in my reviews bring it down to 40."""
    hist = []
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                h = json.loads(line)
            except json.JSONDecodeError:
                continue
            if h.get("item") == item:
                hist.append(h)
    recent = hist[-20:]
    deals = [h for h in recent if h.get("outcome") == "deal"]
    rate = len(deals) / len(recent) if recent else None
    closed = sum(h["price"] / reserve - 1 for h in deals) / len(deals) if deals else 0.15
    markup = 0.40 + 0.15 * (rate if rate is not None else 0.33)
    reviews = own_reviews()[:10]
    stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), (int, float))]
    complaints = sum(1 for r in reviews if re.search(r"expensive|high|overpric|too much|slow", str(r.get("review", "")), re.I))
    if complaints >= 2:
        markup -= 0.05
    markup = min(0.55, max(0.40, markup))
    note = (f"your last {len(recent)} {item} negotiations: {len(deals)} deals"
            + (f", closing on average {closed:+.0%} over your lowest price" if deals else "")
            + (f"; your reviews: {len(reviews)}, avg stars {sum(stars) / len(stars):.1f}" if stars else "; no reviews yet")
            + (f", {complaints} mention price" if complaints else ""))
    return round(reserve * (1 + markup)), note


def shop_key():
    """The Ed25519 key the market knows for this shop. Created here only if the kit has not made it yet."""
    if not KEY_FILE.exists():
        k = Ed25519PrivateKey.generate()
        KEY_FILE.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)


def sign_receipt(receipt: dict, key) -> dict:
    """{shop, item, price, task} + sig_seller: Ed25519 over the canonical JSON (sorted keys, no spaces, UTF-8)."""
    body = {"shop": receipt["shop"], "item": receipt["item"], "price": int(receipt["price"]), "task": receipt["task"]}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return dict(body, sig_seller=base64.b64encode(key.sign(canon)).decode())

PROMPT = """You are the sales agent of a boutique furniture shop that sells only a few pieces each hour.
A buyer is negotiating for one {item}. Your lowest acceptable price for the {item} is {reserve}.
Never sell below it and never reveal it. You do not chase buyers: concede slowly, in small steps, and keep the
price feeling exclusive. A deal at or above {reserve} is still better than losing a buyer at the last offer.
Market data: {note}. Suggested opening ask: {opening}.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def record(deal, outcome, price=None):
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "item": deal["item"], "outcome": outcome,
                            "price": price, "opening": deal["opening"], "asks": deal["asks"],
                            "offers": deal["offers"]}) + "\n")


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "offer", "lines"}
        self.key = shop_key()

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: lamp, chair, bookshelf."))
                return
            if stock_left(item) == 0:
                await up.reject(say(f"Sold out: every {item} for this hour is gone. Please come back next hour."))
                return
            opening, note = pricing(item, RESERVE[item])
            deal = self.open[task.id] = {"item": item, "ask": None, "offer": None, "lines": [f"buyer: {text}"],
                                         "opening": opening, "note": note, "asks": [], "offers": []}
            print(f"[pricing] {item}: opening {opening} ({note})", flush=True)
        else:
            deal["lines"].append(f"buyer: {text}")
            m = re.search(r"\d+", text)
            deal["offer"] = int(m.group()) if m else deal["offer"]
            if m:
                deal["offers"].append(deal["offer"])

        reserve = max(RESERVE[deal["item"]], FLOOR[deal["item"]])   # the boutique floor, never below the reserve
        o, ask = deal["offer"], deal["ask"]
        if o is not None and ask is not None and o >= reserve and ask - o <= max(3, 0.03 * ask):
            move, action = {"action": "accept"}, "accept"   # do not lose a deal over a few units (week 7, 120 vs 121)
            print(f"[guard] offer {o} is within 3% of my ask {ask}: accept", flush=True)
        elif o is not None and o >= reserve and len(deal["offers"]) >= MAX_OFFERS:
            move, action = {"action": "accept"}, "accept"   # the last allowed offer is above my floor: take it
            print(f"[guard] last allowed offer {o} is above my floor {reserve}: accept", flush=True)
        else:
            move, raw = ask_json(PROMPT.format(item=deal["item"], reserve=reserve, note=deal["note"],
                                               opening=deal["opening"], transcript="\n".join(deal["lines"])))
            print(f"[harness] {raw}", flush=True)
            action = (move or {}).get("action")
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below reserve {reserve} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"] or reserve}
        if action == "accept":
            price = int(deal["offer"])
            receipt = sign_receipt({"shop": SHOP_NAME, "item": deal["item"], "price": price, "task": task.id}, self.key)
            print(f"[receipt] {json.dumps(receipt)}", flush=True)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {price}."))
            record(deal, "deal", price)
            del self.open[task.id]
            return
        if action != "accept" and len(deal["offers"]) >= MAX_OFFERS:   # the announced limit is kept
            await up.reject(say("No deal: that was the last offer, as announced."))
            record(deal, "rejected")
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("No deal."))
            record(deal, "rejected")
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = deal["ask"] or reserve              # unparsed answer: repeat the last ask
        if price < reserve:                             # the harness may forget the reserve; the code does not
            print(f"[guard] ask {price} below reserve {reserve} -> {reserve}", flush=True)
            price = reserve
        if deal["ask"] is None and price > deal["opening"] * 1.1:   # opening ask stays near the data-driven one
            print(f"[guard] opening ask {price} -> {round(deal['opening'] * 1.1)}", flush=True)
            price = round(deal["opening"] * 1.1)
        deal["ask"] = int(price)
        deal["asks"].append(deal["ask"])
        left = stock_left(deal["item"])
        rounds_left = MAX_OFFERS - len(deal["offers"])
        if len(deal["lines"]) == 1:   # truthful scarcity: the stock and the round limit are both enforced above
            reply = (f"{deal['item']}: a boutique piece, only {left} left this hour; this negotiation closes after {MAX_OFFERS} offers. "
                     f"Asking {deal['ask']}. Send your offer as a number.")
        else:
            reply = (f"{'Your next offer is the last one, ' if rounds_left <= 1 else f'{rounds_left} offers left, '}"
                     f"{left} in stock this hour. Counteroffer: {deal['ask']}.")
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            record(deal, "canceled")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=f"{SHOP_NAME} shop",
        description=f"A boutique furniture shop: desk lamps, chairs and bookshelves, a few pieces each hour, firm prices. "
                    f"A coding harness ({CMD.split()[0]}) negotiates. "
                    "Every receipt is signed (Ed25519) and always shows the agreed price. "
                    f"Limited stock: {STOCK_PER_HOUR} of each item per hour; a negotiation closes after {MAX_OFFERS} offers.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
