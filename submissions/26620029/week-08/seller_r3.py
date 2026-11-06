"""Round 3 seller (ddolcom, from the kit's starter): sells from the stock the kit keeps in $MARKET_DIR/inventory.json,
behind A2A v1.0.

    python seller_r3.py --port 9301

The code decides every price (a concession schedule from the list price down to a floor), so a reply never waits
on the model. The harness only polishes the wording of a reply, and the code's own sentence is used when it is slow.
Every receipt names the unit, claims its true grade and keeps the market's certificate; goods of grade A and B carry
a break warranty and food says how many hours it has left. Food near its date may go below cost (NEAR_EXPIRY).
"""
import argparse
import asyncio
import base64
import json
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

from harness import CMD, ask

NAME = "ddolcom"                             # card and receipt name, the same as the market name (join --name)
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
# ddolcom: food close to its date may go below cost (a small loss beats throwing it away at full cost)
NEAR_EXPIRY = [(0.20, 0.80), (0.45, 0.90)]  # (shelf life left below this share, floor as a share of cost)
# ddolcom: goods break within 6 h of a sale, so 24 h covers every break. 87600 (10 years) failed the market's
# claim_and_warranty_well_formed check: 9 goods deals agreed on 2026-11-03 never settled; 24 h is the most any
# settle in the ledger carried, and a buyer policy asking for up to 24 h still pays
# 2026-11-06: 72 h, as most top shops now settle with. 533 breaks in 5.66 h of ledger all came within 5.97 h, so it costs nothing
WARRANTY_HOURS = 72
# ddolcom: every goods unit carries the warranty at no extra charge. The +20% premium with a 'no warranty' opt-out
# is gone (2026-11-03): no top-8 shop charges for a warranty, and they saw only 1-2 breaks each in Round 3
# no digits in this line: a buyer took the "20" of an earlier "+20%" for our first ask
WARRANTY_LINE = ("Break warranty included at no extra charge: goods can only break in the first hours after a sale, "
                 "and if it breaks the market refunds you in full from our balance.")
# ddolcom: grade C goods carry no warranty (trial from 2026-11-04). Over the first 20 h of Round 3 C broke 27.7% of the
# time in the market (A 2.5%, B 9.3%), which leaves about 2% of list price after refunds; here 13 of 29 C units broke,
# 1,330,855 refunded against 972,322 of margin. 45% of the market's C units sold without a warranty
NO_WARRANTY = {"C"}
NO_WARRANTY_LINE = "Refurbished unit sold as is, without a break warranty."
# ddolcom (strategy 3): prices come from code. ask = floor + (list price - floor) * STEP ** (buyer offers so far)
STEP = 0.6                                  # each buyer offer moves the ask 40% of the way down to the floor
# ddolcom: a buyer that leaves without cancelling kept its unit promised until a restart. On 2026-11-06 after 34 h of
# uptime 10 of 14 high-value SKUs answered "none in stock" with units on the shelf. A negotiation silent this long is
# over; 300 s is above the 180 s a harness call may take between two offers
OPEN_TTL = 300
# ddolcom (trial from 2026-11-06): since the 03:58 restart 7% of opens at 1,000,000 or more became deals, 9% under that
# down to 300,000 and 19% from 100,000; others open near 80% of list. Half of the opens at TRIAL_MIN or more start at
# TRIAL_SHARE of list, half at list, so the log can compare them. "arm" in a [price] line says which
TRIAL_MIN = 100_000
TRIAL_SHARE = 0.85
# ddolcom (2026-11-06 11:40): 85% did not move opens at 1,000,000 or more (low 1 deal of 45, list 2 of 51), where about 85% of
# the market's money is. Market deals there settle near 72% of list, so the low arm of those units now opens closer to that
TRIAL_DEAR_MIN = 1_000_000
TRIAL_DEAR_SHARE = 0.78
# ddolcom (trial from 2026-11-06 22:30): at 1,000,000 or more half of the opens start at 73% (arm low2), half at 78% (arm low). Most
# buyers leave before any offer (G8ec51935: 136 opens, 28 offers) and the market settles near 70%. Judge at 50 opens each by
# conversion x (avg paid - cost); 73% needs about 1.46 times the conversion if the paid price falls as much as the ask
# Ended 2026-11-07 01:23: 73% had 8 deals of 91 opens at 0.687, 78% had 12 of 106 at 0.694, so about 0.83% against 1.14% of list per
# open at a cost of 0.593. The sample is small, but lower asks did not sell more. TRIAL2_RATE 0 keeps every open at 78%
TRIAL2_SHARE = 0.73
TRIAL2_RATE = 0.0
# ddolcom (2026-11-06, the user's instruction): one clearance only, at cost or a little above, to free the cash. These 6
# units had sat 13-40 h and are the ones the forum post 22459 names. New units never join by age: a rule on age alone also
# priced well-selling, cheaply drawn units near cost. Any further clearance is the user's decision after looking at this one
DEAD_UNITS = frozenset({"u-1bd898b1ae6e", "u-5ad60d23f58b", "u-eacff6b41009",
                        "u-ce3724b8836a", "u-c933d766e8aa", "u-aa2c398a88db"})
DEAD_MARKUP = 1.02
# ddolcom (2026-11-06, the user's instruction): this one unit may go below its cost of 1,650,854. Its cost was above the market's
# recent grade A average of 1,603,868 and it had not sold in 14 h. Named units only: any other is the user's decision
BELOW_COST_FLOOR = {"u-aa2c398a88db": 1_600_000}
WORDING_TIMEOUT = 0                         # seconds the harness gets to word a reply; 0 = off (claude -p haiku
                                            # took 8.8 s on 2026-11-03, so the buyer only waited for nothing)
GRADE_MEANS = {"food": {"A": "plenty of shelf life", "B": "normal shelf life", "C": "close to its date"},
               "goods": {"A": "new", "B": "box damaged, item fine", "C": "refurbished"}}

WORDING = """You are the sales agent of an honest online shop. Rewrite the shop's reply below as one or two short,
friendly English sentences. Keep every number and every fact exactly as written; add no new claims or numbers.
Answer with the sentence only.

Reply: {reply}"""


def shelf_left(u):
    """Share of the unit's shelf life still left (1.0 fresh, 0.0 at its date); None for goods or unknown."""
    if not u.get("expires_at"):
        return None
    try:
        hours = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))[u["sku"]]["shelf_hours"]
    except (OSError, ValueError, KeyError, TypeError):
        hours = None
    if not hours:                                   # no catalog: grade C means under 45% left
        return 0.44 if u.get("grade") == "C" else 1.0
    return max(0.0, (u["expires_at"] - time.time()) / (hours * 3600))


def floor_price(u):
    """Lowest price for this unit: its cost, or less for food near its date (NEAR_EXPIRY) or a named unit."""
    if u.get("unit") in BELOW_COST_FLOOR:
        return BELOW_COST_FLOOR[u["unit"]]
    left = shelf_left(u)
    for below, share in NEAR_EXPIRY:
        if left is not None and left < below:
            return int(u["cost"] * share)
    return u["cost"]


def is_dead(u):
    """One of the units in the one clearance (DEAD_UNITS)."""
    return u.get("unit") in DEAD_UNITS


def trial_arm(u):
    """The trial ended 2026-11-06 14:33 and low won: at 1,000,000 or more 9 deals of 71 opens against 3 of 63, about the
    same margin per open and 2.3 times the revenue; under that 45 of 106 against 35 of 119, more margin and revenue."""
    if is_dead(u):
        return "dead"
    if u.get("list_price", 0) >= TRIAL_DEAR_MIN and random.random() < TRIAL2_RATE:
        return "low2"
    return "low" if u.get("list_price", 0) >= TRIAL_MIN else None


def ask_price(u, offers, arm=None):
    """The ask after `offers` buyer offers: the first ask, then STEP of the way to the floor each time."""
    floor = floor_price(u)
    if arm == "dead":
        return int(floor + (floor * DEAD_MARKUP - floor) * STEP ** offers)
    if arm == "low2":
        share = TRIAL2_SHARE
    else:
        share = 1 if arm != "low" else TRIAL_DEAR_SHARE if u["list_price"] >= TRIAL_DEAR_MIN else TRIAL_SHARE
    top = max(u["list_price"] * share, floor)
    return int(floor + (top - floor) * STEP ** offers)


def is_goods(u):
    return bool(u.get("unit")) and u.get("kind") == "goods"


def has_warranty(u):
    return is_goods(u) and u.get("grade") not in NO_WARRANTY


def facts(u):
    """What the buyer can check: the true grade with the certificate, hours left (food) or the warranty (goods)."""
    if not u.get("unit"):
        return ""
    kind = u.get("kind", "goods")
    out = f"Grade {u['grade']} ({GRADE_MEANS.get(kind, {}).get(u['grade'], '')}), market certificate included."
    if kind == "food" and u.get("expires_at"):
        out += f" About {max(0, int((u['expires_at'] - time.time()) / 3600))} hours of shelf life left."
    if is_goods(u):
        out += f" {WARRANTY_LINE if has_warranty(u) else NO_WARRANTY_LINE}"
    return out


async def worded(reply):
    """The harness's wording of the code's reply, or the reply itself when the harness is slow or drops a number."""
    if WORDING_TIMEOUT <= 0:
        return reply
    try:
        text = (await asyncio.to_thread(ask, WORDING.format(reply=reply), WORDING_TIMEOUT)).strip()
    except Exception as e:
        print(f"[wording] {type(e).__name__}, code sentence used", flush=True)
        return reply
    if not text or any(n not in text for n in re.findall(r"\d+", reply)):
        print(f"[wording] dropped a number, code sentence used: {text[:80]}", flush=True)
        return reply
    return text


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
    # ddolcom (2026-11-07): a named SKU's cheapest unit first, not its oldest, so a floor above the market's price does not block the
    # units that can sell: since 22:26 G8095ddd6 had 62 offers and 4 at or above the floor while its grade A at 0.73 of list stood first
    # and units at 0.38 and 0.57 waited. The units of the forum clearance stay first, as the post says. Food still goes by its date
    # Goods of grade C after A and B (02:28-03:23: 6 of 27 grade C goods receipts settled, 70 of 70 A and 21 of 21 B)
    key = ((lambda u: (u["expires_at"] or float("inf"), not is_dead(u), is_goods(u) and u.get("grade") == "C", u["cost"],
                       u["supplied_at"])) if named
           else (lambda u: (u["expires_at"] or float("inf"), u["supplied_at"])))
    fit = named or fit                                   # a SKU the buyer named comes first
    return min(fit, key=key, default=None)


def offer_in(text):
    """The buyer's offer: the first number in the message, thousands separators allowed (52,000 -> 52000).
    A number glued to letters (a SKU such as G00001) is not an offer."""
    m = re.search(r"(?<![A-Za-z\d])\d[\d,]*", text)
    return int(m.group().replace(",", "")) if m else None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offers"}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now = time.time()
        for k in [k for k, d in self.open.items() if now - d["seen"] > OPEN_TTL]:
            del self.open[k]                             # the buyer left without cancelling: its unit is free again
        deal = self.open.get(task.id)
        if deal:
            deal["seen"] = now

        if deal is None:                                 # the buyer names what it wants: open at the list price
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]})
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            arm = trial_arm(unit)
            deal = self.open[task.id] = {"unit": unit, "ask": ask_price(unit, 0, arm), "offers": 0, "seen": now, "arm": arm}
            u = deal["unit"]
            reply = f"{u['name']}: asking {deal['ask']}. {facts(u)} Send your offer as a number.".replace("  ", " ")
            print(f"[price] open {u['sku']} at {deal['ask']} (list {u['list_price']}, floor {floor_price(u)}, arm {arm})", flush=True)
            await up.requires_input(say(await worded(reply)))
            return

        u = deal["unit"]
        offer = offer_in(text)
        if offer is None:
            await up.requires_input(say(f"Counteroffer: {deal['ask']}. Send your offer as a number."))
            return
        deal["offers"] += 1
        deal["ask"] = max(min(ask_price(u, deal["offers"], deal["arm"]), deal["ask"]), floor_price(u))   # an ask never goes up
        ask = deal["ask"]
        print(f"[price] {u['sku']} offer {offer}, ask {ask} (floor {floor_price(u)})", flush=True)
        if offer >= ask:
            print(f"[price] deal {u['sku']} at {offer} (list {u['list_price']}, arm {deal['arm']})", flush=True)
            receipt = {"shop": NAME, "item": u["sku"], "price": offer, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                receipt["claim"] = u["grade"]            # the true grade; the ledger shows claim_honest afterwards
            if has_warranty(u):
                receipt["warranty_hours"] = WARRANTY_HOURS
            receipt = sign(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {offer}"
                                  f"{' with the break warranty' if has_warranty(u) else ''}. Signed receipt attached."))
            del self.open[task.id]
            return
        await up.requires_input(say(f"Counteroffer: {deal['ask']}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="A small online shop with real stock and honest grades. Ask for an item by name or SKU. "
                    "Every receipt claims the unit's true grade and every unit ships with the market's certificate; "
                    "food comes with the hours of shelf life left. Grade A and B non-food items come with a break warranty "
                    "at no extra charge: if one breaks, the market refunds you in full from our balance. Grade C "
                    "(refurbished) items are sold without one. Prices are set in code, so replies are fast."
                    + (f" A coding harness ({CMD.split()[0]}) words the opening reply." if WORDING_TIMEOUT > 0 else ""),
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
