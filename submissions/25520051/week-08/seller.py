"""The shop: an A2A server (a2a-sdk 1.2.x, JSON-RPC binding) that sells units from stock and signs its receipts.

    python seller.py --port 9999          (the kit runs it from shop/, with MARKET_DIR=shop/.market)

Round 3: about 5,600 real products, bought into stock by restock.py. The seller finds the unit that fits what the
buyer asked for (a SKU, a product name or a category), and code sets every price from that unit's own numbers:
- reserve (lowest price) from the unit's cost: a margin over cost, less as food runs out (selling near expiry below
  cost loses less than throwing the unit away at full cost), more when a warranty is on (expected refunds)
- opening ask from the list price, moved per category by the shop memory's win-rate loop
- the same shrinking concessions as week 7, never below the reserve, at most six offers
The receipt names the SKU and the unit, claims the unit's true grade, shows the market certificate, and carries the
warranty when one was agreed. The harness writes only the greeting. Every reply ends with the price as its last
number, and the same facts ride along as a data Part {ask, final, offers_left, sku, grade, hours_left, warranty_hours}.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from cryptography.hazmat.primitives import serialization

from harness import ask_json
from market_state import SKU_RE, catalog, food_grade, hours_left, leaf, read_json, stock, write_json

sys.stdout.reconfigure(encoding="utf-8")   # the trade test redirects stdout to a file (cp949 on Windows)

HERE = Path(__file__).parent
# The shop kit's folder (shop/): its key, its .market/ (the kit passes MARKET_DIR), the shop's own records.
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / "shop" / ".market"))
SHOP_DIR = MARKET_DIR.parent
SHOP_NAME = "moss-and-oak"                 # the nickname the kit registers this shop under
KEY_PATH = SHOP_DIR / "shop_key.pem"
BRAND = ("Moss & Oak: every unit sold with the market's certificate, its true grade and its shelf life stated, "
         "every receipt signed.")
PROMISES = ("Honest grade: we claim only the grade the market's certificate shows. Certificate: always attached. "
            "Shelf life: tell us how many hours you need the food to last and we ship a unit that lasts. "
            "Warranty: ask for one on goods (a full refund if it breaks within six hours) for a small premium. "
            "Member price promise: members never start above what they last paid us for the same product.")

# --- pricing knobs (prices.json may override them; tune_prices.py writes it)
MARGIN = {"A": 0.06, "B": 0.10, "C": 0.12}  # reserve = cost * (1 + margin); B and C cost less, so more room
OPEN = 0.92                                 # opening ask as a share of list price (settles run at about 0.70)
WARRANTY_H = 6                              # goods can break up to 6 h after the sale: a warranty covers all of it
WARRANTY_DEFAULT = {"A": True, "B": False, "C": False}   # goods grade A ship with a warranty unless it is unwanted
BREAK_PRIOR = 0.08                          # assumed break rate until shop memory has seen enough goods sales
MAX_OFFERS = 6
STEP = [0.25, 0.35, 0.5, 0.7, 1.0]          # share of the remaining (ask - reserve) given up per counter
PRICES = SHOP_DIR / "prices.json"           # Round 3 knobs; ../prices.json holds the Round 2 lamp/chair tuning


def knobs() -> dict:
    p = read_json(PRICES, {}) or {}
    return {"margin": {**MARGIN, **(p.get("margin") or {})}, "open": p.get("open", OPEN),
            "step": p.get("step") or STEP, "open_by_cat": p.get("open_by_cat") or {}}


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt: dict, key_path: Path = KEY_PATH) -> dict:
    """Add sig_seller: base64 Ed25519 over canon({shop, item, price, task}), price as a whole number."""
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    body = {"shop": receipt["shop"], "item": receipt["item"], "price": int(receipt["price"]), "task": receipt["task"]}
    receipt["price"] = body["price"]
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


WORDS = ["no", "one", "two", "three", "four", "five", "six"]
ACCEPT = re.compile(r"^\W*(yes|ok|okay|deal|accept|accepted|agreed|i accept|i agree|i'?ll take it|sold|좋아요|구매할게요|살게요)\b", re.I)
HELP = re.compile(r"\b(help|support|how does|rules?)\b", re.I)
WANTS_WARRANTY = re.compile(r"(warrant|보증)", re.I)
NO_WARRANTY = re.compile(r"(no warranty|without (a )?warranty|보증 ?(없이|필요 ?없))", re.I)
HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hours?|시간)", re.I)
MEMBER = re.compile(r"\bMOSS-([A-Z]{4})([A-Z]{4})\b")
MEMBER_ASK = 0.95                            # members open lower ...
MEMBER_STEP = [0.4, 0.5, 0.7, 1.0]           # ... and reach the reserve sooner
MEMBERS = SHOP_DIR / "members.json"


# --- member codes: letters only, checked by recomputing (nothing to forge)

def _secret() -> bytes:
    return hashlib.sha256(KEY_PATH.read_bytes()).digest()


def _letters(b: bytes, n: int) -> str:
    return "".join(chr(65 + x % 26) for x in b[:n])


def member_code(task_id: str) -> str:
    nonce = _letters(hashlib.sha256(task_id.encode()).digest(), 4)
    return f"MOSS-{nonce}{_letters(hmac.new(_secret(), nonce.encode(), 'sha256').digest(), 4)}"


def member_of(text: str) -> str | None:
    m = MEMBER.search(text.upper())
    ok = bool(m) and hmac.compare_digest(_letters(hmac.new(_secret(), m.group(1).encode(), "sha256").digest(), 4),
                                         m.group(2))
    return m.group(0) if ok else None


def members() -> dict:
    """{"codes": {code: account}, "accounts": {account: {sku: last price, "buys": n}}}"""
    d = read_json(MEMBERS, {}) or {}
    d.setdefault("codes", {})
    d.setdefault("accounts", {})
    return d


def account(book: dict, code: str | None) -> dict | None:
    return book["accounts"].setdefault(book["codes"].setdefault(code, code), {}) if code else None


# --- shop memory: what each category's buyers do, and what our goods do after the sale.
# Round 3 hides budgets and demand; what the shop observes for itself (and restock reads from the ledger) is its
# only edge. Hard limits never move with it: the reserve over cost, and the member price promise.
SHOP_MEM = SHOP_DIR / "shop_memory.json"
MARKUP_CAP = 0.12                            # bounded live move of the opening ask per category
MARKUP_MIN_N = 6                             # negotiations seen in a category before its opening moves


def load_shop_mem() -> dict:
    d = read_json(SHOP_MEM, {}) or {}
    for k in ("cats", "sold", "members"):
        d.setdefault(k, {})
    return d


_MEM_LOCK = threading.Lock()


def record_deal(cat: str, result: str, price: int | None, offers: list, deal: dict, task: str | None = None):
    with _MEM_LOCK:
        d = load_shop_mem()
        c = d["cats"].setdefault(cat, {"deals": 0, "walk": 0, "fast": 0, "ratio": []})
        if result == "deal":
            c["deals"] += 1
            c["ratio"] = (c["ratio"] + [round(price / deal["list"], 3)])[-40:]     # price / list price
            if len(offers) <= 2:
                c["fast"] += 1
            u = deal["unit"]
            d["sold"][u["unit"]] = {"t": int(time.time()), "sku": u["sku"], "kind": u["kind"], "grade": deal["grade"],
                                    "price": price, "cost": u["cost"], "warranty_hours": deal["warranty"], "task": task}
            if len(d["sold"]) > 3000:
                for k in sorted(d["sold"], key=lambda k: d["sold"][k]["t"])[:500]:
                    del d["sold"][k]
        else:
            c["walk"] += 1
        SHOP_DIR.mkdir(exist_ok=True)
        write_json(SHOP_MEM, d)


def break_rate(grade: str) -> float:
    """Share of our goods of this grade that broke within six hours (restock.py marks them from the ledger)."""
    sold = [s for s in load_shop_mem()["sold"].values() if s["kind"] == "goods" and s["grade"] == grade
            and time.time() - s["t"] > WARRANTY_H * 3600]
    if len(sold) < 10:
        return BREAK_PRIOR * (1.5 if grade == "C" else 1.0)
    return sum(1 for s in sold if s.get("broke")) / len(sold)


def opening_move(cat: str) -> tuple[float, str]:
    """Winning nearly every negotiation in a category means its opening is too low; losing many means too high."""
    c = load_shop_mem()["cats"].get(cat, {})
    n = c.get("deals", 0) + c.get("walk", 0)
    if n < MARKUP_MIN_N:
        return 0.0, ""
    win = c["deals"] / n
    adj = max(-MARKUP_CAP, min(MARKUP_CAP, (win - 0.70) * MARKUP_CAP / 0.30))
    return adj, f"{cat}: win rate {win:.0%} of {n}, opening {adj:+.0%}"


# --- picking the unit

def unit_terms(u: dict, cat: dict) -> tuple[str | None, float | None, float | None]:
    """(grade to claim, hours left, share of shelf life left). A food unit within half a percent of a grade cut
    gets no claim: the grade could change between checkout and settle, and a wrong claim shows on the ledger."""
    if u["kind"] != "food":
        return u.get("grade"), None, None
    g, share = food_grade(u, (cat.get(u["sku"]) or {}).get("shelf_hours"))
    near = share is not None and any(abs(share - x) < 0.005 for x in (0.8, 0.45))
    return (None if near else g), hours_left(u), share


def find_unit(text: str, taken: set, need_hours: float | None) -> tuple[dict | None, list]:
    """(unit, other SKUs in the same category we have). A SKU the buyer named comes first, then a product name,
    then a category. Food: the unit that expires first among those that last the hours the buyer needs (first
    expired, first out, so less is thrown away). Goods: the oldest unit (storage is paid per day)."""
    cat = catalog(MARKET_DIR)
    units = [u for u in stock(MARKET_DIR) if u["unit"] not in taken]
    low = text.lower()
    named = set(SKU_RE.findall(text))
    fit = [u for u in units if u["sku"] in named]
    if not fit:
        fit = [u for u in units if (u.get("name") or "").lower() in low]
    if not fit:
        leaves = [lf for lf in {leaf(u.get("category")) for u in units} if lf and lf.lower() in low]
        if leaves:
            best = max(leaves, key=len)
            fit = [u for u in units if leaf(u.get("category")) == best]
    if not fit:
        return None, []
    if need_hours:
        lasting = [u for u in fit if u["kind"] != "food" or (hours_left(u) or 0) >= need_hours]
        fit = lasting or fit
    u = min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0))
    others = sorted({x["sku"] for x in units if leaf(x.get("category")) == leaf(u.get("category"))} - {u["sku"]})
    return u, others


def reserve_for(u: dict, grade: str | None, share: float | None, warranty: int, k: dict) -> int:
    g = grade or u.get("grade") or "B"
    r = u["cost"] * (1 + k["margin"].get(g, 0.08))
    if u["kind"] == "food" and share is not None:
        if share < 0.15:
            r = u["cost"] * 0.55              # about to be thrown away: anything above half the cost is a rescue
        elif share < 0.30:
            r = u["cost"] * 0.85
    if warranty:
        r += break_rate(g) * u["list_price"] * 0.75      # expected refund: we pay back the price if it breaks
    return int(round(r))


PITCH_SYSTEM = f"""You greet buyers for this online shop. {BRAND}
Write ONE short, warm sentence greeting a buyer who asked for a product, in the language of the product name.
No numbers, no prices, no instructions to the buyer. Reply with JSON: {{"message": "<sentence>"}}"""
_PITCH = {}            # category -> greeting; a harness call took 11 s per first reply in week 7


def pitch(cat: str) -> str:
    if cat not in _PITCH:
        ans = ask_json(PITCH_SYSTEM, f"A buyer asked for a product in the category {cat}.", timeout=20, attempts=1) or {}
        text = re.sub(r"\d+", "", str(ans.get("message") or "")).strip()[:160]
        _PITCH[cat] = text or "Welcome to Moss & Oak; every unit comes with its certificate and its true grade."
    return _PITCH[cat]


def next_ask(deal: dict) -> int:
    steps = MEMBER_STEP if deal.get("member") else deal["step"]
    k = min(deal["offers"] - 1, len(steps) - 1)
    return max(deal["reserve"], round(deal["ask"] - (deal["ask"] - deal["reserve"]) * steps[k]))


def describe(deal: dict) -> str:
    """The unit's facts in words and numbers, said before the price so the price stays the last number."""
    u = deal["unit"]
    parts = [f"{u['name']} ({u['sku']})", f"grade {deal['grade']}" if deal["grade"] else "grade on the certificate"]
    if deal["hours"] is not None:
        parts.append(f"{deal['hours']:.0f} hours of shelf life left")
    if u["kind"] == "goods":
        parts.append("six-hour warranty included (full refund if it breaks)" if deal["warranty"]
                     else "no warranty (ask for one if you want it)")
    return ", ".join(parts) + ", market certificate attached."


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> deal

    def taken(self) -> set:
        return {d["unit"]["unit"] for d in self.open.values()}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        print(f"[seller] task {task.id[:8]} <- {text!r}", flush=True)

        if deal is None:
            return await self.opening(up, task.id, text)

        code = member_of(text)
        if code and not deal["member"]:
            deal["member"], deal["code"] = True, code
            cap = (account(members(), code) or {}).get(deal["unit"]["sku"])
            deal["ask"] = max(deal["reserve"], min(deal["ask"], cap or deal["ask"]))
            text = MEMBER.sub("", text.upper())
        if deal["unit"]["kind"] == "goods" and not deal["warranty"] and WANTS_WARRANTY.search(text) \
                and not NO_WARRANTY.search(text):
            return await self.set_warranty(up, deal, WARRANTY_H, text)
        if deal["unit"]["kind"] == "goods" and deal["warranty"] and NO_WARRANTY.search(text):
            return await self.set_warranty(up, deal, 0, text)
        if HELP.search(text) and not re.search(r"\d", text):              # explain, never punish a question
            left = MAX_OFFERS - deal["offers"]
            await self.reply(up, deal, f"Send a whole number as your offer, or reply 'accept' to buy at the current "
                                       f"price. {WORDS[left].capitalize()} offers left; 'Final price' means we cannot "
                                       f"go lower. {PROMISES} Current price: {deal['ask']}.")
            return
        if ACCEPT.match(text) and not re.search(r"\d", text):          # "accept" buys at the current ask
            return await self.sell(up, task.id, deal, deal["ask"])
        m = re.search(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text))
        if m is None:
            await self.reply(up, deal, f"Send your offer as a number, or reply 'accept'. Current price: {deal['ask']}.")
            return
        offer = int(float(m.group()))
        deal["offers"] += 1
        deal["buyer_offers"].append(offer)
        if offer >= deal["ask"]:                                          # never charge more than our own ask
            return await self.sell(up, task.id, deal, deal["ask"])
        new = next_ask(deal)
        left = MAX_OFFERS - deal["offers"]
        close = (deal["ask"] - deal["reserve"]) * 0.1
        if offer >= deal["reserve"] and (offer >= new - close or left == 0):
            return await self.sell(up, task.id, deal, offer)
        if left == 0:
            record_deal(deal["cat"], "walk", None, deal["buyer_offers"], deal)
            del self.open[task.id]
            await up.reject(up.new_agent_message(parts=[
                new_text_part(f"No deal this time. Our final price was {deal['reserve']}."),
                new_data_part({"ask": deal["reserve"], "final": True, "offers_left": 0})]))
            print(f"[seller] -> rejected after {MAX_OFFERS} offers", flush=True)
            return
        deal["ask"] = new
        await self.reply(up, deal, f"{WORDS[left].capitalize()} offer{'s' if left != 1 else ''} left. "
                         + (f"Final price: {new}." if new == deal["reserve"] else f"Counteroffer: {new}."))

    async def opening(self, up, task_id, text):
        hm = HOURS.search(text)
        need = float(hm.group(1)) if hm else None
        u, others = find_unit(text, self.taken(), need)
        if u is None:
            have = sorted({leaf(x.get("category")) for x in stock(MARKET_DIR)})[:12]
            await up.reject(up.new_agent_message(parts=[new_text_part(
                "Sorry, we have none of that in stock. In stock now: " + (", ".join(have) or "nothing") + ".")]))
            print("[seller] -> nothing in stock for that", flush=True)
            return
        k = knobs()
        cat = catalog(MARKET_DIR)
        grade, hours, share = unit_terms(u, cat)
        code = member_of(text)
        warranty = (WARRANTY_H if u["kind"] == "goods" and (WARRANTY_DEFAULT.get(u.get("grade"))
                    or WANTS_WARRANTY.search(text)) and not NO_WARRANTY.search(text) else 0)
        reserve = reserve_for(u, grade, share, warranty, k)
        lf = leaf(u.get("category"))
        adj, note = opening_move(lf)
        open_share = k["open_by_cat"].get(lf, k["open"]) * (1 + adj)
        if u["kind"] == "food" and share is not None and share < 0.30:
            open_share = min(open_share, 0.75)                # short-dated food: open where it can still sell
        ask = round(u["list_price"] * open_share)
        ask = max(round(ask * (MEMBER_ASK if code else 1)), round(reserve * 1.05))
        cap = (account(members(), code) or {}).get(u["sku"]) if code else None
        if cap is not None:
            ask = max(reserve, min(ask, cap))                 # member price promise
        if note:
            print(f"[seller] memory: {note}", flush=True)
        deal = {"unit": u, "cat": lf, "list": u["list_price"], "reserve": reserve, "ask": ask, "offers": 0,
                "grade": grade, "hours": hours, "share": share, "warranty": warranty, "member": bool(code),
                "code": code, "step": k["step"], "buyer_offers": [], "k": k}
        self.open[task_id] = deal
        print(f"[seller] unit {u['unit']} {u['sku']} {u['kind']} grade {grade} cost {u['cost']} list {u['list_price']}"
              f" -> reserve {reserve}, ask {ask}" + (f", {hours:.1f} h left" if hours is not None else "")
              + (", warranty" if warranty else ""), flush=True)
        hello = await asyncio.to_thread(pitch, lf)
        alt = f" Also in stock in this category: {', '.join(others[:5])}." if others else ""
        if need and u["kind"] == "food" and (hours or 0) < need:
            alt = " Note: this is our longest-lasting unit and it does not last the hours you asked for." + alt
        await self.reply(up, deal, f"{hello} {describe(deal)}{alt} Send your offer as a number, or reply 'accept' "
                                   f"to buy now. Asking: {deal['ask']}.")

    async def set_warranty(self, up, deal, hours, text):
        """Add or drop the warranty: the reserve and the ask move by the expected refund, nothing else changes."""
        k = deal["k"]
        old = deal["reserve"]
        deal["warranty"] = hours
        deal["reserve"] = reserve_for(deal["unit"], deal["grade"], deal["share"], hours, k)
        deal["ask"] = max(deal["reserve"], deal["ask"] + deal["reserve"] - old)
        said = ("Six-hour warranty added: a full refund if it breaks within six hours." if hours
                else "Warranty removed, the price comes down by its cost.")
        offer = re.search(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", HOURS.sub("", text)))
        if offer and int(float(offer.group())) >= deal["ask"]:
            return await self.sell(up, None, deal, deal["ask"])
        await self.reply(up, deal, f"{said} Current price: {deal['ask']}.")

    async def reply(self, up, deal, text):
        left = MAX_OFFERS - deal["offers"]
        data = {"ask": deal["ask"], "final": deal["ask"] == deal["reserve"], "offers_left": left,
                "sku": deal["unit"]["sku"], "grade": deal["grade"], "kind": deal["unit"]["kind"],
                "hours_left": round(deal["hours"], 1) if deal["hours"] is not None else None,
                "warranty_hours": deal["warranty"] or None, "certificate": True}
        print(f"[seller] -> {text!r} {data}", flush=True)
        await up.requires_input(up.new_agent_message(parts=[new_text_part(text), new_data_part(data)]))

    async def sell(self, up, task_id, deal, price):
        task_id = task_id or next(t for t, d in self.open.items() if d is deal)
        u = deal["unit"]
        receipt = {"shop": SHOP_NAME, "item": u["sku"], "price": int(price), "task": task_id, "unit": u["unit"],
                   "cert": True}
        if deal["grade"] in ("A", "B", "C"):
            receipt["claim"] = deal["grade"]
        if deal["warranty"]:
            receipt["warranty_hours"] = deal["warranty"]
        receipt = sign_receipt(receipt)
        book = members()
        new_code = member_code(task_id)
        state = account(book, deal.get("code") or new_code)
        book["codes"][new_code] = book["codes"][deal.get("code") or new_code]   # same member, new code
        state[u["sku"]] = int(price)                                       # member price promise
        state["buys"] = int(state.get("buys", 0)) + 1
        SHOP_DIR.mkdir(exist_ok=True)
        write_json(MEMBERS, book)
        record_deal(deal["cat"], "deal", int(price), deal["buyer_offers"], deal, task_id)
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(up.new_agent_message(parts=[new_text_part(
            f"Deal: {u['name']} ({u['sku']}) at {price}. Signed receipt attached; the checkout ships unit "
            f"{u['unit']} with its market certificate. Your Moss & Oak member code is {new_code}; include it next "
            f"time for member pricing.")]))
        print(f"[seller] -> deal at {price} ({u['sku']} {u['kind']} grade {deal['grade']}, cost {u['cost']}, "
              f"reserve {deal['reserve']}{', member' if deal['member'] else ''})", flush=True)
        del self.open[task_id]

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            record_deal(deal["cat"], "walk", None, deal["buyer_offers"], deal)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description=BRAND + " Food, furniture, bedding and appliances from stock. Ask for a SKU (best), a product "
                    "name or a category; say how many hours you need food to last, or ask for a warranty on goods. "
                    "Receipts: Ed25519 sig_seller over {shop, item, price, task}, with unit, claim, cert and "
                    "warranty_hours. Each reply ends with our current price as its last number; 'Final price' means "
                    "we cannot go lower. Up to six offers. Reply 'accept' to buy at the current price. " + PROMISES,
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for a product (SKU, name or category), then send a price as a number or reply 'accept'. "
                        "The shop answers with a counteroffer (also in a data Part {ask, final, offers_left, sku, "
                        "grade, hours_left, warranty_hours}), a signed receipt artifact, or a rejection after six offers.",
            tags=["shopping", "negotiation", "certificate", "warranty"],
            examples=["I want to buy 종가 포기김치 10kg (G00001); I need it to last 24 hours", "52000", "accept",
                      "warranty please"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def app_from_env():
    return build(int(os.environ["SELLER_PORT"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    if os.environ.get("SELLER_RELOAD") == "1":
        # In the market: a restarted kit gets a new tunnel URL, and buyers holding the old one leave
        # "shop did not answer" reviews (8 of our 10 listed reviews at 20:45). The kit forwards to this
        # port, so reloading the seller in place keeps the public URL; only seller code triggers it.
        os.environ["SELLER_PORT"] = str(args.port)
        uvicorn.run("seller:app_from_env", factory=True, host="127.0.0.1", port=args.port, log_level="warning",
                    app_dir=str(HERE), reload=True, reload_dirs=[str(HERE)], reload_includes=["seller.py", "market_state.py", "harness.py"],
                    reload_excludes=["buyer.py", "market_buyer.py", "a2a_trade.py", "tune_prices.py", "restock.py",
                                     "review.py", "shop_report.py", "*.json", "logs/*", ".market/*", "shop/*"])
    else:
        uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
