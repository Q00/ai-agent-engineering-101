"""ggari-shop, Round 3 seller (week 08): rule-based, sells from the stock the kit keeps in $MARKET_DIR/inventory.json.

    python seller_r3.py --port 9301 [--name ggari-shop]

Based on the kit's starter (scripts/starter_round3/seller.py: A2A plumbing, unit lookup, receipt signing), with the
negotiation done by code instead of an LLM call per message (week-07 Worker rules, re-based on unit cost):
- the opening ask is a share of the list price, never under the unit's floor: cost x (1 + MIN_MARGIN + 1 %/day held)
  (R3 has no floor price; the unit's own cost is ours), except food in its last 2 h (half its cost: stop-loss);
- an offer at or above the ask closes at once; an offer under the ask is countered, always coming down at least 1;
- the same offer twice in a row, or any 3rd offer, at or above the floor price closes;
- below the floor: the floor is shown as the final price, until max_rounds offers;
- the receipt names the unit to ship; the kit's checkout adds the market's certificate (we never hide it).
Before Round 3 (no stock) it sells the week-07 items at their reserves, as the starter does.
Pricing knobs live in PRICING so the restock/evaluation loop can tune them (later phases).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import time
from pathlib import Path

MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}      # Rounds 0-2 have no stock: the week-07 items
PRICING = {
    "ask_of_list": 0.92,     # opening ask = 92 % of the list price ...
    "min_margin": 0.05,      # ... but never under cost x 1.05, which is also the lowest price we sign
    "r2_markup": 1.05,       # before Round 3: week-07 items at reserve x 1.05
    "max_rounds": 6,
    "storage_per_day": 0.01,     # fix 4: storage cost already paid, added to the floor (goods)
    "food_markdown_h": 2.0,      # fix 4: food in its last 2 h ...
    "food_markdown_share": 0.5,  # ... may go down to half its cost (thrown away it returns nothing)
}
PRICE = re.compile(r"(?<![\w.])(\d{1,9})(?![\w.])")


# ---------------------------------------------------------------- pure rules (unit-tested)
def floor_price(unit: dict, now: float | None = None) -> int:
    """The lowest price we sign for this unit (fix 4, 11/02): its cost plus the minimum margin plus what it has
    cost us to keep it (goods: 1 % of cost per day in storage), so a unit that waited a week is not sold at a loss.
    Food close to its date is the exception (stop-loss): food past its date is thrown away at full cost, so in its
    last FOOD_MARKDOWN_H hours any price above FOOD_MARKDOWN_SHARE of cost is better than keeping it."""
    now = time.time() if now is None else now
    cost = float(unit["cost"])
    if unit.get("r2"):
        return math.ceil(cost * (1 + PRICING["min_margin"]) - 1e-9)
    exp = unit.get("expires_at")
    if exp and 0 < exp - now <= PRICING["food_markdown_h"] * 3600:
        return max(1, math.ceil(cost * PRICING["food_markdown_share"] - 1e-9))
    held_days = max(0.0, (now - float(unit.get("supplied_at") or now)) / 86400) if unit.get("kind") != "food" else 0.0
    return math.ceil(cost * (1 + PRICING["min_margin"] + PRICING["storage_per_day"] * held_days) - 1e-9)


def opening_ask(unit: dict, now: float | None = None) -> int:
    if unit.get("r2"):                                   # before Round 3: the week-07 ask (reserve x 1.05)
        return max(floor_price(unit, now), unit["list_price"])
    return max(floor_price(unit, now), math.ceil(unit["list_price"] * PRICING["ask_of_list"] - 1e-9))


def parse_offer(text: str) -> int | None:
    """The last plain whole number in the buyer's message (SKUs like G00001 are not prices)."""
    found = PRICE.findall(text.replace(",", ""))
    return int(found[-1]) if found else None


def decide(deal: dict, offer: int | None, says_ok: bool) -> tuple[str, int]:
    """deal: {"floor", "ask", "offers": [...], "floor_shown": bool}. Returns (action, price):
    ("accept", price) signs at price; ("counter", price) asks again; ("end", floor) ends the talk."""
    floor, ask = deal["floor"], deal["ask"]
    if offer is None:
        return ("accept", ask) if says_ok else ("counter", ask)
    offers = deal["offers"] + [offer]
    if offer >= ask:
        return "accept", offer                                   # sign exactly the buyer's number
    if offer >= floor:
        repeated = len(offers) >= 2 and offers[-1] == offers[-2]
        if repeated or len(offers) >= 3:
            return "accept", offer
        return "counter", max(floor, min(ask - 1, max(offer + 1, (offer + ask) // 2)))
    # 11/02: no early "end" when the offer does not rise after the floor was shown. Many buyers repeat their
    # offer once on purpose (as ours does, B3); ending lost them, and keeping the floor on the table costs nothing.
    if len(offers) >= PRICING["max_rounds"]:
        return "end", floor
    return "counter", floor                                      # below the floor: the floor, as final


def unit_line(unit: dict, now: float | None = None) -> str:
    """What the buyer is told about the unit: name, SKU, grade, shelf life left (food), certificate."""
    now = time.time() if now is None else now
    bits = [f"{unit['name']} ({unit['sku']})"]
    if unit.get("grade"):
        bits.append(f"grade {unit['grade']}")
    if unit.get("expires_at"):
        bits.append(f"{max(0.0, (unit['expires_at'] - now) / 3600):.1f} h shelf life left")
    bits.append("market certificate included")
    return ", ".join(bits)


def find_unit(text: str, inv: dict, taken: set = frozenset()) -> dict | None:
    """The in-stock unit that fits what the buyer asked for (SKU, name or category); a named SKU first, then the
    unit that expires first (oldest first for goods), skipping units promised in another open negotiation."""
    want = text.lower()
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": math.ceil(RESERVE[item] * PRICING["r2_markup"]),
                         "cost": RESERVE[item] / (1 + PRICING["min_margin"]), "unit": None, "expires_at": None,
                         "supplied_at": 0, "r2": True}
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u["unit"] not in taken]
    fit = [u for u in units if u["sku"].lower() in want or (u.get("name") or "").lower() in want
           or (u.get("category") or "").split(">")[-1].strip().lower() in want]
    named = [u for u in fit if u["sku"].lower() in want]
    return min(named or fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)),
               default=None)


def read_inventory() -> dict:
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- A2A server (the starter's plumbing)
def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt: dict) -> dict:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


ACCEPT_WORDS = re.compile(r"\b(ok|okay|deal|accept|agreed|yes|take it)\b", re.I)


def build(port: int, name: str):
    import uvicorn  # noqa: F401  (imported here so the rules above test without the server stack)
    from starlette.applications import Starlette
    from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

    class Seller(AgentExecutor):
        def __init__(self):
            self.open: dict[str, dict] = {}

        def log(self, event: str, **kw):
            print(json.dumps({"event": event, **kw}, ensure_ascii=True), flush=True)

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
                taken = {d["unit"]["unit"] for d in self.open.values() if d["unit"].get("unit")}
                unit = find_unit(text, read_inventory(), taken)
                if unit is None:
                    await up.reject(say("Sorry, we have none of that in stock right now."))
                    return
                floor = floor_price(unit)
                ask = opening_ask(unit)
                deal = self.open[task.id] = {"unit": unit, "floor": floor, "ask": ask, "offers": [],
                                             "floor_shown": False, "seen": time.time()}
                self.log("opening", task=task.id, sku=unit["sku"], unit=unit.get("unit"), cost=unit.get("cost"),
                         ask=ask, floor=floor)
                offer = parse_offer(text.replace(unit["sku"], ""))      # "I want X (G00001) for 52000"
                if offer is None:
                    line = f"{unit_line(unit)}. Asking {ask}. Send your offer as a number." if not unit.get("r2") \
                        else f"{unit['name']}: asking {ask}. Send your offer as a number."
                    await up.requires_input(say(line))
                    return
            else:
                offer = parse_offer(text)
            action, price = decide(deal, offer, bool(ACCEPT_WORDS.search(text)))
            if offer is not None:
                deal["offers"].append(offer)
            self.log("decision", task=task.id, offer=offer, action=action, price=price)
            unit = deal["unit"]
            if action == "accept":
                receipt = {"shop": name, "item": unit["sku"], "price": int(price), "task": task.id}
                if unit.get("unit"):
                    receipt["unit"] = unit["unit"]                       # the unit the checkout ships
                await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
                await up.complete(say(f"Deal: {unit['name']} at {int(price)}. Signed receipt attached."))
                self.open.pop(task.id, None)
                return
            if action == "end":
                await up.reject(say(f"Sorry, we cannot go below {price} for this one."))
                self.open.pop(task.id, None)
                return
            if price <= deal["floor"]:
                deal["floor_shown"] = True
            deal["ask"] = price
            await up.requires_input(say(f"Our price: {price}." if price > deal["floor"]
                                        else f"Our lowest price for this unit: {price}. Reply ok to take it."))

        async def cancel(self, context, event_queue):
            task = context.current_task
            self.open.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()

    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=name,
        description=("Food, furniture, bedding and appliances from our own stock. Every unit ships with the market's "
                     "certificate of its grade and shelf life. Ask for an item by name or SKU; our price is the last "
                     "number in each reply. Send a number to offer, or say ok to take it."),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Buy from stock, fast",
                           description="Ask for an item, then send a price. Answers in milliseconds by rule-based code.",
                           tags=["shopping", "certificate", "fast"], examples=["I want to buy 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    ap.add_argument("--name", default="ggari-shop")
    a = ap.parse_args()
    uvicorn.run(build(a.port, a.name), host="127.0.0.1", port=a.port, log_level="warning")
