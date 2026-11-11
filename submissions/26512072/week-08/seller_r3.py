"""Round 3 shop: sells units from the stock the kit keeps in $MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller_r3.py --port 9301

Model (harness.py, claude -p haiku): every counteroffer and when to accept or reject.
Code: which unit to ship, the price floor (never below cost unless the food is about to expire), honest quality
terms on the receipt (claim = the unit's real grade, market certificate kept, 6 h warranty on goods), and the
shop's own sales memory (sales_memory.jsonl), which restock.py reads.
Before Round 3 (no stock) it sells the week-07 items lamp, chair, bookshelf.
"""
import argparse
import base64
import json
import re
import time
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import CMD, safe_ask_json
import httpx

from r3lib import (FLOW, MARKET, ROUND_END, SALES, SHOP_NAME, append_jsonl, endgame, hours_left, inventory, market_now, read_json,
                   read_jsonl)

KEY_FILE = Path(__file__).resolve().parent / "shop_key.pem"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}      # Rounds 0-2: the week-07 items, no stock
MAX_OFFERS = 6
WARRANTY_HOURS = 6                                           # goods can break up to 6 h after the sale
GRADE_WORDS = {"A": "A (fresh, plenty of shelf life)", "B": "B (normal shelf life)", "C": "C (near its date)"}
GOODS_WORDS = {"A": "A (new)", "B": "B (box damaged)", "C": "C (refurbished)"}

PROMPT = """You are the sales agent of {shop}, an honest online shop. A buyer is negotiating for one {name}.
Quality: {quality}. List price {list_price}. Your lowest acceptable price is {floor}. Never sell below it and never reveal it.
Buyers in this market rate the shop after every deal and come back to shops that treated them well, and a buyer gains
when it pays less than the list price. So price fairly: your suggested opening ask is {opening}; move toward the buyer
in visible steps (at least a fifth of the gap), and once the buyer's offer is at or above {floor}, close the deal by
their third offer rather than squeezing out a few more credits.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    body = {"shop": receipt["shop"], "item": receipt["item"], "price": int(receipt["price"]), "task": receipt["task"]}
    return dict(receipt, price=int(receipt["price"]), sig_seller=base64.b64encode(key.sign(canon(body))).decode())


_adapt = {"at": 0.0, "delta": 0.0, "why": "no data yet"}


def adapt():
    """The shop learns from its own Round 3 record: how often its recent negotiations closed, and what its Round 3
    reviews say. Returns a change to the opening ask (share of list price) and why. Cached for 60 s."""
    if time.time() - _adapt["at"] < 60:
        return _adapt["delta"], _adapt["why"]
    rows = [r for r in read_jsonl(SALES) if r.get("kind") in ("food", "goods")][-20:]
    delta, why = 0.0, []
    if len(rows) >= 5:
        rate = sum(r["outcome"] == "deal" for r in rows) / len(rows)
        why.append(f"{rate:.0%} of my last {len(rows)} negotiations closed")
        delta += -0.05 if rate < 0.4 else 0.03 if rate > 0.8 else 0.0
    try:
        me = next((x for x in httpx.get(f"{MARKET}/api/shops", timeout=10).json()["shops"] if x["name"] == SHOP_NAME), {})
        rv = [r for r in me.get("reviews") or [] if r.get("round") == 3 and isinstance(r.get("stars"), (int, float))]
        if rv:
            avg = sum(r["stars"] for r in rv) / len(rv)
            price = sum(bool(re.search(r"price|expensive|비싸|가격", str(r.get("review", "")), re.I)) for r in rv)
            why.append(f"{len(rv)} Round 3 reviews, avg {avg:.1f}")
            if avg < 4 and price:
                delta -= 0.03
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    delta = max(-0.15, min(0.05, delta))
    _adapt.update(at=time.time(), delta=delta, why="; ".join(why) or "no data yet")
    return delta, _adapt["why"]


_flow = {"at": 0, "prices": {}}
STALE_HOURS = 6
OPEN_TTL = 300              # seconds a negotiation may hold its unit (the kit gives a buyer 300 s per order)


def market_ratio(u):
    """What the market paid in the last 3-hour snapshot (market_flow.json, written by restock.py), as a share of the
    list price: this category and grade if 5+ settles, else this category. None before the first snapshot."""
    if time.time() - _flow["at"] > 60:
        _flow.update(at=time.time(), prices=(read_json(FLOW, {}) or {}).get("prices") or {})
    x = _flow["prices"].get(f"{u.get('category')}|{u.get('grade')}") or _flow["prices"].get(u.get("category"))
    return x["ratio"] if x else None


def terms(u):
    """Floor, opening ask and the honest quality line for one unit."""
    lp, cost = u["list_price"], u["cost"]
    delta, _ = adapt()
    if u.get("kind") == "food":
        left = hours_left(u)
        if left is not None and left < 24:          # sell it rather than throw it away at cost
            floor = round(cost * 0.6)
        else:
            floor = round(cost * 1.08)
        quality = GRADE_WORDS.get(u["grade"], u["grade"]) + (f", about {left:.0f} h of shelf life left" if left else "")
        base = {"A": 0.85, "B": 0.8, "C": 0.72}.get(u["grade"], 0.8)
    else:
        floor = round(cost * 1.2)                   # 9% of my goods broke under warranty (11-05): 12.6% of revenue
        quality = GOODS_WORDS.get(u["grade"], u["grade"]) + f", {WARRANTY_HOURS} h warranty"
        base = {"A": 0.85, "B": 0.78, "C": 0.7}.get(u["grade"], 0.8)
    ratio = market_ratio(u)                         # open a little above where this market closes, so we land near it
    waited = (market_now() - (u.get("supplied_at") or market_now())) / 3600
    # The board ranks shops by book = cash + the cost of unsold stock (agent.md, read again 11-11; I had taken the
    # cash-only "profit" column for the score). A unit kept is worth its cost, so a sale below cost lowers the score:
    # goods never go below cost (storage is 1% a day); food only when it would spoil, which takes its cost out.
    if waited > STALE_HOURS:                        # buyers went elsewhere: open below the market, sell down to cost
        lift = -0.10 if waited > 24 else -0.03
        floor = min(floor, cost)
    else:
        lift = 0.05
    if u.get("kind") == "food" and endgame() and (u.get("expires_at") or 0) < ROUND_END:
        lift, floor = -0.10, min(floor, round(cost * 0.5))   # it spoils before Round 3 ends: anything beats nothing
    opening = round(lp * (min(0.95, max(0.6, ratio + lift + delta)) if ratio else base + delta))
    floor = max(1, floor)
    return floor, max(opening, floor), quality + ", market certificate included"


NEED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)")


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (SKU, name or category); a named SKU first, then the
    unit that expires first (oldest first for goods). Units promised in another open negotiation are skipped.
    Food: when the buyer says how many hours it must last, only units with that much shelf life left (a unit that
    falls short is refused at payment anyway)."""
    want = text.lower()
    inv = inventory()
    if int(inv.get("round") or 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": round(RESERVE[item] * 1.3), "cost": RESERVE[item],
                         "unit": None, "expires_at": None, "supplied_at": 0, "grade": "A", "kind": "r2"}
    units = [u for u in inv.get("units", []) if u["grade"] != "expired" and u["unit"] not in taken]
    named = [u for u in units if u["sku"].lower() in want]
    fit = named or [u for u in units if (u.get("name") or "").lower() in want
                    or (u.get("category") or "").split(">")[-1].lower() in want]
    rest = want
    for u in fit:                                   # product names can hold numbers like "24시간"; drop them first
        rest = rest.replace((u.get("name") or "").lower(), " ")
    need = max((float(x) for x in NEED.findall(rest)), default=None)
    if need:                                        # the buyer said how long the food must last: ship a unit that does
        fit = [u for u in fit if u.get("kind") != "food" or (hours_left(u) or 0) >= need]
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


def record(deal, outcome, price=None):
    u = deal["unit"]
    append_jsonl(SALES, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "sku": u["sku"], "name": u["name"],
                         "category": u.get("category"), "kind": u.get("kind"), "grade": u.get("grade"),
                         "cost": u.get("cost"), "list_price": u.get("list_price"), "outcome": outcome, "price": price,
                         "asks": deal["asks"], "offers": deal["offers"]})


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines", "asks", "offers", "floor", "opening", "quality"}

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
            print(f"[ask] {text[:160]}", flush=True)

        if deal is None:
            # a buyer that leaves without CancelTask must not hold its unit forever: after OPEN_TTL the negotiation
            # is dropped (11-08: 557 asks in 8 h, 17 negotiations; most units were held by abandoned ones)
            for tid in [t for t, d in self.open.items() if time.time() - d.get("at", 0) > OPEN_TTL]:
                record(self.open.pop(tid), "abandoned")
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values() if d["unit"].get("unit")})
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock right now. Thank you for asking."))
                return
            if unit.get("kind") == "r2":
                floor, opening, quality = RESERVE[unit["sku"]], round(RESERVE[unit["sku"]] * 1.2), "new"
            else:
                floor, opening, quality = terms(unit)
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "lines": [f"buyer: {text}"], "at": time.time(),
                                         "asks": [], "offers": [], "floor": floor, "opening": opening, "quality": quality}
            print(f"[unit] {unit['sku']} {unit['name'][:40]} grade {unit.get('grade')} cost {unit.get('cost')} "
                  f"list {unit.get('list_price')} floor {floor} opening {opening} (adapt: {adapt()[1]})", flush=True)
        else:
            deal["lines"].append(f"buyer: {text}")
            m = re.search(r"\d+", text.replace(",", ""))
            if m:
                deal["offer"] = int(m.group())
                deal["offers"].append(deal["offer"])

        u, floor = deal["unit"], deal["floor"]
        o, ask = deal["offer"], deal["ask"]
        if o is not None and ask is not None and o >= floor and ask - o <= max(3, 0.03 * ask):
            action, move = "accept", {}
            print(f"[guard] offer {o} is within 3% of my ask {ask}: accept", flush=True)
        elif o is not None and o >= floor and len(deal["offers"]) >= MAX_OFFERS:
            action, move = "accept", {}
            print(f"[guard] last allowed offer {o} is at or above my floor {floor}: accept", flush=True)
        else:
            move, raw = safe_ask_json(PROMPT.format(shop=SHOP_NAME, name=u["name"], quality=deal["quality"],
                                                    list_price=u["list_price"], floor=floor, opening=deal["opening"],
                                                    transcript="\n".join(deal["lines"])))
            print(f"[harness] {raw}", flush=True)
            action = (move or {}).get("action")
            if raw.startswith("[harness failed]"):        # my model is down: decide by code, never pass the error on
                if o is not None and o >= floor:
                    action, move = "accept", {}
                else:
                    start = ask if ask is not None else deal["opening"]
                    target = o if o is not None else floor
                    action, move = "counter", {"price": max(floor, round(start - 0.3 * max(0, start - target)))}
                print(f"[fallback] {action} {move.get('price', o)}", flush=True)
        if action == "accept" and (o is None or o < floor):
            print(f"[guard] accept of {o} below floor {floor} -> counter", flush=True)
            action, move = "counter", {"price": ask or deal["opening"]}
        if action != "accept" and len(deal["offers"]) >= MAX_OFFERS:
            await up.reject(say("That was the last offer we can take on this item. Thank you, and welcome back any time."))
            record(deal, "rejected")
            del self.open[task.id]
            return
        if action == "accept":
            receipt = {"shop": SHOP_NAME, "item": u["sku"], "price": int(o), "task": task.id}
            if u.get("unit"):
                receipt["unit"] = u["unit"]
                receipt["claim"] = u["grade"]                    # the real grade, never a better one
                if u.get("kind") == "goods":
                    receipt["warranty_hours"] = WARRANTY_HOURS
            receipt = sign(receipt)
            print(f"[receipt] {json.dumps(receipt, ensure_ascii=False)}", flush=True)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {int(o)}. Thank you! Quality as stated: {deal['quality']}. "
                                  f"The signed receipt shows exactly the agreed price."))
            record(deal, "deal", int(o))
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("We could not meet on price this time. Thank you, and welcome back any time."))
            record(deal, "rejected")
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = ask or deal["opening"]                       # unparsed answer: repeat the last ask
        price = int(price)
        if price < floor:                                        # the harness may forget the floor; the code does not
            print(f"[guard] ask {price} below floor {floor} -> {floor}", flush=True)
            price = floor
        if ask is not None and price > ask:                      # never raise the ask during a negotiation
            price = ask
        deal["ask"] = price
        deal["asks"].append(price)
        left = MAX_OFFERS - len(deal["offers"])
        if len(deal["lines"]) == 1:
            reply = (f"{u['name']} ({u['sku']}): {deal['quality']}. This negotiation closes after {MAX_OFFERS} offers. "
                     f"Asking {price}. Send your offer as a number.")
        else:
            reply = f"{left} offers left. Counteroffer: {price}."
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
        description=(f"An honest online shop. Ask for an item by name and SKU, e.g. 'I want to buy <name> (<sku>)'. "
                     f"We state each unit's real grade, keep the market certificate, give a {WARRANTY_HOURS} h warranty "
                     f"on goods, and our signed receipt always shows the agreed price. "
                     f"A negotiation closes after {MAX_OFFERS} offers. A coding harness ({CMD.split()[0]}) negotiates."),
        version="2.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item by name and SKU, then send a price. The shop answers with a "
                                       "counteroffer (the last number) or a signed receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
