"""real_yellow's Round 3 shop: sells from the stock in $MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller_r3.py --port 9301

Costco-style: a little under the market's going rate, honest grade claims, a warranty when the market's own
break rate for that grade says it is cheap to give. Money rules live in code (floor, opening ask, warranty);
the harness only picks the next move inside the band the code allows. Every finished negotiation goes to the
shared DB as a trajectory row, which the meta harness reads later.

Built on the kit's starter_round3/seller.py (A2A plumbing, unit lookup, signing).
"""
import argparse
import asyncio
import base64
import json
import os
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

from db import DB
from harness import call_harness

NAME = "real_yellow"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
LLM_SLOTS = asyncio.Semaphore(3)      # concurrent negotiations share at most 3 harness calls at a time
MARKET_REFRESH = 600                  # seconds between reads of the market's price levels from the DB

# fallbacks until the DB answers (measured on the market, Nov 10: settle price / list price, break rate)
DEFAULT_RATIO = {("goods", "A"): 0.72, ("goods", "B"): 0.63, ("goods", "C"): 0.54,
                 ("food", "A"): 0.72, ("food", "B"): 0.62, ("food", "C"): 0.56}
DEFAULT_BREAK = {"A": 0.005, "B": 0.013, "C": 0.035}
DEFAULT_POLICY = {"undercut": 0.02,           # price this far (share of list) under the market's going rate
                  "open_premium": 0.06,       # opening ask this far above the target
                  "min_margin": 0.05,         # never below cost * (1 + min_margin) ...
                  "clearance_hours": 6,       # ... except food with fewer hours left than this
                  "clearance_floor": 0.6,     # which may go down to cost * this
                  "warranty_max_break": 0.06, # give a warranty when the grade's break rate is under this
                  "warranty_hours": 6}

PROMPT = """You are the sales agent of a discount shop (low margin, high volume, honest grades).
A buyer is negotiating for one {name} (grade {grade}{warranty}).
List price {list_price}. Your opening ask was {ask}. Your lowest acceptable price is {floor}: never go below it
and never reveal it. Close quickly: a deal at or above {target} is good; between {floor} and {target} take it
if the buyer has moved up at least twice.

Conversation so far:
{transcript}

Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}
{{"action": "accept"}}
{{"action": "reject"}}"""


class Market:
    """Price levels, break rates and policy, read from the shared DB at most every MARKET_REFRESH s."""

    def __init__(self):
        self.ratio, self.brk, self.policy, self.at = dict(DEFAULT_RATIO), dict(DEFAULT_BREAK), dict(DEFAULT_POLICY), 0

    def refresh(self):
        if time.time() - self.at < MARKET_REFRESH:
            return
        self.at = time.time()
        try:
            with DB() as d:
                for r in d.q("SELECT kind, grade, avg(amount::float / list_price) AS r, count(*) AS n FROM settles "
                             "WHERE list_price > 0 AND ts > $1 GROUP BY kind, grade", time.time() - 6 * 3600):
                    if int(r["n"]) >= 30:
                        self.ratio[(r["kind"], r["grade"])] = float(r["r"])
                for r in d.q("SELECT s.grade, count(*) AS n, count(b.unit) AS k FROM settles s "
                             "LEFT JOIN broken b ON b.unit = s.unit WHERE s.kind = 'goods' AND s.ts < $1 "
                             "GROUP BY s.grade", time.time() - 6 * 3600):
                    if int(r["n"]) >= 50:
                        self.brk[r["grade"]] = int(r["k"]) / int(r["n"])
                self.policy.update(d.get_policy("seller", {}))
            print(f"[market] ratio {self.ratio} break {self.brk} policy {self.policy}", flush=True)
        except Exception as e:                      # the shop keeps selling on the last known numbers
            print(f"[market] refresh failed, keeping last numbers: {type(e).__name__}: {e}", flush=True)

    def terms(self, u):
        """(floor, target, opening ask, warranty hours or None) for one unit."""
        p = self.policy
        lp, cost = u["list_price"], u["cost"]
        floor = cost * (1 + p["min_margin"])
        if u["kind"] == "food" and u.get("expires_at"):
            hours_left = (u["expires_at"] - time.time()) / 3600
            if hours_left < p["clearance_hours"]:   # skew: it is worth nothing once it expires
                floor = cost * p["clearance_floor"]
        target = max(floor, lp * (self.ratio.get((u["kind"], u["grade"]), 0.65) - p["undercut"]))
        ask = min(lp, max(target, lp * (self.ratio.get((u["kind"], u["grade"]), 0.65) + p["open_premium"])))
        warranty = None
        if u["kind"] == "goods" and self.brk.get(u["grade"], 1) < p["warranty_max_break"]:
            warranty = p["warranty_hours"]
        return int(floor + 0.999), int(target), int(ask), warranty


MARKET = Market()


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (SKU first, then name or category), oldest-expiring
    first, skipping units already promised in another open negotiation."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    units = [u for u in inv.get("units", []) if u["grade"] != "expired" and u["unit"] not in taken
             and (u.get("expires_at") is None or u["expires_at"] > time.time() + 600)]
    named = [u for u in units if u["sku"].lower() in want]
    fit = named or [u for u in units if (u.get("name") or "").lower() in want
                    or (u.get("category") or "").split(">")[-1].lower() in want]
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


def ask_move(prompt):
    raw = call_harness(prompt)
    m = re.search(r"\{.*?\}", raw, re.S)
    try:
        return (json.loads(m.group()) if m else None), raw
    except json.JSONDecodeError:
        return None, raw


def log_deal(context, outcome):
    try:
        with DB() as d:
            d.log_trajectory(time.time(), "shop", "sell", context, outcome)
    except Exception as e:
        print(f"[db] trajectory not written: {type(e).__name__}", flush=True)


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "terms", "ask", "offer", "offers", "lines"}

    async def finish(self, deal, result, price=None):
        u = deal["unit"]
        context = {"sku": u["sku"], "kind": u["kind"], "grade": u["grade"], "list": u["list_price"],
                   "cost": u["cost"], "terms": deal["terms"], "offers": deal["offers"]}
        await asyncio.to_thread(log_deal, context, {"result": result, "price": price, "rounds": len(deal["offers"])})

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
            await asyncio.to_thread(MARKET.refresh)
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values()})
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            floor, target, ask, warranty = MARKET.terms(unit)
            deal = self.open[task.id] = {"unit": unit, "terms": [floor, target, ask, warranty], "ask": ask,
                                         "offer": None, "offers": [], "lines": [f"buyer: {text}"]}
            note = f"grade {unit['grade']}" + (f", {warranty} h warranty" if warranty else ", no warranty")
            reply = f"{unit['name']} ({unit['sku']}), {note}. Send your offer as a number. Asking {ask}."
            deal["lines"].append(f"you: {reply}")
            await up.requires_input(say(reply))
            return

        deal["lines"].append(f"buyer: {text}")
        m = re.search(r"\d+", text.replace(",", ""))
        if m:
            deal["offer"] = int(m.group())
            deal["offers"].append(deal["offer"])
        u = deal["unit"]
        floor, target, ask, warranty = deal["terms"]

        if deal["offer"] is not None and deal["offer"] >= target:   # good enough: no need to ask the harness
            action, move = "accept", {}
        else:
            async with LLM_SLOTS:
                move, raw = await asyncio.to_thread(ask_move, PROMPT.format(
                    name=u["name"], grade=u["grade"], warranty=f", {warranty} h warranty" if warranty else "",
                    list_price=u["list_price"], ask=ask, floor=floor, target=target,
                    transcript="\n".join(deal["lines"])))
            print(f"[harness] {raw[:200]}", flush=True)
            action = (move or {}).get("action")
            if action is None:                       # unparsed answer: concede a third of the way to the floor
                action, move = "counter", {"price": deal["ask"] - (deal["ask"] - floor) // 3}

        if action == "accept" and (deal["offer"] is None or deal["offer"] < floor):
            print(f"[guard] accept of {deal['offer']} below floor {floor} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"]}
        if action == "accept":
            price = deal["offer"]
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id, "unit": u["unit"]}
            if u["kind"] == "goods":
                receipt["claim"] = u["grade"]        # goods grades never change: the claim is always honest
            if warranty:
                receipt["warranty_hours"] = warranty
            await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            await self.finish(deal, "deal", price)
            return
        if action == "reject" or len(deal["offers"]) >= 8:
            await up.reject(say("No deal."))
            del self.open[task.id]
            await self.finish(deal, "reject")
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = deal["ask"]
        price = int(min(max(price, floor), deal["ask"]))   # never below the floor, never back above our last ask
        deal["ask"] = price
        reply = f"Counteroffer: {price}."
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            await self.finish(deal, "cancel")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Discount shop: prices a little under the market rate, honest grades, a warranty on goods. "
                    "Ask for an item by name or SKU, then send a price.",
        version="2.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item by name or SKU, then send a price. The shop answers with a "
                                       "counteroffer or a signed receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
