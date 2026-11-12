"""Adapted from scripts/starter_round3/seller.py at upstream d4b4d53e.

Round 3 starter seller: sells from the stock the kit keeps in $MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller.py --port 9301

The course inventory seller is adapted to our logged model harness, explicit purchase confirmation,
condition disclosure, approved clearance policy and bounded A-grade warranty trials.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import time
import math
from datetime import datetime, timezone
from uuid import uuid4
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

import model_router
from r3_harness import ask_json
from seller_rules import decision_mode, rule_move
from seller import purchase_offer
from r3_policy import selling_terms
from market_state import read, write, task_file
from kit_runtime import OFFER_SECONDS
from market_intel import fresh
from price_cap import capped
from receipt_label import receipt_item, request_form
from sale_order import grade_c_last, order_key
from strategy_policy import effective

NAME = "kcwarni Inventory Shop"
FEATURES = ["actual_grade_receipt", "bounded_warranty_v1", "coded_seller_rules", "model_breaker", "food_pricing_v2", "price_cap_v1", "sale_order_v1", "a_grade_six_hour_v1", "pq1_v1", "food_floor_108_v1", "a_warranty_90m_v1", "shelf_need_fit_v1", "shelf_need_days_pct_v1", "food_hours_need_v1", "food_shelf_left_notice_v1"]
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))

PROMPT = """You are a Round 3 inventory sales agent. A buyer is negotiating for one {name}.
The list price is {list_price}. Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
Seek profitable inventory turnover. Quote no more than {list_price}; make measured concessions toward the coded floor.
The floor includes approved carrying-cost/margin or clearance rules. Do not invent warranties, grade claims or inspections.
Do not use furniture prices, shop preferences or tactics from Round 2.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def condition_notice(unit, terms=None):
    """Disclose observed inventory state and the coded coverage, without inventing inspection."""
    if not unit.get("unit"):
        return "", {}
    grade = unit.get("grade", "unknown")
    expiry = unit.get("expires_at")
    expires = datetime.fromtimestamp(expiry, timezone.utc).isoformat() if expiry is not None else None
    supplied = unit.get("supplied_at")
    details = {"kind": "product_condition", "unit": unit["unit"], "sku": unit["sku"],
               "grade_observed": grade, "expires_at": expires, "supplied_at": supplied,
               "certificate_available": bool(unit.get("cert")),
               "discount_reason": (terms or {}).get("discount_reason"),
               "discount_stage": (terms or {}).get("discount_stage", 0)}
    details["warranty_hours"] = (terms or {}).get("warranty_hours", 0)
    notice = f"Inventory condition: {grade}."
    if expires:
        notice += f" Expires at {expires}."
    left = hours_left(unit, time.time()) if unit.get("kind") == "food" else None
    if left is not None:
        # Shelf life left on a food quote (the user's 11-12 choice in the Orchestrator session, about 15:3x KST: "남은 유통기한 명시
        # (권장)"): every probe-food low since 06:04 came from a request with no stated need and a unit under 75% of its shelf life,
        # while units shown short to a buyer who stated a need never sold. The notice and the data part now give the hours left
        # (rounded down), the SKU's full shelf hours and the share left, so a buyer can see the unit is short before buying.
        # No refusal and no price change.
        hours = max(0, int(left))
        full = shelf_hours_by_sku().get(unit.get("sku"))
        share = int(100 * max(0, left) / full) if full else None
        details.update(shelf_hours_left=hours, shelf_hours_full=full, shelf_left_percent=share)
        notice += f" Shelf life left: {hours} h" + (f" of {full:g} h ({share}%)." if full else ".")
    if terms and terms["discount_stage"]:
        notice += f" Clearance reason: {terms['discount_reason']}; stage {terms['discount_stage']}."
    notice += " Confirm the certificate at checkout; condition can change while negotiating."
    notice += f" Warranty: {details['warranty_hours']} hours."
    return notice, details


# A stated shelf-life need (the user's 11-11 choice in the Orchestrator session, about 18:3x KST: "맞는 재고 골라 팔기 + 기록
# (권장)"): probe-food 2 stars on 11-10 said "Only X h of shelf life left; I needed Y h", and find_unit gave out the unit that
# expires first. When the request says how many hours it needs, find_unit picks a unit that has them if one exists, and
# otherwise picks as before. No refusal and no disclosure change; the pricing log records the stated need.
NEED_PATTERNS = (
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)\b(?:\s+of)?\s+(?:shelf[\s-]?life|freshness)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)\b\s+(?:left|remaining)", re.I),
    re.compile(r"min_hours_left\W{0,3}(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*시간\s*이상"),
)


# Days and shares of shelf life (the user's 11-12 choice in the Orchestrator session, about 10:0x KST: "단서 기록 + 일·% 표현
# 읽기 (권장)"): 55 probe-food 2 stars from 11-11 18:04 to 11-12 06:08 each said "I needed Y h" with Y exactly 25, 50 or 75% of
# the SKU's full shelf life, while only about 2% of food requests stated a need in hours. "N days" counts as N x 24 hours, and
# "N% of shelf life" as N% of the catalog's shelf_hours for the unit's SKU. Only which unit is picked changes, as above.
DAY_PATTERNS = (
    re.compile(r"(\d+(?:\.\d+)?)\s*days?\b(?:\s+of)?\s+(?:shelf[\s-]?life|freshness)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*days?\b\s+(?:left|remaining)", re.I),
    re.compile(r"min_days_left\W{0,3}(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*일\s*이상"),
)
PERCENT_PATTERNS = (
    re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+(?:its\s+|the\s+|their\s+)?(?:full\s+|total\s+)?)?(?:shelf[\s-]?life|freshness)", re.I),
    re.compile(r"(?:shelf[\s-]?life|freshness)\W{0,3}(?:of\s+|at\s+least\s+|>=?\s*|≥\s*){0,3}(\d+(?:\.\d+)?)\s*%", re.I),
    re.compile(r"(?:유통기한|신선도)\D{0,10}(\d+(?:\.\d+)?)\s*%"),
)
# What the pricing log records about a food request's shelf-life wording: terms from this fixed list and numbers that carry a
# time or percent unit. Never the request text itself.
CLUE_TERMS = (
    ("shelf life", re.compile(r"shelf[\s-]?life", re.I)), ("fresh", re.compile(r"fresh", re.I)),
    ("expir", re.compile(r"expir", re.I)), ("best before", re.compile(r"best\s+before", re.I)),
    ("use by", re.compile(r"use\s+by", re.I)), ("left", re.compile(r"\bleft\b", re.I)),
    ("remaining", re.compile(r"remaining", re.I)), ("at least", re.compile(r"at\s+least", re.I)),
    ("min_hours_left", re.compile(r"min_hours_left", re.I)), ("유통기한", re.compile(r"유통기한")), ("신선", re.compile(r"신선")),
    ("이상", re.compile(r"이상")), ("use within", re.compile(r"use\s+within", re.I)), ("within", re.compile(r"\bwithin\b", re.I)),
    ("need", re.compile(r"\bneed", re.I)), ("use_within_hours", re.compile(r"use_within_hours", re.I)),
)
# Every hours number in a food request is the need (the user's 11-12 choice in the Orchestrator session, about 12:5x KST: "식품 요청의
# 시간 숫자는 모두 읽기 (권장)"): the market gives each food order a use_within_hours (the kit log shows 42/84/126 and 180/360/540 h,
# 25/50/75% of shelf life, each 200+ times), the 2 star reviews say "I needed Y h" with those same Y, and in the 2 h after the 11-12
# 10:44 restart 8 of 41 food requests carried an hours number in wording the patterns above do not read. A number in a delivery or
# reply window ("deliver within 3 h", "respond within 2 hours") or followed by "ago" is not read. Only which unit is picked changes.
FOOD_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:(?:h|hrs?|hours?)\b|시간)", re.I)
WINDOW_WORDS = r"(?:deliver|ship|arriv|dispatch|respond|response|repl|answer|valid|배송|발송|도착|응답|답변|회신)"
# The window word at most three words before the number ("deliver within 3 h", "respond within the next 2 hours"), or at most two
# words after it with no punctuation between ("3시간 이내 배송", "2 hour delivery").
NOT_SHELF_BEFORE = re.compile(WINDOW_WORDS + r"\w*(?:[^\w.;,!?\n]+\w+){0,3}[^\w.;,!?\n]*$", re.I)
# The order's own field, when a buyer passes it on as data ("use_within_hours": 126).
USE_WITHIN_KEY = re.compile(r"use_within_hours\W{0,3}(\d+(?:\.\d+)?)", re.I)
NOT_SHELF_AFTER = re.compile(r"[^\S\n]*(?:-?[^\W\d]+[^\S\n]+){0,2}" + WINDOW_WORDS + r"|\s*ago\b", re.I)


def food_need_hours(text):
    """For a food request: the largest number with an hours unit outside delivery and reply windows, or None when there is none."""
    text = str(text or "")
    found = []
    for m in FOOD_HOURS.finditer(text):
        if NOT_SHELF_BEFORE.search(text[max(0, m.start() - 60):m.start()]) or NOT_SHELF_AFTER.match(text, m.end()):
            continue
        found.append(float(m.group(1)))
    found += [float(m.group(1)) for m in USE_WITHIN_KEY.finditer(text)]
    found = [h for h in found if 0 < h <= 10000]
    return max(found) if found else None
# "grade A or B", "grades A, B": each letter named after "grade" is logged as "grade A" and so on.
CLUE_GRADES = re.compile(r"\b(?i:grades?)\s+([ABC]\b(?:\s*(?:,|/|or|and)\s*[ABC]\b)*)")
CLUE_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(%|hours?|hrs?|h\b|days?|d\b|weeks?|시간|일|주)", re.I)
CLUE_UNITS = {"%": "%", "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h", "시간": "h", "d": "d", "day": "d", "days": "d",
              "일": "d", "week": "w", "weeks": "w", "주": "w"}


def _needs(patterns, text, top):
    found = [float(m.group(1)) for pattern in patterns for m in pattern.finditer(str(text or ""))]
    found = [x for x in found if 0 < x <= top]
    return max(found) if found else None


def stated_need_hours(text):
    """The most hours of shelf life the request says the order needs ("N days" as N x 24), or None when it states none."""
    hours, days = _needs(NEED_PATTERNS, text, 10000), _needs(DAY_PATTERNS, text, 10000 / 24)
    found = [x for x in (hours, days * 24 if days is not None else None) if x is not None]
    return max(found) if found else None


def stated_need_percent(text):
    """The largest share of full shelf life (0-100) the request says the order needs, or None when it states none."""
    return _needs(PERCENT_PATTERNS, text, 100)


def shelf_clues(text):
    """{"terms", "numbers"}: the shelf-life terms (from CLUE_TERMS) and the numbers with a time or percent unit in the request, at
    most 12 numbers, as [value, unit]; never the text itself."""
    text = str(text or "")
    terms = [name for name, pattern in CLUE_TERMS if pattern.search(text)]
    grades = {g for m in CLUE_GRADES.finditer(text) for g in re.findall(r"\b[ABC]\b", m.group(1))}
    terms += [f"grade {g}" for g in sorted(grades)]
    numbers = [[float(m.group(1)), CLUE_UNITS.get(m.group(2).lower(), m.group(2))] for m in CLUE_NUMBER.finditer(text)][:12]
    return {"terms": terms, "numbers": numbers}


_SHELF = {"stamp": None, "hours": {}}


def shelf_hours_by_sku():
    """{sku: the catalog's full shelf_hours}, parsed again only when catalog.json changes; {} when it cannot be read."""
    path = MARKET_DIR / "catalog.json"
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if _SHELF["stamp"] != stamp:
            catalog = json.loads(path.read_text(encoding="utf-8"))
            _SHELF["hours"] = {sku: p["shelf_hours"] for sku, p in catalog.items() if isinstance(p, dict)
                               and type(p.get("shelf_hours")) in (int, float) and p["shelf_hours"] > 0}
            _SHELF["stamp"] = stamp
    except (OSError, ValueError, AttributeError):
        return {}
    return _SHELF["hours"]


def unit_need_hours(unit, hours, percent, shelf, food=None):
    """The hours this unit must have left: the largest of the stated hours, the stated share of its SKU's full shelf life and, for a
    food unit, the food request's hours number (food_need_hours); None when none applies to it."""
    found = [x for x in (hours, percent / 100 * shelf[unit["sku"]] if percent is not None and unit.get("sku") in shelf else None,
                         food if unit.get("kind") == "food" else None)
             if x is not None]
    return max(found) if found else None


def hours_left(unit, now):
    """Hours of shelf life left, or None for a unit without an expiry (order_key treats it as never expiring)."""
    return (unit["expires_at"] - now) / 3600 if unit.get("expires_at") else None


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation; with a stated shelf-life need, the first
    to expire among those that meet it, if any does."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if "round" not in inv:
        return None
    if inv.get("round", 0) < 3:
        return None  # R3-only: never synthesize the Round 2 furniture inventory.
    units = inv.get("units", [])
    fit = [u for u in units if u["grade"] != "expired" and u["unit"] not in taken and
           ((u.get("category") or "").split(">")[-1].lower() in want or (u.get("name") or "").lower() in want
            or u["sku"].lower() in want)]
    named = [u for u in fit if u["sku"].lower() in want]
    fit = named or fit                                   # a SKU the buyer named comes first
    need, percent, food = stated_need_hours(text), stated_need_percent(text), food_need_hours(text)
    if need is not None or percent is not None or food is not None:
        now = time.time()
        shelf = shelf_hours_by_sku() if percent is not None else {}
        def meets(u):
            left, wanted = hours_left(u, now), unit_need_hours(u, need, percent, shelf, food)
            return left is None or wanted is None or left >= wanted
        enough = [u for u in fit if meets(u)]
        fit = enough or fit
    c_last = grade_c_last()
    return min(fit, key=lambda u: order_key(u, c_last), default=None)


def model_allowed():
    return model_router.available(role="seller")


async def choose_move(deal, terms, reserve, unit):
    """Coded rule by default. The model is optional: any reason it cannot answer leaves the coded rule in place."""
    rule = rule_move(deal["ask"], deal["offer"], reserve, terms["quote"], deal.get("asks", 0))
    if decision_mode() != "model":
        return rule
    allowed, reason = model_allowed()
    if not allowed:
        print(f"[decision] model off ({reason}); coded rule used", flush=True)
        return rule
    try:
        return await asyncio.to_thread(ask_json, PROMPT.format(
            name=unit["name"], list_price=terms["quote"], reserve=reserve, transcript="\n".join(deal["lines"])))
    except Exception as error:
        print(f"[harness] decision failed: {error}; coded rule used", flush=True)
        return rule


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines"}
        self.committed_units = set()
        self.busy = set()
        self.locks = {}

    def prune(self, now=None):
        now = time.time() if now is None else now
        for task, deal in list(self.open.items()):
            if task not in self.busy and now - deal.get("touched", now) > 180:
                self.open.pop(task, None)
        # Restore signed holds after seller restarts. Never free an uncertain payment.
        managed, held = set(), set()
        for path in (MARKET_DIR / "signed-offers").glob("*.json"):
            offer = read(path)
            managed.add(offer["unit"])
            event = read(task_file(MARKET_DIR, "checkout-events", offer["task"]), {})
            status = event.get("status")
            released = status in ("canceled", "refused", "expired")
            if (os.environ.get("MARKET_LEASE_GUARD") == "1" and now >= offer["expires_at"] + 90
                    and status not in ("processing", "uncertain", "completed")):
                released = True
            if not released:
                held.add(offer["unit"])
        self.committed_units.difference_update(managed)
        self.committed_units.update(held)

    async def execute(self, context, event_queue):
        self.prune()
        task_id = context.current_task.id if context.current_task else uuid4().hex
        lock = self.locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            self.busy.add(task_id)
            busy_ids = [task_id]
            try:
                return await self._execute(context, event_queue, busy_ids)
            finally:
                for identity in busy_ids:
                    self.busy.discard(identity)
                    if identity in self.open:
                        self.open[identity]["touched"] = time.time()

    async def _execute(self, context, event_queue, busy_ids):
        write(MARKET_DIR/"strategy-consumer.json", {"protocol": 1, "pid": os.getpid(), "heartbeat_at": time.time(),
              "features": FEATURES})
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        self.busy.add(task.id)
        busy_ids.append(task.id)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            unit = find_unit(text, self.committed_units | {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]})
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "request": text,
                                       "touched": time.time(), "lines": [f"buyer: {text}"]}
        else:
            deal["lines"].append(f"buyer: {text}")
            deal["offer"] = purchase_offer(text, deal["unit"]["sku"], deal["ask"])
            if deal["offer"] is None:
                await up.requires_input(say(
                    f"To confirm, send an unconditional numeric offer. Current quote: {deal['ask']}"))
                return

        u = deal["unit"]
        if u.get("unit") in self.committed_units:
            self.open.pop(task.id, None)
            await up.reject(say("Unit is already reserved for checkout. Please request a new quote."))
            return
        catalog = {}
        try:
            if u.get("unit"):
                inventory = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
                u = next(x for x in inventory["units"] if x["unit"] == u["unit"])
                deal["unit"] = u
                catalog = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
            signals = fresh(MARKET_DIR).get("skus", {}).get(u["sku"], {})
            terms = capped(selling_terms(u, catalog, signals=signals, runtime=effective(MARKET_DIR)), u, task=task.id)
            from warranty_trial import assignment
            coverage = deal.get("coverage")
            if coverage is None or coverage.get("enrolled"):
                coverage = assignment(MARKET_DIR, task.id, u, terms)
                deal["coverage"] = coverage
            terms["floor"] = max(terms["floor"], coverage["floor"])
            terms["warranty_hours"] = coverage["hours"]
        except (OSError, ValueError, KeyError, StopIteration):
            self.open.pop(task.id, None)
            await up.reject(say("This unit is unavailable or its current condition cannot be verified."))
            return
        reserve = terms["floor"]
        log_dir = Path(__file__).resolve().parent / "logs" / "pricing"
        log_dir.mkdir(parents=True, exist_ok=True)
        need_hours, need_percent = stated_need_hours(deal.get("request")), stated_need_percent(deal.get("request"))
        need_food = food_need_hours(deal.get("request")) if u.get("kind") == "food" else None
        (log_dir / (uuid4().hex + ".json")).write_text(json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(), "task": task.id,
             "unit": u.get("unit"), "sku": u["sku"], "cost": u["cost"],
             "grade_observed": u.get("grade"), "expires_at": u.get("expires_at"), "request_form": request_form(deal.get("request"), u),
             "stated_need_hours": need_hours, "stated_need_percent": need_percent, "food_need_hours": need_food,
             "need_for_unit_hours": unit_need_hours(u, need_hours, need_percent, shelf_hours_by_sku() if need_percent is not None else {},
                                                    need_food),
             "shelf_clues": shelf_clues(deal.get("request")) if u.get("kind") == "food" else None, **terms},
            ensure_ascii=False, indent=2), encoding="utf-8")
        if deal["ask"] is not None and deal["offer"] == deal["ask"] and deal["offer"] >= reserve:
            move, raw = {"action": "accept"}, "deterministic acceptance of the disclosed quote"
        else:
            move, raw = await choose_move(deal, terms, reserve, u)
        print(f"[decision] {raw}", flush=True)
        action = (move or {}).get("action")
        # A model call takes time: verify the unit and terms again before signing.
        if u.get("unit"):
            before = deal.get("disclosed", (u.get("grade"), u.get("expires_at"), terms["quote"]))
            try:
                inventory = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
                u = next(x for x in inventory["units"] if x["unit"] == u["unit"])
                if u["unit"] in self.committed_units:
                    raise ValueError("Unit already committed")
                terms = capped(selling_terms(u, catalog, signals=signals, runtime=effective(MARKET_DIR)), u, task=task.id)
                if deal.get("coverage", {}).get("hours"):
                    coverage = assignment(MARKET_DIR, task.id, u, terms)
                    terms["floor"] = max(terms["floor"], coverage["floor"])
                terms["warranty_hours"] = deal.get("coverage", {}).get("hours", 0)
                reserve = terms["floor"]
                deal["unit"] = u
            except (OSError, ValueError, KeyError, StopIteration):
                self.open.pop(task.id, None)
                await up.reject(say("Unit unavailable or expired while checking the offer. No purchase confirmed."))
                return
            after = (u.get("grade"), u.get("expires_at"), terms["quote"])
            if before != after:
                action, move = "counter", {"price": max(reserve, min(deal["ask"] or terms["quote"], terms["quote"]))}
                print("[guard] inventory condition/price changed; disclose again and request confirmation", flush=True)
        # A new unit always receives an explicit quote before purchase confirmation.
        if terms["discount_stage"] and deal["ask"] is None:
            action, move = "counter", {"price": terms["quote"]}
        if terms["discount_stage"] and deal["ask"] != terms["quote"]:
            action, move = "counter", {"price": terms["quote"]}
        if deal["offer"] is not None and deal["ask"] is not None and deal["offer"] > deal["ask"]:
            action, move = "counter", {"price": deal["ask"]}
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below cost {reserve} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"] or u["list_price"]}
        if action == "accept":
            price = deal["offer"]
            receipt = {"shop": NAME, "item": receipt_item(deal.get("request"), u), "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                receipt.update(claim=u["grade"], cert=True, warranty_hours=terms.get("warranty_hours", 0))
            receipt = sign(receipt)
            if u.get("unit"):
                write(task_file(MARKET_DIR, "signed-offers", task.id),
                      {"task": task.id, "unit": u["unit"], "sku": u["sku"], "receipt_item": receipt["item"], "request_form": request_form(deal.get("request"), u), "price": price,
                       "cost": u["cost"], "strategy_version": terms.get("strategy_version"),
                       "grade": u.get("grade"), "warranty_hours": terms.get("warranty_hours", 0),
                       "warranty_trial_group": deal.get("coverage", {}).get("group"),
                       "strategy_experiment": terms.get("strategy_experiment"),
                       "list_price": u.get("list_price"), "price_cap": terms.get("price_cap"),
                       "issued_at": time.time(), "expires_at": time.time() + OFFER_SECONDS})
                self.committed_units.add(u["unit"])
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if type(price) not in (int, float) or not math.isfinite(price):
            price = deal["ask"] or u["list_price"]      # unparsed answer: repeat the last ask
        price = min(price, max(reserve, terms["quote"]))
        if price < reserve:                             # the harness may forget the cost; the code does not
            print(f"[guard] ask {price} below cost {reserve} -> {reserve}", flush=True)
            price = reserve
        if terms["discount_stage"]:
            price = terms["quote"]
        deal["ask"] = int(price)
        deal["asks"] = deal.get("asks", 0) + 1
        deal["disclosed"] = (u.get("grade"), u.get("expires_at"), terms["quote"])
        notice, details = condition_notice(u, terms)
        reply = (f"{u['name']}: asking {deal['ask']}. Send your offer as a number." if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        if notice:
            reply = notice + "\n" + reply
        deal["lines"].append(f"you: {reply}")
        parts = [new_text_part(reply)]
        if details:
            parts.append(new_data_part(details))
        await up.requires_input(up.new_agent_message(parts=parts))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    write(MARKET_DIR/"strategy-consumer.json", {"protocol": 1, "pid": os.getpid(), "heartbeat_at": time.time(),
          "features": FEATURES})
    mode = decision_mode()
    allowed, reason = model_allowed()
    print(f"[model] seller: decisions={mode}, model {'on' if allowed else 'off'} ({reason})", flush=True)
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="A small online shop. Ask for an item by name or SKU. Coded rules answer quote requests and numeric offers.",
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
