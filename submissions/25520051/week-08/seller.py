"""The shop: an A2A server (a2a-sdk 1.2.x, JSON-RPC binding) that signs its receipts.

    python seller.py --port 9999

Week 6 let the harness pick every price; in the market that cost seconds per reply
and sometimes left a stray number in the text. Week 7: code sets the prices
(opening ask, shrinking concessions, never below the reserve, at most six offers),
the harness writes only the greeting, and every reply is easy for any buyer model to read.
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
from datetime import datetime
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

sys.stdout.reconfigure(encoding="utf-8")   # the trade test redirects stdout to a file (cp949 on Windows)

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
SHOP_NAME = "moss-and-oak"                 # the nickname the kit registers this shop under
KEY_PATH = Path(__file__).with_name("shop_key.pem")
BRAND = ("Moss & Oak: simple, well-made furniture with only the parts you need, at fair prices. "
         "Eco-friendly materials, an inclusive workshop, every receipt signed.")
# Promises the code keeps (and only those):
# - receipt promise: the receipt always shows the price we agreed, signed (sell() is the only way to a receipt)
# - member price promise: a member never opens above the price they last paid us for that item
# - make-good: a member who says 'support' gets our final price at once, one time per member code
PROMISES = ("Receipt promise: the receipt always shows the agreed price, signed. Member price promise: members "
            "never start above what they last paid us for the same item. Make-good: if a negotiation with us broke "
            "off, say 'support' with your member code for our final price at once (once per code). "
            "Points: each purchase earns points worth one to five percent of the price (more on pricier items), "
            "and every fifth purchase draws a coupon for ten, twenty-five, fifty or one hundred percent off. "
            "Points and coupons lower the next asking price, never below our floor price. First visit "
            "(also if you usually shop elsewhere): a lower first ask and double points. Weekly pop-up: one "
            "item in a celebrity edition, five percent off (the celebrities are characters of this market). Ten "
            "purchases in one pop-up season enter a draw for a VIP pass: every purchase starts at our floor price "
            "for the rest of that season.")
POINT_RATE = {"lamp": 0.01, "chair": 0.03, "bookshelf": 0.05}
# First-time buyers, including those who shop elsewhere: a smaller first ask and double points. Another shop's
# member code cannot be checked, so the offer is for everyone without one of ours.
WELCOME_ASK = 0.97
# Weekly pop-ups: a themed edition of one of our three items, five percent off, with our own characters
# (no real celebrities or licensed characters: that would advertise a collaboration that never happened).
# The celebrities are fictional, made up for this market (a real person's name would claim an endorsement that
# never happened). Titles are spelled out: the price stays the only number in a reply.
POPUPS = [
    {"name": "Autumn Fern pop-up", "item": "lamp", "edition": "Luna Vale Moonlight lamp",
     "collab": "Luna Vale, the number one pop star among teens"},
    {"name": "Reading Nook week", "item": "bookshelf", "edition": "Marco Brio Kitchen Nook shelf",
     "collab": "Marco Brio, the most-watched TV chef of the season"},
    {"name": "Work-from-home week", "item": "chair", "edition": "Kai Storm Pro chair",
     "collab": "Kai Storm, three-time world champion of esports"},
    {"name": "Winter Moss pop-up", "item": "lamp", "edition": "Aria Bloom Glow lamp",
     "collab": "Aria Bloom, best-selling indie singer of the year"},
]
SEASON_GOAL, VIP_ODDS = 10, 0.5      # ten purchases in one pop-up season enter a draw for a VIP pass
POPUP_ASK = 0.95
EVENT = Path(__file__).with_name("event.json")     # {"name": ..., "until": "YYYY-MM-DDTHH:MM:SS"}, read per negotiation


def event() -> dict | None:
    """A running first-visit event, or None. A free rental would be a receipt at zero, below the floor, so the
    strongest offer the rules allow is the floor price itself, for a limited time."""
    try:
        e = json.loads(EVENT.read_text(encoding="utf-8"))
        return e if datetime.now() < datetime.fromisoformat(e["until"]) else None
    except (OSError, ValueError, KeyError):
        return None


def popup() -> dict:
    return POPUPS[(datetime.now().isocalendar()[1] - 43) % len(POPUPS)]    # week 43 (Oct 19, 2026) opens with Autumn Fern
COUPONS, COUPON_ODDS = [10, 25, 50, 100], [60, 25, 10, 5]
PCT_WORDS = {10: "ten", 25: "twenty-five", 50: "fifty", 100: "one hundred"}
MEMBERS = Path(__file__).with_name(".market") / "members.json"


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt: dict, key_path: Path = KEY_PATH) -> dict:
    """Add sig_seller: base64 Ed25519 over canon({shop, item, price, task}), price as a whole number."""
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    body = {"shop": receipt["shop"], "item": receipt["item"], "price": int(receipt["price"]), "task": receipt["task"]}
    receipt["price"] = body["price"]
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt

# Opening asks; the reserves stay. Challenge 1: tune_prices.py rewrites prices.json from our trades and
# reviews, and every new negotiation reads it, so a change needs no restart.
ASK = {"lamp": 130, "chair": 234, "bookshelf": 338}
MAX_OFFERS = 6                               # numeric offers per negotiation; buyers are told how many remain
STEP = [0.25, 0.35, 0.5, 0.7, 1.0]           # share of the remaining (ask - reserve) given up per counter
PRICES = Path(__file__).with_name("prices.json")
LINE = {"lamp": "Fern desk lamp", "chair": "Oak-ply chair", "bookshelf": "Moss modular bookshelf"}


def prices() -> tuple[dict, list]:
    try:
        p = json.loads(PRICES.read_text(encoding="utf-8"))
        return {**ASK, **p.get("ask", {})}, p.get("step") or STEP
    except (OSError, ValueError):
        return ASK, STEP
WORDS = ["no", "one", "two", "three", "four", "five", "six"]
ACCEPT = re.compile(r"^\W*(yes|ok|okay|deal|accept|accepted|agreed|i accept|i agree|i'?ll take it|sold)\b", re.I)
HELP = re.compile(r"\b(help|support|how does|rules?)\b", re.I)
MEMBER = re.compile(r"\bMOSS-([A-Z]{4})([A-Z]{4})\b")
MEMBER_ASK = 0.92                            # members open lower ...
MEMBER_STEP = [0.4, 0.5, 0.7, 1.0]           # ... and reach the reserve sooner


def _secret() -> bytes:
    return hashlib.sha256(KEY_PATH.read_bytes()).digest()


def _letters(b: bytes, n: int) -> str:
    return "".join(chr(65 + x % 26) for x in b[:n])


def member_code(task_id: str) -> str:
    """Letters only (the price stays the only number in a reply); checked by recomputing, nothing stored."""
    nonce = _letters(hashlib.sha256(task_id.encode()).digest(), 4)
    return f"MOSS-{nonce}{_letters(hmac.new(_secret(), nonce.encode(), 'sha256').digest(), 4)}"


def member_of(text: str) -> str | None:
    """The valid member code in the text, if any."""
    m = MEMBER.search(text.upper())
    ok = bool(m) and hmac.compare_digest(_letters(hmac.new(_secret(), m.group(1).encode(), "sha256").digest(), 4),
                                         m.group(2))
    return m.group(0) if ok else None


def is_member(text: str) -> bool:
    return member_of(text) is not None


def members() -> dict:
    """{"codes": {code: account}, "accounts": {account: state}}; every code a member got points to one account,
    so points and coupons follow the member and cannot be spent twice through an old code."""
    try:
        d = json.loads(MEMBERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if "codes" not in d:                                   # first format: {code: state}
        d = {"codes": {c: c for c in d}, "accounts": dict(d)}
    return d


def account(book: dict, code: str | None) -> dict | None:
    return book["accounts"].setdefault(book["codes"].setdefault(code, code), {}) if code else None


def save_members(d: dict):
    MEMBERS.parent.mkdir(exist_ok=True)
    tmp = MEMBERS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(MEMBERS)


def member_cap(code: str | None, item: str) -> int | None:
    """Member price promise: the last price this member paid for the item."""
    state = account(members(), code)
    return state.get(item) if state else None


def perks(code: str | None, ask: int, reserve: int) -> tuple[int, str, int, int | None]:
    """(new ask, words for the buyer, points used, coupon used). Never below the reserve."""
    state = account(members(), code)
    if not state:
        return ask, "", 0, None
    pts, cpn = int(state.get("points", 0)), state.get("coupon")
    after = ask - round(ask * cpn / 100) if cpn else ask
    used = max(0, min(pts, after - reserve))
    new = max(reserve, after - used)
    if new == ask:
        return ask, "", 0, None
    said = " and ".join(x for x in ["your points" if used else "",
                                     f"your {PCT_WORDS[cpn]} percent coupon (down to our floor)" if cpn else ""] if x)
    return new, f" Member perks applied: {said}.", used, cpn

# --- shop memory: learn each buyer's negotiation behaviour and price to it. Round 3 takes the budget
# off the ledger, so what the shop observes for itself is its only edge. Kept honest by two hard limits
# that never move: the reserve floor, and the member price promise (we never open a member above the
# price they last paid us). Buyers weigh their own past trades with us at 76% and a matching signed
# receipt at 99% (lecture data), so the memory's job is to price to a known buyer, never to trick one.
SHOP_MEM = Path(__file__).with_name("shop_memory.json")
GRINDER_STEP = [0.4, 0.6, 0.8, 1.0]      # reach the reserve sooner for hard hagglers: speed is a star (>=4 offers -> lower)
MARKUP_CAP = 0.08                        # bounded live move of the opening ask for buyers without a member code
MARKUP_MIN_N = 8                         # negotiations seen for an item before its opening moves at all


def account_id(code: str | None) -> str | None:
    """The stable account a member code points to (codes rotate each purchase; the account does not)."""
    return members()["codes"].get(code) if code else None


def load_shop_mem() -> dict:
    try:
        d = json.loads(SHOP_MEM.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("members", {})
    d.setdefault("items", {})
    return d


def save_shop_mem(d: dict):
    tmp = SHOP_MEM.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SHOP_MEM)


def ceiling_estimate(offers: list) -> int | None:
    """From a buyer's rising offers, extrapolate where they would stop: the mirror of the buyer's
    floor estimate. If the increments shrink by a steady ratio r, the remaining climb sums to
    inc*r/(1-r). Recorded only; the member price promise, not this guess, caps what we ask."""
    inc = [b - a for a, b in zip(offers, offers[1:]) if b - a > 0]
    if len(inc) >= 2 and 0 < inc[-1] < inc[-2]:
        r = inc[-1] / inc[-2]
        return int(offers[-1] + inc[-1] * r / (1 - r))
    return offers[-1] if offers else None


def record_deal(acct: str | None, item: str, result: str, price: int | None, buyer_offers: list, ask: int):
    """One negotiation's outcome. Members are keyed by account (behaviour follows the buyer across
    visits); everyone is counted into the item aggregate (anonymous buyers share no identity)."""
    d = load_shop_mem()
    rounds = len(buyer_offers)
    if acct:
        m = d["members"].setdefault(acct, {}).setdefault(item, {"accepts": [], "rounds": [], "walks": 0})
        if result == "deal":
            m["accepts"] = (m["accepts"] + [price])[-10:]
            m["rounds"] = (m["rounds"] + [rounds])[-10:]
        else:
            m["walks"] = m.get("walks", 0) + 1
        if buyer_offers:
            m["ceiling_est"] = ceiling_estimate(buyer_offers)
    it = d["items"].setdefault(item, {"deals": 0, "near_ask": 0, "walk": 0, "cleared": []})
    if result == "deal":
        it["deals"] += 1
        it["cleared"] = (it["cleared"] + [price])[-50:]
        if rounds <= 2 and price >= ask * 0.95:          # won fast and near the ask: clear sign of room
            it["near_ask"] += 1
    else:
        it["walk"] += 1
    save_shop_mem(d)


def memory_opening(acct: str | None, item: str, ask: int, reserve: int, step: list, member: bool) -> tuple[int, list, str]:
    """Adjust the opening ask and concession schedule from memory.

    Member (ask already <= their last price, so this only moves within the promise):
      accepts fast (avg <= 2 offers) -> hold the opening; grinds (avg >= 4) -> open a touch lower and
      concede faster, trading a little margin for the speed star and avoiding a 'held firm' review.
    No member code: move the item's opening within +/-MARKUP_CAP by the win rate (deals vs walks), a
    self-correcting loop -- the Round-3 lever, now that no one can read budgets to undercut toward.
    Winning nearly every negotiation means the opening is too low; losing too many means it is too high."""
    d = load_shop_mem()
    if member and acct and item in d["members"].get(acct, {}):
        rounds = d["members"][acct][item].get("rounds") or []
        if rounds:
            avg = sum(rounds) / len(rounds)
            if avg >= 4:
                ask = max(reserve, round(ask * 0.96))
                return max(ask, round(reserve * 1.08)), GRINDER_STEP, f"member grinds (avg {avg:.1f} offers): open {ask}, concede faster"
            if avg <= 2:
                return max(ask, round(reserve * 1.08)), step, f"member accepts fast (avg {avg:.1f} offers): hold the opening {ask}"
    if not member:
        it = d["items"].get(item, {})
        n = it.get("deals", 0) + it.get("walk", 0)
        if n >= MARKUP_MIN_N:
            win = it["deals"] / n
            adj = max(-MARKUP_CAP, min(MARKUP_CAP, (win - 0.70) * MARKUP_CAP / 0.30))   # win 100%->+cap, 70%->0, 40%->-cap
            ask = round(ask * (1 + adj))
            return max(ask, round(reserve * 1.08)), step, (f"win rate {win:.0%} of {n} "
                    f"(near-ask {it.get('near_ask', 0)}): opening {adj:+.0%} -> {ask}")
    return max(ask, round(reserve * 1.08)), step, ""


# Every reply is written for any buyer model, large or small:
# - the current price is the only digit in the text, and it comes last ("Counteroffer: 115.")
# - "Final price" means it will not go lower; the offers left are spelled out, never digits
# - the same facts ride along as a data Part {ask, final, offers_left} for buyers that read data
PITCH_SYSTEM = f"""You greet buyers for this furniture shop. {BRAND}
Write ONE short, warm sentence greeting a buyer who asked for an item, mentioning one of those facts.
No numbers, no prices, no instructions to the buyer. Reply with JSON: {{"message": "<sentence>"}}"""


_PITCH = {}            # (item, member) -> greeting; the harness took 11 s per first reply (20:53, 20:57)


def pitch(item: str, member: bool) -> str:
    if (item, member) not in _PITCH:
        _PITCH[(item, member)] = _pitch(item, member)
    return _PITCH[(item, member)]


def _pitch(item: str, member: bool) -> str:
    who = "a returning member" if member else "a new buyer"
    ans = ask_json(PITCH_SYSTEM, f"{who} asked for a {item}; this season's model is the {LINE[item]}.",
                   timeout=20, attempts=1) or {}
    text = re.sub(r"\d+", "", str(ans.get("message") or "")).strip()   # the ask must stay the only number
    text = text[:160] or f"Welcome to Moss & Oak; our {item} is simple, sturdy and made from eco-friendly materials."
    return text + (" Welcome back: member pricing applies." if member else "")


def next_ask(deal: dict) -> int:
    steps = MEMBER_STEP if deal.get("member") else deal["step"]
    k = min(deal["offers"] - 1, len(steps) - 1)
    return max(deal["reserve"], round(deal["ask"] - (deal["ask"] - deal["reserve"]) * steps[k]))


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "offers"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        print(f"[seller] task {task.id[:8]} <- {text!r}", flush=True)

        if deal is None:  # first message: which item?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(up.new_agent_message(parts=[new_text_part(
                    "We sell lamps, chairs and bookshelves. Ask for one of those.")]))
                return
            code = member_of(text)
            member = code is not None
            asks, step = prices()
            acct = account_id(code)
            if member:
                ask = round(asks[item] * MEMBER_ASK)
                cap = member_cap(code, item)
                if cap is not None:
                    ask = min(ask, cap)                               # member price promise
            else:
                ask = asks[item]
            ask, step, mem_note = memory_opening(acct, item, ask, RESERVE[item], step, member)
            if mem_note:
                print(f"[seller] memory: {mem_note}", flush=True)
            ask, perk_text, pts_used, cpn_used = perks(code, max(ask, RESERVE[item]), RESERVE[item])
            pop = popup()
            ev = None if member else event()
            if ev:
                ask = RESERVE[item]
                perk_text += (f" {ev['name']} for first-time buyers, for a limited time: our floor price right "
                              f"away, and double points on this purchase.")
            elif not member:
                ask = round(ask * WELCOME_ASK)
                perk_text += " First visit: welcome offer applied, and double points on this purchase."
            if pop["item"] == item:
                ask = round(ask * POPUP_ASK)
                perk_text += (f" This week's {pop['name']}: the {pop['edition']}, in collaboration with "
                              f"{pop['collab']}, is on special.")
            state = account(members(), code)
            if state and state.get("vip") == pop["name"]:                # VIP pass: the floor price all season
                ask = RESERVE[item]
                perk_text += " VIP pass holder: you start at our floor price this season."
            deal = {"item": item, "reserve": RESERVE[item], "ask": max(ask, RESERVE[item]), "offers": 0,
                    "member": member, "code": code, "list": asks[item], "step": step,
                    "pts_used": pts_used, "cpn_used": cpn_used, "first": not member, "buyer_offers": []}
            self.open[task.id] = deal
            hello = await asyncio.to_thread(pitch, item, member)
            if deal["ask"] == deal["reserve"]:
                # Opening at the floor read as "held firm on every offer" in two 3-star reviews (21:4x), so say
                # plainly that this is already the lowest price and haggling cannot lower it.
                await self.reply(up, deal, f"{hello}{perk_text} This is already our lowest price, so no haggling "
                                           f"is needed: reply 'accept' to buy now. Final price: {deal['ask']}.")
                return
            await self.reply(up, deal, f"{hello}{perk_text} Send your offer as a number, or reply 'accept' to "
                                       f"buy now. {item.capitalize()}: asking {deal['ask']}.")
            return

        if not deal["member"] and is_member(text):                       # a code given after the opening
            deal["member"], deal["code"] = True, member_of(text)
            cap = member_cap(deal["code"], deal["item"]) or deal["ask"]
            deal["ask"] = max(deal["reserve"], min(deal["ask"], round(deal["list"] * MEMBER_ASK), cap))
            if not re.search(r"\d", MEMBER.sub("", text.upper())):
                await self.reply(up, deal, f"Welcome back, member pricing applies. Current price: {deal['ask']}.")
                return
            text = MEMBER.sub("", text.upper())
        if deal["code"] and re.search(r"\bsupport\b", text, re.I) and not re.search(r"\d", MEMBER.sub("", text.upper())):
            book = members()
            state = account(book, deal["code"])
            if not state.get("_make_good"):                               # make-good: final price at once, once
                state["_make_good"] = deal["item"]
                save_members(book)
                deal["ask"] = deal["reserve"]
                await self.reply(up, deal, f"Sorry for the trouble; here is our make-good. Final price: {deal['ask']}.")
                return
        if HELP.search(text) and not re.search(r"\d", text):              # A/S: explain, never punish a question
            left = MAX_OFFERS - deal["offers"]
            await self.reply(up, deal, f"Send a whole number as your offer, or reply 'accept' to buy at the current "
                                       f"price. {WORDS[left].capitalize()} offers left; 'Final price' means we cannot "
                                       f"go lower. Current price: {deal['ask']}.")
            return
        if ACCEPT.match(text) and not re.search(r"\d", text):          # "accept" buys at the current ask
            return await self.sell(up, task.id, deal, deal["ask"])
        m = re.search(r"\d+(?:\.\d+)?", text)
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
            record_deal(account_id(deal.get("code")), deal["item"], "walk", None,
                        deal.get("buyer_offers", []), deal["list"])
            del self.open[task.id]
            await up.reject(up.new_agent_message(parts=[
                new_text_part(f"No deal this time. Our final price was {deal['reserve']}."),
                new_data_part({"ask": deal["reserve"], "final": True, "offers_left": 0})]))
            print(f"[seller] -> rejected after {MAX_OFFERS} offers", flush=True)
            return
        deal["ask"] = new
        await self.reply(up, deal, f"{WORDS[left].capitalize()} offer{'s' if left != 1 else ''} left. "
                         + (f"Final price: {new}." if new == deal["reserve"] else f"Counteroffer: {new}."))

    async def reply(self, up, deal, text):
        left = MAX_OFFERS - deal["offers"]
        data = {"ask": deal["ask"], "final": deal["ask"] == deal["reserve"], "offers_left": left}
        print(f"[seller] -> {text!r} {data}", flush=True)
        await up.requires_input(up.new_agent_message(parts=[new_text_part(text), new_data_part(data)]))

    async def sell(self, up, task_id, deal, price):
        receipt = sign_receipt({"shop": SHOP_NAME, "item": deal["item"], "price": price, "task": task_id})
        book = members()
        new_code = member_code(task_id)
        state = account(book, deal.get("code") or new_code)
        book["codes"][new_code] = book["codes"][deal.get("code") or new_code]   # same member, new code
        state[deal["item"]] = price                                       # member price promise
        state["points"] = int(state.get("points", 0)) - deal.get("pts_used", 0) + round(price * POINT_RATE[deal["item"]]) * (2 if deal.get("first") else 1)
        if deal.get("cpn_used"):
            state.pop("coupon", None)
        state["buys"] = int(state.get("buys", 0)) + 1
        drew = None
        if state["buys"] % 5 == 0 and not state.get("coupon"):
            drew = state["coupon"] = random.choices(COUPONS, COUPON_ODDS)[0]
        season = popup()["name"]
        if state.get("season") != season:
            state["season"], state["season_buys"] = season, 0
        state["season_buys"] += 1
        vip = None
        if state["season_buys"] == SEASON_GOAL and state.get("vip") != season:
            vip = random.random() < VIP_ODDS
            if vip:
                state["vip"] = season
        save_members(book)
        record_deal(book["codes"][deal.get("code") or new_code], deal["item"], "deal", price,
                    deal.get("buyer_offers", []), deal["list"])
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(up.new_agent_message(parts=[new_text_part(
            f"Deal: {deal['item']} at {price}. Signed receipt attached. Your Moss & Oak member code is "
            f"{new_code}; include it when you ask next time for member pricing and your points."
            + (f" Fifth purchase: you drew a {PCT_WORDS[drew]} percent coupon for next time." if drew else "")
            + (" Tenth purchase this season: you won the VIP pass, so every purchase starts at our floor price "
               "for the rest of the season." if vip else "")
            + (" Tenth purchase this season: you entered the VIP pass draw; not this time, thank you for "
               "shopping with us." if vip is False else ""))]))
        print(f"[seller] -> deal at {price} ({deal['item']}, opened {deal['list']}"
              f"{', member' if deal['member'] else ''})", flush=True)
        del self.open[task_id]

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description=BRAND + " Desk lamps, chairs and bookshelves. Receipts: Ed25519 sig_seller over "
                    "{shop, item, price, task}. Each reply ends with our current price as its only number; "
                    "'Final price' means we cannot go lower. Up to six offers, and we say how many remain. "
                    "Reply 'accept' to buy at the current price, 'help' for the rules. Every buyer gets a member "
                    "code with the receipt; members get lower prices next time. " + PROMISES,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item, then send a price as a number or reply 'accept'. The shop answers with "
                        "a counteroffer (the only number in its message, also in a data Part {ask, final, "
                        "offers_left}), a signed receipt artifact, or a rejection after six offers.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90", "accept"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def app_from_env():
    # Warm the greetings in the background so no buyer waits on the harness after a reload.
    threading.Thread(target=lambda: [pitch(i, m) for i in RESERVE for m in (False, True)], daemon=True).start()
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
                    reload=True, reload_dirs=[str(Path(__file__).parent)],
                    reload_excludes=["buyer.py", "market_buyer.py", "a2a_trade.py", "tune_prices.py",
                                     "shop_report.py", "*.json", "logs/*", ".market/*"])
    else:
        uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
