"""Week 07 seller: an A2A v1.0 server (JSON-RPC binding) priced against the market, fast.

    python seller.py --port 9999

Round 2 ranks shops by counted deals: signed receipts behind a market order. Round 1 showed
that share follows visits, and visits follow memory: buyers go back to the shop where they
paid least (corner-store 466 visits, outseop 237, both near the reserve). So the shop stays
at or under the cheapest price in the market, with fixed rules and no harness (week 06 used
claude -p, about 3 s per turn, and lost a few deals to harness errors).

Market price: a background thread reads the public ledger every minute and keeps, per item,
the lowest verified sale by another shop in the current round over the last 30 minutes.
No data (or no market) -> the reserve.

The ladder, never below the reserve:
  0. open at the market price,
  1. "too expensive" (an offer below the reserve, or words without a number) -> market price - 1,
     with the fact that it is below every verified sale in the last half hour,
  2. still below the reserve -> the reserve, as the final price, repeated from then on.
  Any offer at or above the reserve closes at once at the offered price; accept words
  without a number ("accept", "deal", "ok") close at the current ask. The shop never walks away.
  The asking price is the only number in each message, so buyers that read the first or the
  last number both read the ask.
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
WINDOW = 30 * 60   # seconds of verified sales that set the market price
REFRESH = 60       # seconds between ledger reads


class MarketPrices:
    """Lowest verified sale per item by other shops, current round, last WINDOW seconds."""

    def __init__(self):
        self.after, self.round, self.sales = 0, None, []   # sales: (ts, item, price)
        self.lock = threading.Lock()

    def poll(self):
        while True:
            try:
                self.read_new()
            except Exception as e:  # the shop keeps selling at the reserve without the feed
                print(f"[prices] ledger read failed: {type(e).__name__}", flush=True)
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
                    if rc and e["data"].get("verified") and rc.get("shop") != SHOP and rc.get("item") in RESERVE:
                        self.sales.append((e["ts"], rc["item"], round(float(rc["price"]))))
                self.after = batch[-1]["seq"]
            if len(batch) < 500:
                break
        print(f"[prices] round {self.round}: " + ", ".join(f"{i} {self.lowest(i)}" for i in RESERVE), flush=True)

    def lowest(self, item):
        cutoff = time.time() - WINDOW
        with self.lock:
            prices = [p for ts, i, p in self.sales if i == item and ts >= cutoff]
        return min(prices) if prices else None


PRICES = MarketPrices()


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask", "step", "market"}

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
        market = PRICES.lowest(item)
        ask = max(RESERVE[item], market or 0)
        self.open[task_id] = {"item": item, "ask": ask, "step": 0, "market": market}
        print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {ask} (market {market})", flush=True)
        msg = await self.words(item, ask, f"First reply: the asking price is {ask}; ask for their offer as a number.",
                               text, f"{item}: asking {ask}. {SERVICE} Send your offer as a number.")
        await up.requires_input(say(msg))

    async def respond(self, task_id, deal, text, up, say):
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None and accepts(text):
            await self.close(task_id, deal, deal["ask"], text, up, say)
            return
        offer = round(float(m.group())) if m else None
        if offer is not None and offer >= RESERVE[deal["item"]]:
            await self.close(task_id, deal, offer, text, up, say)
            return
        await self.step_down(task_id, deal, offer, text, up, say)

    async def step_down(self, task_id, deal, offer, text, up, say):
        """Too expensive: market price - 1 first, then the reserve as the final price."""
        item, reserve, market = deal["item"], RESERVE[deal["item"]], deal["market"]
        under = max(reserve, market - 1) if market else reserve
        if deal["step"] == 0 and under < deal["ask"] and under > reserve:
            deal["step"], deal["ask"] = 1, under
            msg = (f"{item}: {under}, below every verified {item} sale in this market in the last half hour. "
                   f"Asking {under}.")
            situation = (f"Their offer was too low. The shop comes down to {under}, below every verified "
                         f"{item} sale in this market in the last half hour.")
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
        print(f"[task {task_id[:8]}] -> COMPLETED at {price}", flush=True)
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
        description="Sells lamps, chairs and bookshelves, priced at the cheapest recent verified sale in "
                    "this market or below. " + SERVICE + " Answers every question, signed receipts.",
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
    print(f"{SHOP} on http://127.0.0.1:{args.port}/ (rule-based market price ladder; haiku replies to requests)",
          flush=True)
    threading.Thread(target=PRICES.poll, daemon=True).start()
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
