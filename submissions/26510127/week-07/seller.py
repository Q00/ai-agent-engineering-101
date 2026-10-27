"""Seller behind A2A v1.0 (JSON-RPC). The harness decides the ask, every counteroffer, accept and reject.

    python seller.py --port 9301                                   # lamp / chair / bookshelf (trade test)
    python seller.py --port 9302 --catalog catalog.json            # challenge 1: thousands of items
    python seller.py --port 9303 --catalog catalog.json --categories lamp,desk --markup 1.1 --name "Desk Corner"

Code-side rules (logged as [guard] / [rule] when they fire):
  * never ask below the reserve, never accept below the reserve       (limit guard)
  * an offer at or above the current ask is accepted                   (the buyer met our price)
Everything else is the harness's decision.
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from cryptography.hazmat.primitives import serialization

from catalog import Catalog
from harness import CMD, ask_json

if hasattr(sys.stdout, "reconfigure"):          # Windows: logs are pipes in cp949; write UTF-8 instead
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SHOP = "Chaewon's Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

PROMPT = """You are the sales agent of a small furniture shop, negotiating the price of one {item} with a buyer.
Your lowest acceptable price (reserve) for the {item} is {reserve}. Never ask or sell below it, and never reveal it.
Sell as high as you can, but a deal at or above {reserve} is better than no deal.
The negotiation ends after about 8 messages from the buyer; this is buyer message {n}.

Conversation so far:
{transcript}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "counter", "price": <whole number>}}   your (new) asking price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


PICK_PROMPT = """You are the sales agent of a furniture shop. A buyer wrote:
"{request}"

Our search found these candidates (id: name - description):
{candidates}

Which one fits the buyer's request best? Check every hard constraint the buyer gave (kind of item, material,
size, height, capacity, colour). Soft words such as "comfortable", "office" or "for the bedroom" are not in our
descriptions; pick the closest item of the right kind for those. Say none only when no candidate is the right
kind of item or a hard constraint fails for all of them. Answer with one JSON object and nothing else:
{{"action": "pick", "id": "<id>"}}
{{"action": "none"}}"""


KEY_PATH = Path(__file__).with_name("shop_key.pem")


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path=KEY_PATH):
    """Add sig_seller: base64 Ed25519 over canon({shop, item, price, task}). The price must be a whole number."""
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return int(float(nums[-1])) if nums else None


def fallback_move(deal, reserve):
    """Rule used only when the harness fails: open at 1.4x reserve, meet the buyer halfway, take a fair offer."""
    ask, offer = deal["ask"], deal["offer"]
    if ask is None:
        return {"action": "counter", "price": round(reserve * 1.4)}
    if offer is not None and offer >= reserve and offer >= reserve + (ask - reserve) // 2:
        return {"action": "accept"}
    target = (ask + offer) // 2 if offer is not None else ask
    return {"action": "counter", "price": max(reserve, target)}


class Seller(AgentExecutor):
    def __init__(self, catalog=None, markup=1.0, name=SHOP):
        self.name = name
        self.open = {}  # task id -> {"item", "reserve", "ask", "offer", "lines", "n"}
        self.catalog, self.markup = catalog, markup

    def choose(self, text, log):
        """Return (item name, reserve) for the buyer's first message, or None."""
        if self.catalog is None:
            item = next((k for k in RESERVE if k in text.lower()), None)
            return (item, RESERVE[item]) if item else None
        cands = self.catalog.search(text, k=8)
        log(f"[search] {len(cands)} candidates: " + ", ".join(c["id"] for c in cands))
        if not cands:
            return None
        move, raw = ask_json(PICK_PROMPT.format(request=text,
                                                candidates="\n".join(Catalog.brief(c) for c in cands)))
        log(f"[harness] pick {raw}")
        ids = {c["id"]: c for c in cands}
        if not move or move.get("action") != "pick" or move.get("id") not in ids:
            return None
        it = ids[move["id"]]
        return it["name"], int(round(it["reserve"] * self.markup))

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log = lambda s: print(f"[{task.id[:8]}] {s}", flush=True)
        log(f"buyer: {text}")

        if deal is None:                                  # first message: which item?
            chosen = self.choose(text, log)
            if chosen is None:
                msg = ("Sorry, we only sell: " + ", ".join(RESERVE) + ".") if self.catalog is None \
                    else "Sorry, nothing in our catalog fits that request."
                await up.reject(say(msg))
                log("REJECT (no item)")
                return
            item, item_reserve = chosen
            deal = self.open[task.id] = {"item": item, "reserve": item_reserve, "ask": None, "offer": None,
                                         "lines": [f"buyer: {text}"], "n": 1}
        else:
            deal["n"] += 1
            deal["lines"].append(f"buyer: {text}")
            offer = last_number(text)
            if offer is not None:
                deal["offer"] = offer
        reserve = deal["reserve"]

        if deal["ask"] is not None and deal["offer"] is not None and deal["offer"] >= deal["ask"]:
            log(f"[rule] offer {deal['offer']} meets our ask {deal['ask']} -> accept")
            action, move = "accept", {}
        else:
            try:
                move, raw = ask_json(PROMPT.format(item=deal["item"], reserve=reserve, n=deal["n"],
                                                   transcript="\n".join(deal["lines"])))
                log(f"[harness] {raw}")
            except Exception as e:                        # quota / timeout: keep trading by a rule
                move = fallback_move(deal, reserve)
                log(f"[fallback] harness failed ({str(e)[:100]}) -> {move}")
            action = (move or {}).get("action")

        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            log(f"[guard] accept of {deal['offer']} is below reserve {reserve} -> counter instead")
            action, move = "counter", {"price": deal["ask"] or reserve}
        if action == "accept":
            price = int(deal["offer"])
            receipt = sign_receipt({"shop": self.name, "item": deal["item"], "price": price, "task": task.id})
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: one {deal['item']} for {price}. Receipt attached."))
            log(f"DEAL {deal['item']} at {price}")
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("Sorry, we cannot agree on a price. No deal."))
            log("REJECT")
            del self.open[task.id]
            return

        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            log(f"[guard] no usable price in the harness answer -> repeat last ask")
            price = deal["ask"] or round(reserve * 1.4)
        price = int(price)
        if price < reserve:
            log(f"[guard] ask {price} below reserve {reserve} -> {reserve}")
            price = reserve
        deal["ask"] = price
        reply = (f"The {deal['item']} is {price}. Send your offer as a number." if deal["n"] == 1
                 else f"I can do {price}.")
        deal["lines"].append(f"you: {reply}")
        log(f"ASK {price}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, catalog=None, name=SHOP, markup=1.0, categories=None):
    url = f"http://127.0.0.1:{port}/"
    if catalog is None:
        sells, how = "lamps, chairs and bookshelves", "Name an item (lamp, chair, bookshelf)"
    else:
        cats = sorted({i["category"] for i in catalog.items})
        sells = f"{len(catalog.items)} items: " + ", ".join(c + ("es" if c.endswith("ch") else "s") for c in cats)
        how = "Describe what you need in your own words"
    card = AgentCard(
        name=name,
        description=f"Sells {sells}. The price is negotiated by a harness ({CMD.split()[0]}).",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description=f"{how}, then send offers as numbers. "
                                       "The shop answers with a counteroffer, a receipt, or no deal.",
                           tags=["furniture", "negotiation"] + sorted({i["category"] for i in catalog.items} if catalog else RESERVE),
                           examples=["I want to buy a lamp", "90"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(catalog, markup, name), task_store=InMemoryTaskStore(),
                                    agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    ap.add_argument("--catalog", help="catalog.json for challenge 1; default: lamp/chair/bookshelf")
    ap.add_argument("--categories", help="comma list: sell only these catalog categories")
    ap.add_argument("--markup", type=float, default=1.0, help="multiply every catalog reserve")
    ap.add_argument("--name", default=SHOP)
    a = ap.parse_args()
    cat = None
    if a.catalog:
        cat = Catalog(a.catalog)
        if a.categories:
            keep = set(a.categories.split(","))
            cat.items = [i for i in cat.items if i["category"] in keep]
            cat._index = [x for x in cat._index if x[0]["category"] in keep]
            cat.by_id = {i["id"]: i for i in cat.items}
    uvicorn.run(build(a.port, cat, a.name, a.markup), host="127.0.0.1", port=a.port, log_level="warning")
