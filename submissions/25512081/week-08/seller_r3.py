"""Round 3 seller: sells real units from the stock the kit keeps in $MARKET_DIR/inventory.json, over A2A v1.0.

    python seller_r3.py --port 9301

Week 7 showed that fast, certain deals win buyers, so every price here is set in code from the unit's grade,
list price and cost, and a reply never waits on a model. The restock agent (restock.py) is where the model works.

- Opening ask: the shop manager's share of list price for the category, times the grade factor and the stale
  markdown, times a multiplier chosen by Thompson sampling from our own deal outcomes (see ARMS).
- First message: find the unit the buyer asked for (SKU first, then name, then category) and answer with the
  facts the buyer's kit checks before paying (grade, certificate, hours left, warranty) and the ask, which is
  always the last number in the reply.
- Never below cost: the floor is cost plus FLOOR_MARGIN.
- An offer at or above the ask closes at the ask; an offer near it closes at the offer; anything lower gets a
  counter that gives up a shrinking share of the room down to the floor (STEPS).
- The receipt claims the unit's true grade, keeps the market's certificate on and carries the warranty we
  promised in the reply, so the ledger shows every claim as honest.
Before Round 3 there is no stock: the week-7 items sell at their reserve, as in Round 2.
"""
import argparse
import base64
import json
import math
import os
import random
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

NAME = os.environ.get("SHOP_NAME", "kimauve")
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2: the week-7 items

# the opening ask as a share of the list price for a grade-A unit: the shop manager (planner.py) sets it per
# category in .market/prices.json, else DEFAULT_SHARE. The buyer's value is the list price, so a lower ask is
# the buyer's surplus. Lower grades open lower, and stock held over a day is marked down; both stay in code.
DEFAULT_SHARE = 0.85
GRADE_FACTOR = {"A": 1.0, "B": 0.92, "C": 0.80}
STALE_MARKDOWN = 0.05     # per day in stock, up to 3 days
_prices = {"t": 0, "rules": {}}
FLOOR_MARGIN = float(os.environ.get("SHOP_FLOOR_MARGIN", "0.05"))
CLOSE_GAP = 0.03          # an offer within 3% of the ask closes at the offer
MAX_DISCOUNT = 0.10       # haggling never takes the ask more than 10% below the opening ask
MAX_ROUNDS = 6
STEPS = (0.4, 0.25, 0.2, 0.15)   # each counter gives up this share of the room between opening ask and floor
# Learning the price from our own talks: Thompson sampling, a bandit (the simplest kind of reinforcement
# learning). Each category has ARMS, multipliers on the manager's share. Per arm we count talks that ended in a
# deal (success) or without one: the buyer took another shop's quote, walked after haggling, or we refused
# (failure). For each new buyer, every arm draws a deal chance from its Beta posterior and we open with the arm
# whose draw times its margin (price minus cost) is highest. Arms rarely tried draw widely, so they still get
# tried; arms that lose stop being picked. A category with few talks borrows from all categories' counts,
# weighted by POOL. Counts live in .market/price_bandit.json.
ARMS = (0.88, 0.94, 1.0, 1.06)
POOL = 0.2
TYPICAL_CLOSE = 0.95      # a deal closes at about this share of the opening ask, after haggling
SHARE_BOUNDS = (0.55, 0.97)
CLEARANCE_HOURS = 12      # food this close to its date opens 20% lower and may go below cost: unsold, it is a full loss
CLEARANCE_FLOOR = 0.6     # share of cost
WARRANTY = {"A": 6}       # goods grade -> warranty hours; B and C goods carry none
MIN_FOOD_HOURS = 2        # never ship food with less shelf life left than this
ACCEPT = re.compile(r"\b(ok|okay|yes|deal|accept|accepted|agree|agreed|take it|buy it|sold)\b", re.I)


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} [seller] {msg}", flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def inventory():
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hours_left(u):
    return None if not u.get("expires_at") else (u["expires_at"] - time.time()) / 3600


def find_unit(text, taken=()):
    """The in-stock unit for what the buyer asked (SKU, then name, then category). Among several units of the
    same product: goods ship the best grade, food the one that expires first but still has MIN_FOOD_HOURS."""
    want = text.lower()
    inv = inventory()
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": RESERVE[item], "cost": RESERVE[item],
                         "kind": "goods", "grade": "A", "unit": None, "expires_at": None, "cert": False}
    units = [u for u in inv.get("units", []) if u["grade"] != "expired" and u["unit"] not in taken
             and (u["kind"] != "food" or (hours_left(u) or 0) >= MIN_FOOD_HOURS)]
    for match in (lambda u: u["sku"].lower() in want,
                  lambda u: (u.get("name") or "").lower() in want,
                  lambda u: (u.get("category") or "").split(">")[-1].lower() in want):
        fit = [u for u in units if match(u)]
        if fit:
            if fit[0]["kind"] == "food":
                return min(fit, key=lambda u: u["expires_at"] or math.inf)
            return min(fit, key=lambda u: (u["grade"], u["supplied_at"]))
    return None


def _load(name, default):
    try:
        return json.loads((MARKET_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


BANDIT = _load("price_bandit.json", {"all": {}, "cats": {}})   # arm -> [deals, talks without one]


def base_share(category):
    if time.time() - _prices["t"] > 30:
        _prices["rules"] = _load("prices.json", {}).get("rules", _prices["rules"])
        _prices["t"] = time.time()
    return _prices["rules"].get(category, DEFAULT_SHARE)


def pick_arm(u, base, floor):
    """Thompson sampling: the arm whose sampled deal chance times margin is highest, and the opening ask it gives."""
    cat = BANDIT["cats"].get(u.get("category"), {})
    best = None
    for arm in ARMS:
        k = str(arm)
        s_, f_ = cat.get(k, [0, 0])
        gs, gf = BANDIT["all"].get(k, [0, 0])
        theta = random.betavariate(1 + s_ + POOL * gs, 1 + f_ + POOL * gf)
        ask = max(floor, int(round(u["list_price"] * min(max(base * arm, SHARE_BOUNDS[0]), SHARE_BOUNDS[1]), -1)))
        score = theta * (ask * TYPICAL_CLOSE - u["cost"])
        if best is None or score > best[0]:
            best = (score, arm, ask, theta)
    return best[1], best[3]


def learn(category, arm, deal):
    """One finished talk opened with `arm`: a deal is a success, anything else a failure."""
    if not category or arm is None:
        return
    i = 0 if deal else 1
    for counts in (BANDIT["cats"].setdefault(category, {}), BANDIT["all"]):
        counts.setdefault(str(arm), [0, 0])[i] += 1
    tmp = MARKET_DIR / "price_bandit.tmp"
    tmp.write_text(json.dumps(BANDIT, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MARKET_DIR / "price_bandit.json")
    log(f"[bandit] {category.split('>')[-1]}: arm {arm} {'deal' if deal else 'no deal'}, "
        f"counts {BANDIT['cats'][category]}")


def terms(u):
    """(ask, floor, warranty hours, bandit arm or None) for one unit."""
    if u["unit"] is None:
        return u["cost"], u["cost"], 0, None
    floor = math.ceil(u["cost"] * (1 + FLOOR_MARGIN))
    days = min(3, int((time.time() - u.get("supplied_at", time.time())) // 86400))
    share = base_share(u.get("category")) * GRADE_FACTOR.get(u["grade"], 0.8) * (1 - STALE_MARKDOWN * days)
    arm = None
    if u["kind"] == "food" and (hours_left(u) or 0) < CLEARANCE_HOURS:
        share *= 0.8                          # clearance: not a price test, the bandit stays out
        floor = math.ceil(u["cost"] * CLEARANCE_FLOOR)
    else:
        arm, theta = pick_arm(u, share, floor)
        log(f"[bandit] {u['category'].split('>')[-1]}: opening with arm {arm} (sampled deal chance {theta:.2f})")
        share *= arm
    ask = max(floor, int(round(u["list_price"] * min(max(share, SHARE_BOUNDS[0]), SHARE_BOUNDS[1]), -1)))
    warranty = WARRANTY.get(u["grade"], 0) if u["kind"] == "goods" else 0
    return ask, floor, warranty, arm


def facts(u, warranty):
    """What the buyer's kit checks before paying, stated as it is."""
    if u["unit"] is None:
        return u["name"]
    bits = [f"{u['name']} ({u['sku']})", f"grade {u['grade']}",
            "market certificate included" if u.get("cert", True) else "no certificate"]
    if u["kind"] == "food":
        bits.append(f"{hours_left(u):.0f} hours of shelf life left")
    else:
        bits.append(f"{warranty}h full-refund warranty" if warranty else "no warranty")
    bits.append(f"list price {u['list_price']}")
    return ", ".join(bits)


def last_number(text):
    nums = re.findall(r"\d[\d,]*", text)
    return int(nums[-1].replace(",", "")) if nums else None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}   # task id -> {"unit", "ask", "floor", "warranty", "rounds"}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        tid = task.id[:8]
        log(f"[{tid}] <- {text[:160]!r}")

        def say(t):
            log(f"[{tid}] -> {t}")
            return up.new_agent_message(parts=[new_text_part(t)])

        deal = self.open.get(task.id)
        if deal is None:
            taken = {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]}
            u = find_unit(text, taken)
            if u is None:
                await up.reject(say("Sorry, none of that in stock."))
                return
            ask, floor, warranty, arm = terms(u)
            # profit is half the shop's score: concede at most MAX_DISCOUNT, and never below cost
            floor = max(floor, int(ask * (1 - MAX_DISCOUNT)))
            deal = self.open[task.id] = {"unit": u, "ask": ask, "opening": ask, "floor": floor,
                                         "warranty": warranty, "rounds": 0, "arm": arm}
            await up.requires_input(say(f"{facts(u, warranty)}. Send the ask or say ok to buy. "
                                        f"Our ask: {ask}"))
            return

        deal["rounds"] += 1
        ask, floor = deal["ask"], deal["floor"]
        offer = last_number(text)
        if offer is None and ACCEPT.search(text):
            offer = ask
        if offer is not None and offer >= ask:
            await self._close(task, up, say, deal, ask)
        elif offer is not None and offer >= max(floor, ask * (1 - CLOSE_GAP)):
            await self._close(task, up, say, deal, offer)
        elif deal["rounds"] >= MAX_ROUNDS:
            del self.open[task.id]
            if deal["unit"]["unit"]:
                learn(deal["unit"].get("category"), deal["arm"], False)
            await up.reject(say(f"No deal. Our last ask was {ask}."))
        else:
            if offer is not None:            # a third of the gap, but never more than this round's step
                step = STEPS[min(deal["rounds"], len(STEPS)) - 1] * (deal["opening"] - floor)
                deal["ask"] = max(floor, int(round(ask - min((ask - offer) / 3, step), -1)))
            await up.requires_input(say(f"Counteroffer: {deal['ask']}"))

    async def _close(self, task, up, say, deal, price):
        u = deal["unit"]
        receipt = {"shop": NAME, "item": u["sku"], "price": int(price), "task": task.id}
        if u["unit"]:
            receipt.update(unit=u["unit"], claim=u["grade"], cert=True, warranty_hours=deal["warranty"])
        await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
        await up.complete(say(f"Deal: {u['name']} at {int(price)}."))
        if u["unit"]:
            learn(u.get("category"), deal["arm"], True)
        log(f"DEAL {u['sku']} {u['name'][:30]} grade {u['grade']} at {int(price)} "
            f"(cost {u['cost']}, list {u['list_price']}, warranty {deal['warranty']}h)")
        del self.open[task.id]

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal and deal["unit"]["unit"]:
            learn(deal["unit"].get("category"), deal["arm"], False)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=("Real stock, honest grades. Every reply states the unit's true grade, the market "
                     "certificate, shelf life left (food) and warranty (grade A goods: 6h full refund). "
                     "Prices below list. Answers in milliseconds; send the ask or say ok and the signed "
                     "receipt comes back at once."),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Buy a unit from stock",
                           description="Ask for a SKU: 'I want to buy <name> (<sku>)'. The ask is the last number "
                                       "in each reply. Send it or say ok to buy.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 포기김치 10kg (G00001)", "ok"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    log(f"{NAME}: round 3 seller, stock from {MARKET_DIR}")
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
