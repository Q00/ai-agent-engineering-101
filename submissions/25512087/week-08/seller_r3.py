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

import tuning

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
    "endgame_h": 24,             # 11/04: in the round's last 24 h the floor drops to cost (no margin) ...
    "endgame_floor": 0.8,        # ... and in its last 6 h to cost x 0.8: stock left at the end is a total loss
    "warranty_hours": 6,         # 11/04 (2): A/B goods at or above warranty_min_list carry a 6 h warranty
    "warranty_min_list": 100_000,
    "endgame_ask_mult": 0.0,     # 11/17 flag (policy.json): in the endgame the ask is at most floor x this; 0 = off
    "value_floor": 0.0,          # 11/05: never sign under this share of the list price (the buyer's value), whatever
                                 # the unit cost us: B/C units cost 25-60 % of list, so a cost-based floor gave them away.
                                 # 0 in code (cost floor only); the live value comes from improve/policy.json (0.65)
}
ROUND_END = 1794927599           # 2026-11-17 23:59:59 KST (the board counts balance, not stock)
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
    left_h = (ROUND_END - now) / 3600
    if 0 < left_h <= 6:
        return max(1, math.ceil(cost * PRICING["endgame_floor"] - 1e-9))
    if 0 < left_h <= PRICING["endgame_h"]:
        return max(1, math.ceil(cost - 1e-9))
    held_days = max(0.0, (now - float(unit.get("supplied_at") or now)) / 86400) if unit.get("kind") != "food" else 0.0
    by_cost = math.ceil(cost * (1 + PRICING["min_margin"] + PRICING["storage_per_day"] * held_days) - 1e-9)
    # 11/05: price on value, not only on cost. A buyer's value is the list price whatever the grade; the market signs
    # at a median 0.71-0.79 of list. Our B units sold at a median 0.65 of list and C at 0.67 (1.25x and 1.77x cost).
    by_value = math.ceil(float(unit.get("list_price") or 0) * PRICING.get("value_floor", 0) - 1e-9)
    return max(by_cost, by_value)


def opening_ask(unit: dict, now: float | None = None) -> int:
    if unit.get("r2"):                                   # before Round 3: the week-07 ask (reserve x 1.05)
        return max(floor_price(unit, now), unit["list_price"])
    floor = floor_price(unit, now)
    ask = math.ceil(unit["list_price"] * PRICING["ask_of_list"] - 1e-9)
    now = time.time() if now is None else now
    mult = PRICING.get("endgame_ask_mult", 0.0)
    if mult > 0 and 0 < (ROUND_END - now) / 3600 <= PRICING["endgame_h"]:
        # 11/17: every endgame deal closed at the floor (466/466) while the ask sat at 1.48x it and 344 buyers left
        # without an offer: open near the floor so the remaining stock sells (it only counts at cost at the end).
        ask = min(ask, math.ceil(floor * mult - 1e-9))
    return max(floor, ask)


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
    if warranty_for(unit):
        bits.append(f"{warranty_for(unit)} h warranty (full refund if it breaks)")
    bits.append("market certificate included")
    return ", ".join(bits)


HOURS_ASKED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)", re.I)
REFUSED_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) \[acp\] checkout refused: (?:\d+ )?.*?unit (u-[0-9a-f]+) is not in stock")
REFUSED_WINDOW_S = 3600          # 11/09 (approved): a unit the kit refused at checkout this often in this window...
REFUSED_MAX = 2                  # ...is not offered again until the window passes (R3-24/25: 120+ refusals, 1 unit)
KIT_LOG = Path(os.environ.get("KIT_LOG", MARKET_DIR.parent / "logs" / "market-kit.log"))
_refused_cache: dict = {"at": 0.0, "units": frozenset()}


def hours_asked(text: str) -> float | None:
    """11/09: the shelf life a buyer says food must have left ("must last 40 h", "at least 24 hours", "48시간")."""
    found = [float(x) for x in HOURS_ASKED.findall(text or "")]
    return max(found) if found else None


def refused_units(now: float | None = None, path: Path | None = None, tail_bytes: int = 3_000_000) -> frozenset:
    """11/09: units the kit's checkout refused ("unit u-... is not in stock for this order": held by an unpaid
    checkout, or not fitting the order) at least REFUSED_MAX times in the last REFUSED_WINDOW_S. Read from the end
    of the kit log (local HH:MM:SS stamps; a stamp later than now is from yesterday). Cached for 30 s."""
    now = time.time() if now is None else now
    if path is None and now - _refused_cache["at"] < 30:
        return _refused_cache["units"]
    p = path or KIT_LOG
    try:
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - tail_bytes))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return frozenset()
    lt = time.localtime(now)
    now_s = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
    counts: dict = {}
    for line in text.splitlines():
        m = REFUSED_LINE.match(line)
        if not m:
            continue
        age = now_s - (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
        if age < 0:
            age += 86400
        if age <= REFUSED_WINDOW_S:
            counts[m.group(4)] = counts.get(m.group(4), 0) + 1
    out = frozenset(u for u, n in counts.items() if n >= REFUSED_MAX)
    if path is None:
        _refused_cache.update(at=now, units=out)
    return out


def find_unit(text: str, inv: dict, taken: set = frozenset(), refused: set = frozenset(),
              now: float | None = None) -> dict | None:
    """The in-stock unit that fits what the buyer asked for (SKU, name or category); a named SKU first, then the
    unit that expires first (oldest first for goods), skipping units promised in another open negotiation.
    11/09 (approved): also skips units the kit keeps refusing at checkout (refused_units), and for food asked to
    last N hours prefers the first-expiring unit with at least N h left (else the one with the most left)."""
    want = text.lower()
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": math.ceil(RESERVE[item] * PRICING["r2_markup"]),
                         "cost": RESERVE[item] / (1 + PRICING["min_margin"]), "unit": None, "expires_at": None,
                         "supplied_at": 0, "r2": True}
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u["unit"] not in taken
             and u["unit"] not in refused]
    fit = [u for u in units if u["sku"].lower() in want or (u.get("name") or "").lower() in want
           or (u.get("category") or "").split(">")[-1].strip().lower() in want]
    pool = [u for u in fit if u["sku"].lower() in want] or fit
    need = hours_asked(text)
    if need and any(u.get("expires_at") for u in pool):
        now = time.time() if now is None else now
        left = lambda u: ((u["expires_at"] - now) / 3600) if u.get("expires_at") else float("inf")
        enough = [u for u in pool if left(u) >= need]
        if not enough:
            return max(pool, key=left)
        pool = enough
    return min(pool, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)), default=None)


FOOD_GRADE_CUTS = (0.8, 0.45)    # the market's food grades: share of shelf life left for A, B (market_kit.py)
CLAIM_MARGIN_S = 900             # a food claim must hold this long after we sign (checkout and settle follow)
_shelf: dict = {}


def shelf_hours(sku: str) -> float | None:
    """A food SKU's full shelf life in hours, from the market's catalog (cached)."""
    if not _shelf:
        try:
            cat = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
            _shelf.update({k: v.get("shelf_hours") for k, v in cat.items() if v.get("shelf_hours")})
        except (OSError, ValueError):
            return None
    return _shelf.get(sku)


def food_grade(left_s: float, shelf_h: float) -> str:
    share = left_s / (shelf_h * 3600)
    return "A" if share >= FOOD_GRADE_CUTS[0] else "B" if share >= FOOD_GRADE_CUTS[1] else "C"


def claim_for(unit: dict, now: float | None = None, shelf_h: float | None = None) -> str | None:
    """11/04: the grade we claim on the receipt, or None when we cannot be sure it is true when the market judges it.
    Goods: the grade is fixed at arrival, so the unit's grade. Food: the grade from shelf life left, but only when the
    grade is the same now and CLAIM_MARGIN_S later (a unit about to cross a cut gets no claim, never a wrong one)."""
    if unit.get("r2") or not unit.get("unit"):
        return None
    if unit.get("kind") != "food":
        g = unit.get("grade")
        return g if g in ("A", "B", "C") else None
    now = time.time() if now is None else now
    shelf_h = shelf_h or shelf_hours(unit.get("sku", ""))
    exp = unit.get("expires_at")
    if not shelf_h or not exp or exp - now <= CLAIM_MARGIN_S:
        return None
    g_now, g_later = food_grade(exp - now, shelf_h), food_grade(exp - now - CLAIM_MARGIN_S, shelf_h)
    return g_now if g_now == g_later else None


def warranty_for(unit: dict) -> int | None:
    """11/04 (2): hours of warranty for this unit, or None. Big-ticket buyers ask for one (top shops give it); a
    break within it refunds the price from our balance, so only A/B goods at or above warranty_min_list get it."""
    h = int(PRICING.get("warranty_hours") or 0)
    if (h <= 0 or unit.get("r2") or not unit.get("unit") or unit.get("kind") == "food"
            or unit.get("grade") not in ("A", "B") or float(unit.get("list_price") or 0) < PRICING["warranty_min_list"]):
        return None
    return h


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
                unit = find_unit(text, read_inventory(), taken, refused_units())
                if unit is None:
                    await up.reject(say("Sorry, we have none of that in stock right now."))
                    return
                PRICING.update(tuning.load("seller"))          # the improvement loop's knobs, no restart needed
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
                    claim = claim_for(unit)                              # 11/04: honest grade claim (or none)
                    if claim:
                        receipt["claim"] = claim
                    warranty = warranty_for(unit)                        # 11/04 (2)
                    if warranty:
                        receipt["warranty_hours"] = warranty
                    self.log("receipt", task=task.id, unit=unit["unit"], sku=unit["sku"], price=int(price),
                             claim=claim, warranty=warranty, kind=unit.get("kind"), list_price=unit.get("list_price"))
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
