"""Inventory-aware Round 3 A2A seller.

The kit writes MARKET_DIR/inventory.json.  This seller reserves one concrete
unit per open task, prices from cost/list price/expiry, tells the truth about
grade, includes the market certificate, and signs only through ReserveSigner.
Rounds 0--2 retain the three week-07 products so the old market test remains a
useful compatibility check.
"""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import ledger
import round_clock
import strategy_params
from seller_guard import ReserveSigner


NAME = "Sealed Stock Shop"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
SHOP_KEY = "shop_key.pem"
EVENTS = MARKET_DIR / "seller_events.jsonl"   # dated copy of the strategy events for tune.py; the kit log has no date
ROUND2_RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
GRADE_RANK = {"A": 0, "B": 1, "C": 2}


def strategy(event, **fields):
    row = {"event": event, **fields}
    print("[strategy] " + json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        MARKET_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **row}, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def load_inventory():
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def round2_unit(text):
    item = next((name for name in ROUND2_RESERVE if name in text.lower()), None)
    if not item:
        return None
    reserve = ROUND2_RESERVE[item]
    return {"sku": item, "name": item, "category": item, "list_price": int(reserve * 1.30), "kind": "goods",
            "cost": reserve, "grade": "A", "unit": None, "expires_at": None, "supplied_at": 0}


def find_unit(text, taken=()):
    """Match exact SKU/name first; otherwise category leaf. Never promise one unit twice."""
    inv = load_inventory()
    if inv.get("round", 0) < 3:
        return round2_unit(text)
    now, want = time.time(), text.casefold()
    fit = []
    for unit in inv.get("units") or []:
        if unit.get("unit") in taken or unit.get("grade") == "expired":
            continue
        if unit.get("expires_at") and unit["expires_at"] <= now:
            continue
        sku = str(unit.get("sku", ""))
        name = str(unit.get("name", ""))
        category = str(unit.get("category", ""))
        leaf = category.split(">")[-1].strip()
        if sku.casefold() in want or name.casefold() in want or (leaf and leaf.casefold() in want):
            fit.append(unit)
    exact = [unit for unit in fit if str(unit.get("sku", "")).casefold() in want]
    fit = exact or fit
    if not fit:
        return round2_unit(text)  # keeps the fixed compatibility test usable after inventory.json exists

    def ship_order(unit):
        if unit.get("kind") == "food":
            return (0, unit.get("expires_at") or float("inf"), unit.get("cost", 0))
        return (1, GRADE_RANK.get(unit.get("grade"), 9), unit.get("supplied_at", 0), unit.get("cost", 0))

    return min(fit, key=ship_order)


def policy_arm(unit, task, volume_share=None):
    """Stable per (sku, task): the tuner's seller_volume_share for goods, 0.15 more for food (it spoils)."""
    share = strategy_params.DEFAULTS["seller_volume_share"] if volume_share is None else volume_share
    share = min(1.0, share + 0.15) if unit.get("kind") == "food" else share
    digest = hashlib.sha256(f"{unit.get('sku')}:{task}".encode()).digest()
    return "volume" if int.from_bytes(digest[:8], "big") / 2**64 < share else "margin"


def price_plan(unit, task, now=None, params=None, market=None, pressure=None, hours_to_end=None):
    """Return an explainable ask/floor. A floor is this agent's policy, not a market rule.
    `params` are the tuner's knobs (margins per arm, grade B cap); defaults when not given.
    `market` is ledger.market_price(): when the market paid a known price for this SKU (or its category) lately,
    the ask follows that price (volume arm 0.97x, margin arm 1.05x) instead of the cost-based target. Either way
    the ask stays inside [cost x 1.03, grade cap]: not below cost, not above what the grade is worth."""
    now = now or time.time()
    params = {**strategy_params.DEFAULTS, **(params or {})}
    cost = max(1, int(round(unit["cost"])))
    list_price = max(cost, int(round(unit["list_price"])))
    arm = policy_arm(unit, task, params["seller_volume_share"])
    target = max(cost * (params["seller_margin_volume"] if arm == "volume" else params["seller_margin_margin"]),
                 list_price * (0.82 if arm == "volume" else 0.95))
    # the category's pressure from restock's category_pressure.json (my last 6 h): where units close 60%+ of the
    # time they carry 5% more, where they close under 40% they give 3% back and the margin arm steps aside (11-05
    # 22:40: sofas at 39% close with 4 in stock; turnover beats 100k of margin). 11-05 01:00: the market has no busy
    # hour (orders arrive flat around the clock), so the regime is per category, not per time of day. Opens per
    # deal is not the signal: juice had 4.3 opens per deal because buyers kept leaving (close rate 23%), not queueing.
    pressure = pressure or {}
    regime = "even"
    if pressure.get("opens", 0) >= 10 and pressure.get("close_rate", 0) >= 0.6:
        regime = "sought"
    elif pressure.get("opens", 0) >= 20 and pressure.get("close_rate", 1.0) < 0.4:
        regime = "slow"
    market_price, market_n, market_src = market if market and market[0] else (None, 0, "none")
    # 11-06 18:30, 287 settles of SKUs the market also sold: mine closed at 0.954 of the market's median for the
    # same SKU (mattresses 0.955, 12 minutes to sell, 80% of the stock bought in 5 h sold). Nothing opens below
    # the market any more, and the floor holds 96% of it unless the category is slow or the unit underwater.
    if market_price:
        target = market_price * (1.00 if arm == "volume" or regime == "slow" else 1.05) * params["seller_markup"]
    target *= {"sought": 1.05, "slow": 0.97}.get(regime, 1.0)
    cap = list_price * {"A": 0.99, "B": params["grade_cap_B"], "C": 0.65}.get(unit.get("grade"), 0.85)
    target = min(target, cap)
    # goods pay 1% of cost a day in storage: from the third day the ask comes down 5% a day, to 70% of the target
    days = (now - unit["supplied_at"]) / 86400 if unit.get("kind") == "goods" and isinstance(unit.get("supplied_at"), (int, float)) and unit["supplied_at"] > 0 else 0
    if days > 2:
        target *= max(0.70, 1 - 0.05 * (days - 2))
    floor = cost * 1.03
    if market_price and regime != "slow":
        floor = max(floor, min(market_price * 0.96, cap))   # never above what the grade is worth
    # underwater: the market now pays less than this unit cost (11-06 18:00: sofas bought at 1.74M while the
    # category's settles fell to 1.66M, 25 opens -> 4 deals). After six hours in stock the floor follows the market
    # (93% of it): a few percent lost now beats storage for days and the dump at the close.
    if market_price and unit.get("kind") == "goods" and days * 24 >= 1 and cost * 1.03 > market_price * 0.97:
        target, floor, regime = min(target, market_price * 0.97), min(floor, market_price * 0.93), "underwater"
    hours_left = None

    if unit.get("kind") == "food" and unit.get("expires_at"):
        hours_left = max(0.0, (unit["expires_at"] - now) / 3600)
        if hours_left <= 1:
            target, floor = min(target, cost * 0.65), cost * 0.50
        elif hours_left <= 3:
            target, floor = min(target, cost * 0.90), cost * 0.60
        elif hours_left <= 6:
            target, floor = min(target, cost * 1.02), cost * 0.85
    # the round's close: stock is worth nothing afterwards, so the last day sells down, the last 12 h sell under
    # cost, and the last 3 h take anything above one credit (round_clock)
    if hours_to_end is not None:
        if hours_to_end < round_clock.LAST_HOURS:
            target, floor, regime = min(target, cost * 0.6), 1, "last-hours"
        elif hours_to_end < round_clock.DUMP_HOURS:
            target, floor, regime = min(target, cost * 0.9), cost * 0.6, "dump"
        elif hours_to_end < round_clock.SELL_DOWN_HOURS:
            target, floor, regime = min(target, (market_price or cost * 1.05) * 0.95), cost * 0.9, "sell-down"
    floor = max(1, int(round(floor)))
    ask = max(floor, int(round(target)))
    return {"arm": arm, "ask": ask, "floor": floor, "cost": cost, "list_price": list_price,
            "hours_left": round(hours_left, 2) if hours_left is not None else None,
            "market": round(market_price) if market_price else None, "market_n": market_n, "market_src": market_src,
            "regime": regime}


def receipt_options(unit):
    options = {"claim": unit.get("grade"), "cert": True}
    if unit.get("unit"):
        options["unit"] = unit["unit"]
    if unit.get("kind") == "goods":
        warranty = {"A": 6, "B": 3, "C": 0}.get(unit.get("grade"), 0)
        if warranty:
            options["warranty_hours"] = warranty
    return options


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text, meta=None: up.new_agent_message(parts=[new_text_part(text)], metadata=meta)
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        tid = task.id[:8]

        if deal is None:
            taken = {row["unit"].get("unit") for row in self.open.values() if row["unit"].get("unit")}
            unit = find_unit(text, taken)
            if unit is None:
                await up.reject(say("Sorry, no matching unit is in stock."))
                return
            market = ledger.market_price(ledger.load_stats(MARKET_DIR), unit["sku"], unit.get("category"), unit.get("list_price"))
            pressure = (ledger.load_json(MARKET_DIR / "category_pressure.json") or {}).get(unit.get("category"))
            plan = price_plan(unit, task.id, params=strategy_params.load(), market=market, pressure=pressure,
                              hours_to_end=round_clock.hours_left())   # all re-read per task: live without a restart
            signer = ReserveSigner(NAME, unit["sku"], plan["floor"], task.id, SHOP_KEY)
            deal = self.open[task.id] = {"unit": unit, "plan": plan, "ask": plan["ask"], "offers": [], "signer": signer}
            strategy("seller_open", task=tid, sku=unit["sku"], grade=unit.get("grade"), **plan)
            quality = f", certified grade {unit.get('grade')}" if unit.get("grade") else ""
            reply = f"{unit['name']} ({unit['sku']}){quality}: asking {plan['ask']}. Send your offer as a number."
            await up.requires_input(say(reply, {"ask": plan["ask"], "item": unit["sku"], "grade": unit.get("grade"), "cert": True}))
            return

        numbers = [int(float(value)) for value in re.findall(r"\d+(?:\.\d+)?", text)]
        if not numbers:
            await up.requires_input(say(f"Send one price. Current ask: {deal['ask']}.", {"ask": deal["ask"]}))
            return
        offer = numbers[-1]
        deal["offers"].append(offer)
        plan, unit = deal["plan"], deal["unit"]
        rounds = len(deal["offers"])
        repeated = len(deal["offers"]) >= 2 and deal["offers"][-1] == deal["offers"][-2]

        if offer >= plan["ask"] or (rounds >= 2 and offer >= plan["floor"]):
            receipt = deal["signer"].sign(offer, **receipt_options(unit))
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {unit['name']} at {offer}.", {"offer": offer, "item": unit["sku"]}))
            strategy("seller_deal", task=tid, sku=unit["sku"], arm=plan["arm"], price=offer,
                     cost=plan["cost"], margin=offer - plan["cost"], rounds=rounds)
            del self.open[task.id]
            return
        if rounds >= 5 or (rounds >= 3 and repeated):
            strategy("seller_reject", task=tid, sku=unit["sku"], arm=plan["arm"], offer=offer, floor=plan["floor"])
            await up.reject(say(f"No deal below {plan['floor']}."))
            del self.open[task.id]
            return

        counter = max(plan["floor"], int(round((3 * deal["ask"] + offer) / 4)))   # concede a quarter of the gap, not a third
        deal["ask"] = min(deal["ask"], counter)
        strategy("seller_counter", task=tid, sku=unit["sku"], offer=offer, counter=deal["ask"], round=rounds)
        await up.requires_input(say(f"Counteroffer: {deal['ask']}.", {"ask": deal["ask"], "item": unit["sku"]}))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="An inventory-aware shop with certified grades, expiry liquidation, and guarded receipt signing.",
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate an in-stock SKU",
                           description="Ask by product name or SKU, then offer a whole-number price.",
                           tags=["shopping", "inventory", "certificate", "negotiation"],
                           examples=["I want 종가 포기김치 (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9301)
    args = parser.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
