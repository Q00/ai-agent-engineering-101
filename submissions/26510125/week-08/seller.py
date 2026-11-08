"""Round 3 starter seller: sells from the stock the kit keeps in $MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller.py --port 9301

The harness decides every move; this file only speaks A2A, finds a unit for what the buyer asks, and signs the
receipt with shop_key.pem (the kit makes it). It ships the unit that expires first, says nothing about quality,
never offers a warranty, and never goes below the unit's cost. All of that is yours to change.
"""
import argparse
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

from harness import COMMAND as CMD
from market_price import median_price

NAME = "yeonsong2"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))

def floor_price(cost, kind, urgent, target):
    """Never below cost. Fresh goods: +10 %, but never above 90 % of where the market settles (buyers anchor there
    and offer ~85 % of it; a floor above that only produces walk-aways). Food and grade C: cost, it must go."""
    if urgent or kind != "goods":
        return cost
    return max(cost, min(int(cost * 1.10), int(target * 0.9)))


def price_move(ask, offer, floor, target, below):
    """Rule-based pricing. Measured on the live log: with the LLM at ~10 s a turn only 18 % of visits became deals and
    most buyers left mid-negotiation; the top shops close 62 %. Returns (action, price).
    floor = never below; target = where the market settles this SKU; below = offers under the floor so far."""
    if offer is not None and offer >= floor:
        return "accept", offer
    if offer is None:
        return "counter", max(floor, target)
    if below >= 3:                                   # third offer under the floor: the buyer will not reach it
        return "reject", None
    return "counter", max(floor, (ask + offer) // 2 if ask else target)


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
    # 58 of 88 visits got a grade-C unit (earliest expiry first) and were refused at checkout: serve the unit that
    # meets the hours the buyer asked for, grade A/B before C, and only then the one that expires first
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hours)", want)
    need = float(m.group(1)) * 3600 if m else 0
    return min(fit, key=lambda u: ((u["expires_at"] or float("inf")) - time.time() < need, u.get("grade") == "C",
                                   u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines", "t": last message time}

    def held(self):
        """Units promised in negotiations that answered within the last 3 minutes. A buyer that stops answering
        never cancels; without the cutoff its unit stayed promised forever (237 "no unit" visits/day had stock)."""
        return {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"] and time.time() - d["t"] < 180}

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
            unit = find_unit(text, self.held())
            if unit is None:
                print(f"[visit] no unit for {text[:60]!r}", flush=True)
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            print(f"[visit] {text[:60]!r} -> {unit['sku']} grade {unit.get('grade')} cost {unit['cost']}", flush=True)
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "lines": [f"buyer: {text}"], "t": time.time()}
        else:
            deal["lines"].append(f"buyer: {text}"); deal["t"] = time.time()
            m = re.search(r"\d+", text)
            deal["offer"] = int(m.group()) if m else deal["offer"]

        u = deal["unit"]
        hours = (u["expires_at"] - time.time()) / 3600 if u.get("expires_at") else None
        urgent = (hours is not None and hours < 12) or u.get("grade") == "C"
        med, n = median_price(u["sku"])
        target = med or int(u["list_price"] * 0.78)             # 78 % of list: the market's median settle ratio
        reserve = floor_price(u["cost"], u.get("kind"), urgent, target)
        if deal["offer"] is not None and deal["offer"] < reserve:
            deal["below"] = deal.get("below", 0) + 1
        action, price = price_move(deal["ask"], deal["offer"], reserve, target, deal.get("below", 0))
        move = {"price": price}
        if action == "accept":
            price = deal["offer"]
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                receipt["claim"] = u.get("grade")          # honest: the ledger shows claim_honest afterwards
                receipt["cert"] = True
                if u.get("kind") == "goods":
                    receipt["warranty_hours"] = {"A": 6, "B": 6}.get(u.get("grade"), 0)   # agent.md's example policy asks 6 h; B at 3 h was refused
            receipt = sign(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            print(f"[deal] {u['sku']} grade {u.get('grade')} at {price} (cost {u['cost']})", flush=True)
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if action == "reject":
            print(f"[walk] {u['sku']} grade {u.get('grade')}: rejected at offer {deal['offer']} vs floor {reserve}", flush=True)
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        deal["ask"] = int(price)
        reply = (f"{u['name']}: asking {deal['ask']}. Send your offer as a number." if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=f"A small online shop. Ask for an item by name or SKU. A coding harness ({CMD.split()[0]}) negotiates the price.",
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
