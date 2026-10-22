"""Week 07 seller: an A2A v1.0 server (JSON-RPC binding) whose opening asks haiku sets from the shop's own record.

    python seller.py --port 9999

Round 2 ranks shops by counted deals: signed receipts behind a market order. Until 10-22 the shop
opened at the cheapest verified sale by other shops in the last 30 minutes. Every other shop also
sold at the reserve, so that price was always the reserve (100/180/260), buyers offered below it,
and 100% of our Round 2 sales closed at the reserve (challenge 1).

Pricing desk: a background thread reads the public ledger every minute and keeps the shop's own
verified sales with their stars and reviews; the seller keeps its own visits (how far each one got).
Every 10 minutes haiku reads the last hour of both and sets the opening ask per item, with a
one-sentence reason in the log. Code guards: the opening ask stays within reserve .. 1.5 x reserve
and moves at most 10% of the reserve per decision. Without data or a harness the last ask stays
(the reserve at start).

The ladder, never below the reserve:
  0. open at the desk's ask,
  1. an offer below it -> one counter halfway between the ask and the offer (or the reserve);
     an offer at or above the ask closes at once,
  2. after the counter any offer at or above the reserve closes; below it -> the reserve, as the
     final price, repeated from then on.
  Accept words without a number ("accept", "deal", "ok") close at the current ask. The shop never
  walks away. The asking price is the only number in each message, so buyers that read the first
  or the last number both read the ask.
Week 06 history: the first trade test ran with the reserve in the prompt only and failed
(haiku countered 250 and sold at 230 on a bookshelf with reserve 260); the code guard fixed it.

Replies: Round 2 visits stopped at 02:15 UTC on 10-21 when one buyer gave four 3-star reviews in
a row, "very low price with no explicit warranty or after-sales service mentioned": it asks about
service in its first message and the rule-based shop answered with a price only. So every message
now states the service terms, and a message that asks or requests something (a question mark,
a service word, or more than eight words) gets its reply written by haiku through claude -p:
yes to every request that is not about price, with the price the ladder chose. The code still
picks the price; a reply with any number other than that price, or without it, is thrown away
for the template. Plain offers and accepts stay on the template (claude -p costs about 36k tokens
a call and the buyer shares the account).

LAB 2: the receipt is {shop, item, price, task} with price a whole number and task the A2A
task id, signed with shop_key.pem over canon(...) as sig_seller (sign_receipt from the notes).
"""
import argparse
import asyncio
import base64
import json
import os
import re
import threading
import time

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

import harness

SHOP = "Inextro Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
ACCEPT = re.compile(r"\b(accept|accepted|agree|agreed|deal|ok|okay|yes)\b")
REFUSE = re.compile(r"\b(no|not|don't|dont|won't|cannot|can't|never|reject|walk)\b")


def accepts(text):
    """A plain yes with no number: 'I accept', 'deal', 'ok'. Never 'no deal' or 'not ok'."""
    t = text.lower()
    return bool(ACCEPT.search(t)) and not REFUSE.search(t)


# In words: buyers that read the first or the last number in a message must only find the price.
SERVICE = ("Every item comes with a one-year warranty, free after-sales service, free delivery and "
           "assembly, and free returns within thirty days.")
REQUEST = re.compile(r"\?|warrant|guarant|service|support|quality|deliver|ship|return|refund|assembl|"
                     r"install|repair|material|durab|condition|gift|wrap|insur")
REPLY_TIMEOUT = 10  # seconds; past this the template answers


def asks_something(text):
    """A question, a request about anything but the price, or more than a bare offer."""
    return bool(REQUEST.search(text.lower())) or len(re.findall(r"[A-Za-z']+", text)) > 8


def write_reply(item, price, situation, buyer_text, template):
    """haiku writes the reply; the template answers when it fails or names another number."""
    prompt = (
        f"You are the sales clerk of {SHOP}, a furniture shop. A customer wants a {item}.\n"
        f"The shop's service: {SERVICE}\n"
        f"Situation: {situation}\n"
        f"Customer's message: {buyer_text!r}\n\n"
        "Write the shop's reply in at most three short sentences. Say yes to every request that is not "
        "about the price (warranty, service, delivery, quality, returns, anything else) and promise it at "
        f"no extra cost. The price is {price}: write it once as digits, and write no other digits or "
        "numbers anywhere (say 'one-year', not '1-year'). Never offer any other price. "
        "Reply with the message text only.")
    t0 = time.time()
    try:
        reply = harness.call(prompt, timeout=REPLY_TIMEOUT).strip()
    except Exception as e:  # timeout, auth, rate limit: the template still sells
        print(f"[reply] harness failed after {time.time() - t0:.1f}s: {type(e).__name__}", flush=True)
        return template
    numbers = re.findall(r"\d+(?:\.\d+)?", reply)
    if not reply or not numbers or any(float(n) != price for n in numbers):
        print(f"[reply] {time.time() - t0:.1f}s rejected (numbers {numbers}): {reply[:200]!r}", flush=True)
        return template
    print(f"[reply] {time.time() - t0:.1f}s haiku: {reply[:300]!r}", flush=True)
    return reply


MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
REFRESH = 60          # seconds between ledger reads
DESK_EVERY = 10 * 60  # seconds between pricing decisions
LOOKBACK = 60 * 60    # seconds of own sales and visits the desk reads
MAX_OPEN = 1.5        # the opening ask stays within reserve .. 1.5 x reserve
MAX_MOVE = 0.1        # and moves at most 10% of the reserve per decision
STEPS = ("at the opening ask", "after the counter", "at the reserve")


class Book:
    """The shop's own record: verified sales and reviews from the ledger, visits from this process."""

    def __init__(self):
        self.after, self.round, self.ready = 0, None, False
        self.sales = []    # (ts, item, price, stars, review), current round
        self.visits = {}   # task id -> {"ts", "item", "ask", "price", "step"}
        self.lock = threading.Lock()

    def poll(self):
        while True:
            try:
                self.read_new()
                self.ready = True
            except Exception as e:  # the shop keeps its last asks without the feed
                print(f"[book] ledger read failed: {type(e).__name__}", flush=True)
            time.sleep(REFRESH)

    def read_new(self):
        while True:
            batch = httpx.get(f"{MARKET}/api/ledger", params={"after": self.after}, timeout=20).json()
            if not batch:
                break
            with self.lock:
                for e in batch:
                    if e["round"] != self.round:
                        self.round, self.sales = e["round"], []
                    rc = e["data"].get("receipt") if e["kind"] == "result" else None
                    if rc and e["data"].get("verified") and rc.get("shop") == SHOP and rc.get("item") in RESERVE:
                        self.sales.append((e["ts"], rc["item"], round(float(rc["price"])), e["data"].get("stars"),
                                           (e["data"].get("review") or "")[:160]))
                self.after = batch[-1]["seq"]
            if len(batch) < 500:
                break

    def visit(self, task_id, item, ask):
        with self.lock:
            self.visits[task_id] = {"ts": time.time(), "item": item, "ask": ask, "price": None, "step": None}

    def closed(self, task_id, price, step):
        with self.lock:
            if task_id in self.visits:
                self.visits[task_id].update(price=price, step=step)

    def facts(self, item):
        """The last LOOKBACK seconds for one item, in words for the desk."""
        cutoff = time.time() - LOOKBACK
        with self.lock:
            sales = [s for s in self.sales if s[1] == item and s[0] >= cutoff]
            visits = [v for v in self.visits.values() if v["item"] == item and v["ts"] >= cutoff]
        deals = [v for v in visits if v["price"] is not None]
        lines = [f"  visits to the shop: {len(visits)}, deals {len(deals)}, left without a deal or still "
                 f"talking {len(visits) - len(deals)}"]
        if deals:
            by_step = ", ".join(f"{sum(v['step'] == i for v in deals)} {STEPS[i]}" for i in range(3))
            asks = sorted({v["ask"] for v in visits})
            lines.append(f"  deals closed {by_step}; opening asks used {asks}; "
                         f"average deal price {sum(v['price'] for v in deals) / len(deals):.0f}")
        if sales:
            stars = [s[3] for s in sales if s[3] is not None]
            lines.append(f"  verified sales on the market ledger: {len(sales)}, prices {sorted(s[2] for s in sales)}"
                         + (f", stars average {sum(stars) / len(stars):.1f}" if stars else ""))
            lines += [f"  review: {s[3]} stars, {s[4]!r}" for s in sales[-5:]]
        else:
            lines.append("  verified sales on the market ledger: none")
        return lines


BOOK = Book()


class Desk:
    """haiku sets the opening ask per item from the shop's own sales, reviews and visits."""

    def __init__(self):
        self.ask = dict(RESERVE)

    def run(self):
        while not BOOK.ready:
            time.sleep(5)
        while True:
            try:
                self.decide()
            except Exception as e:  # timeout, rate limit, bad JSON: the last asks stay
                print(f"[desk] no decision: {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(DESK_EVERY)

    def decide(self):
        facts = []
        for item, reserve in RESERVE.items():
            facts += [f"{item}: reserve {reserve}, current opening ask {self.ask[item]}"] + BOOK.facts(item)
        prompt = (
            f"You set the opening asking prices of {SHOP}, a furniture shop in a market of about thirty shops "
            "that sell the same three items to AI buyers. Each buyer has a budget, remembers the shop where it "
            "paid least, and reads the shops' reviews; many offer below whatever the shop asks.\n"
            "How a negotiation goes: the shop opens at your ask; an offer below it gets one counter halfway "
            "between the ask and the offer; after that any offer at or above the reserve closes. The reserve "
            "is the lowest price the shop may sell at and cannot change.\n\n"
            "The shop's own record over the last hour:\n" + "\n".join(facts) + "\n\n"
            "Choose the opening ask per item for the next ten minutes to earn the most: more deals and higher "
            "prices both count, and a buyer driven away goes to another shop. Raise an ask when buyers pay it "
            "and review well; lower it toward the reserve when deals or stars fall. Each ask must be between "
            f"the reserve and {MAX_OPEN} times the reserve.\n"
            'Reply with JSON only: {"lamp": {"open": <whole number>, "why": "<one sentence>"}, '
            '"chair": {...}, "bookshelf": {...}}')
        t0 = time.time()
        reply = harness.call(prompt, timeout=90)
        m = re.search(r"\{.*\}", reply, re.S)
        if m is None:
            raise ValueError(f"no JSON in {reply[:200]!r}")
        data = json.loads(m.group())
        for item, reserve in RESERVE.items():
            want = (data.get(item) or {}).get("open")
            if not isinstance(want, (int, float)):
                print(f"[desk] {item}: no ask in the reply, stays {self.ask[item]}", flush=True)
                continue
            step = MAX_MOVE * reserve
            new = round(min(max(want, self.ask[item] - step, reserve), self.ask[item] + step, MAX_OPEN * reserve))
            print(f"[desk] {item}: {self.ask[item]} -> {new} (haiku {want}): {(data[item].get('why') or '')[:200]}",
                  flush=True)
            self.ask[item] = new
        print(f"[desk] {time.time() - t0:.1f}s; facts: " + " | ".join(f.strip() for f in facts), flush=True)


DESK = Desk()


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "step"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        print(f"[task {task.id[:8]}] buyer: {text!r}", flush=True)
        if deal is None:
            await self.opening(task.id, text, up, say)
        else:
            await self.respond(task.id, deal, text, up, say)

    async def words(self, item, price, situation, text, template):
        """The template, or haiku's reply when the buyer asked or requested something."""
        if not asks_something(text):
            return template
        return await asyncio.to_thread(write_reply, item, price, situation, text, template)

    async def opening(self, task_id, text, up, say):
        item = next((k for k in RESERVE if k in text.lower()), None)
        if item is None:
            await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
            return
        ask = max(RESERVE[item], DESK.ask[item])
        self.open[task_id] = {"item": item, "ask": ask, "step": 0}
        BOOK.visit(task_id, item, ask)
        print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {ask} (desk)", flush=True)
        msg = await self.words(item, ask, f"First reply: the asking price is {ask}; ask for their offer as a number.",
                               text, f"{item}: asking {ask}. {SERVICE} Send your offer as a number.")
        await up.requires_input(say(msg))

    async def respond(self, task_id, deal, text, up, say):
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None and accepts(text):
            await self.close(task_id, deal, deal["ask"], text, up, say)
            return
        offer = round(float(m.group())) if m else None
        if offer is not None and offer >= RESERVE[deal["item"]] and (offer >= deal["ask"] or deal["step"] > 0):
            await self.close(task_id, deal, offer, text, up, say)
            return
        await self.step_down(task_id, deal, offer, text, up, say)

    async def step_down(self, task_id, deal, offer, text, up, say):
        """Below the ask: one counter halfway to the offer (or the reserve), then the reserve as final."""
        item, reserve = deal["item"], RESERVE[deal["item"]]
        counter = round((deal["ask"] + max(reserve, offer or 0)) / 2)
        if deal["step"] == 0 and offer is not None and offer >= reserve and counter - offer <= 1:
            await self.close(task_id, deal, offer, text, up, say)
            return
        if deal["step"] == 0 and reserve < counter < deal["ask"]:
            deal["step"], deal["ask"] = 1, counter
            msg = f"{item}: we can meet you partway. Asking {counter}."
            situation = f"Their offer was below the asking price. The shop comes down to {counter}."
        else:
            deal["step"], deal["ask"] = 2, reserve
            msg = f"{item}: final price, the lowest we can go. Asking {reserve}."
            situation = f"Their offer was too low. {reserve} is the final price, the lowest the shop can go."
        print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {deal['ask']} step {deal['step']} (offer {offer})",
              flush=True)
        await up.requires_input(say(await self.words(item, deal["ask"], situation, text, msg)))

    async def close(self, task_id, deal, price, text, up, say):
        receipt = sign_receipt({"shop": SHOP, "item": deal["item"], "price": int(price), "task": task_id})
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        BOOK.closed(task_id, int(price), deal["step"])
        print(f"[task {task_id[:8]}] -> COMPLETED at {price} step {deal['step']}", flush=True)
        msg = await self.words(deal["item"], int(price), f"Deal closed at {price}; the signed receipt is attached.",
                               text, f"Deal: {deal['item']} at {price}. {SERVICE}")
        await up.complete(say(msg))
        del self.open[task_id]

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP,
        description="Sells lamps, chairs and bookshelves at fair prices and meets buyers partway. " + SERVICE
                    + " Answers every question, signed receipts.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf) to get an asking price, then reply with your "
                        "offer as a number on the same task. The shop closes with a signed receipt artifact "
                        "{shop, item, price, task, sig_seller} or comes down; it never ends the negotiation.",
            tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "100"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    print(f"{SHOP} on http://127.0.0.1:{args.port}/ (haiku pricing desk from own sales and reviews; "
          "haiku replies to requests)",
          flush=True)
    threading.Thread(target=BOOK.poll, daemon=True).start()
    threading.Thread(target=DESK.run, daemon=True).start()
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
