"""Round 3 seller (started from the kit's starter_round3): sells from the stock the kit keeps in
$MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller_r3.py --port 9301

Week-08 change: the price is set by code, not by a harness call per move. The numbers come from policy.json (the
strategist's, see market_tool.DEFAULT_POLICY) and the ledger: the first ask is the SKU's market median price/list
plus ask_premium, the lowest price is the median minus floor_below_anchor but never under cost x (1 + min_margin),
except food within clearance_h of its date (it would be thrown away at cost anyway). Each buyer offer moves the ask
40% of the way down. The receipt carries the grade it claims (the grade now; none if food is about to drop a grade),
a warranty on goods per policy warranty_h, and the market's certificate (the kit attaches it).
"""
import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

import market_tool as mt

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

NAME = "Harness Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
FOOD_CUTS = (0.80, 0.45)        # shelf life left: A at or above 80%, B at or above 45%, C below (agent.md)
MAX_ROUNDS = 8
# An open negotiation holds its unit so two buyers are not promised the same one. Many buyers walk away without
# CancelTask, so a hold lapses after HOLD_S seconds of silence; before this, every walk-away locked its unit until
# the seller restarted (11-11 probe: 16 of 18 units answered "none of that in stock").
HOLD_S = float(os.environ.get("SELLER_HOLD_S", 300))
CACHE ={"at": 0, "skus": {}, "catalog": {}}   # restock.py writes state/sku_stats.json every minute
SALES_LOG = Path("logs") / "seller_r3.jsonl"


def cached():
    if time.time() - CACHE["at"] > 60:
        try:
            CACHE["skus"] = json.loads((mt.STATE / "sku_stats.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        CACHE["catalog"] = CACHE["catalog"] or mt.catalog()
        CACHE["at"] = time.time()
    return CACHE


def ledger_ratio(sku):
    s = cached()["skus"].get(sku)
    return s["ratio"] if s and s["n"] >= 3 else None


def food_grade(u, now, shelf_h):
    """(grade now, seconds until it drops a grade) from the unit's date, on the market's clock."""
    left_h = (u["expires_at"] - now) / 3600
    frac = left_h / shelf_h
    if frac >= FOOD_CUTS[0]:
        return "A", (frac - FOOD_CUTS[0]) * shelf_h * 3600
    if frac >= FOOD_CUTS[1]:
        return "B", (frac - FOOD_CUTS[1]) * shelf_h * 3600
    return "C", left_h * 3600


def terms(u):
    """Price plan and receipt terms for one unit: ask, floor, claim, warranty, hours left."""
    pol = mt.policy()
    cat = cached()["catalog"].get(u["sku"], {})
    now = mt.market_now()
    lp, cost = u["list_price"], u["cost"]
    hours_left, claim = None, u.get("grade")
    if u.get("kind") == "food" and u.get("expires_at") and cat.get("shelf_hours"):
        g, drop_s = food_grade(u, now, cat["shelf_hours"])
        hours_left = (u["expires_at"] - now) / 3600
        claim = g if drop_s > 120 else None              # about to drop a grade: claim nothing rather than be wrong
        grade = g
    else:
        grade = u.get("grade") or "B"
    anchor = ledger_ratio(u["sku"]) or pol["anchor_ratio"].get(grade, 0.68)
    floor = max(round(cost * (1 + pol["min_margin"])), round(lp * (anchor - pol["floor_below_anchor"])))
    if hours_left is not None and hours_left < pol["clearance_h"]:
        floor = max(1, round(cost * 0.3))                # clearance: some money beats losing the cost
    ask = max(floor, round(lp * min(0.98, anchor + pol["ask_premium"])))
    warranty = pol["warranty_h"].get(grade, 0) if u.get("kind") == "goods" else 0
    return {"ask": ask, "floor": floor, "claim": claim, "warranty": warranty, "hours_left": hours_left,
            "grade": grade, "anchor": anchor}


def describe(u, t):
    bits = [f"grade {t['claim']}" if t["claim"] else "grade not claimed"]
    if t["hours_left"] is not None:
        bits.append(f"{t['hours_left']:.0f} h of shelf life left")
    if t["warranty"]:
        bits.append(f"{t['warranty']} h warranty (full refund if it breaks)")
    bits.append("market certificate attached")
    return f"{u['name']} [{u['sku']}], list {u['list_price']}: " + ", ".join(bits)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": int(RESERVE[item] * 1.3), "cost": RESERVE[item],
                         "unit": None, "expires_at": None, "supplied_at": 0}
    units = inv.get("units", [])
    fit = [u for u in units if u["grade"] != "expired" and u["unit"] not in taken and
           ((u.get("category") or "").split(">")[-1].lower() in want or (u.get("name") or "").lower() in want
            or u["sku"].lower() in want)]
    named = [u for u in fit if u["sku"].lower() in want]
    fit = named or fit                                   # a SKU the buyer named comes first
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines"}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now_s = time.time()
        for tid in [k for k, d in self.open.items() if now_s - d["at"] > HOLD_S]:
            del self.open[tid]                           # silent too long: let the unit go
        deal = self.open.get(task.id)

        if deal is None and context.current_task is not None:
            await up.reject(say(f"This negotiation lapsed after {HOLD_S / 60:.0f} min of silence. Ask for the item again."))
            return

        if deal is None:
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]})
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            t = terms(unit)
            self.open[task.id] = {"unit": unit, "t": t, "ask": t["ask"], "rounds": 0, "at": now_s}
            await up.requires_input(say(f"{describe(unit, t)}. Asking {t['ask']}. Send your offer as a number."))
            return

        deal["rounds"] += 1
        deal["at"] = now_s
        u, t = deal["unit"], deal["t"]
        nums = [int(x.replace(",", "")) for x in re.findall(r"(?<![A-Za-z#\d])\d[\d,]*", text)]
        nums = [n for n in nums if n >= 0.05 * u["list_price"]]     # not hours, grades or parts of a SKU
        offer = nums[-1] if nums else None
        if offer is None and re.search(r"\b(accept|deal|ok|yes)\b", text.lower()):
            offer = deal["ask"]
        if offer is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}."))
            return
        near = deal["ask"] - 0.35 * (deal["ask"] - t["floor"])
        if offer >= deal["ask"] or (offer >= t["floor"] and (offer >= near or deal["rounds"] >= 4)):
            price = min(offer, deal["ask"])
            now = terms(u)                               # food may have dropped a grade while we talked
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
            if now["claim"]:
                receipt["claim"] = now["claim"]
            if now["warranty"]:
                receipt["warranty_hours"] = now["warranty"]
            receipt = sign(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            SALES_LOG.parent.mkdir(exist_ok=True)
            with SALES_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task.id, "sku": u["sku"],
                                    "unit": u["unit"], "grade": now["grade"], "claim": now["claim"],
                                    "warranty": now["warranty"], "cost": u["cost"], "list": u["list_price"],
                                    "price": price, "floor": t["floor"], "first_ask": t["ask"],
                                    "anchor": t["anchor"], "rounds": deal["rounds"]}, ensure_ascii=False) + "\n")
            return
        if deal["rounds"] >= MAX_ROUNDS:
            await up.reject(say(f"No deal. My lowest price is {t['floor']}."))
            del self.open[task.id]
            return
        new = max(t["floor"], round(deal["ask"] - 0.4 * (deal["ask"] - max(offer, t["floor"]))))
        deal["ask"] = min(deal["ask"], new)
        last = " This is my lowest price." if deal["ask"] == t["floor"] else ""
        await up.requires_input(say(f"Counteroffer: {deal['ask']}.{last}"))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Furniture, bedding and food from stock. Ask for an item by name or SKU. Honest grades with the "
                    "market's certificate; goods graded A or B carry a 6 h warranty (full refund if it breaks).",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
