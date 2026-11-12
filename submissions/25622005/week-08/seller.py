"""Round 3 shop: a rule-based A2A seller over the stock the kit keeps in $MARKET_DIR/inventory.json.

    python seller.py --port 9301

Each message is answered by tools/pricing.py (D3 price, D4 below-cost, D5 warranty,
D14 end-of-round, L5 fact line) with the policy reloaded from strategy/SKILL.md per request. The first
reply names the unit and carries its market certificate; the signed receipt names the SKU and the unit
(plus the claim and the warranty when they apply). Decisions go to logs/decisions/seller.jsonl, diagnostics
to logs/hooks/seller.log; nothing is printed, because the kit copies our stdout into market-kit.log.
Only a message the rules cannot read (no SKU, name or category; later no offer or acceptance) goes to a small
model (D9, tools/llm_fallback.py), off the event loop; its reading then meets the same floor and promise rules.
"""
import argparse
import asyncio
import base64
import json
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# a2a's tracing wrapper logs 'Failed to detach context' tracebacks when a task is garbage-collected; we use
# no tracing. Read at import time, so before any a2a import.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

import uvicorn
from cryptography.hazmat.primitives import serialization
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import policy
from memory import decisions
from memory.store import HOME, LOGS, hook_log, read_json, tail_jsonl
from tools import llm_fallback, pricing

NAME = "psyoung82"
RECEIPT_KEYS = ("shop", "item", "price", "task")          # what sig_seller covers (kit L87)
SKU_RE = re.compile(r"\bG[0-9a-f]{8}\b", re.I)
NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?!\d|,\d|\.\d)"
NOT_PRICE = r"(?!\s*(?:h\b|hrs?\b|hours?\b|%|kg\b|g\b|ml\b|l\b|units?\b|pcs\b|시간|개|팩))"
OFFER_RE = re.compile(r"(?:offer|bid|pay|counter(?:offer)?|propose|final|accept|deal|for|at|price"
                      r"|(?:can|could|will|would)\s+(?:do|go|pay|give)|settle\s+(?:at|for)|take\s+it\s+(?:at|for)|meet\s+(?:you\s+)?at)"
                      r"(?:\s+(?:is|of|at|your\s+(?:offer|price)|the\s+price))?\s*[:=]?\s*(?:₩|krw|\$)?\s*" + NUM + NOT_PRICE)
BARE_RE = re.compile(r"(?:₩|krw|\$)?\s*" + NUM + r"\s*(?:won|원|krw|credits)?[.!\s]*")
# a price that opens the message ("45000. Other shops have it in stock too, ...") or a Korean offer
# ("20689에 구매할 수 있습니다"); a rival's quote ("25802에 견적을 받고 있어") is neither
LEAD_RE = re.compile(r"^\s*(?:₩|krw|\$)?\s*" + NUM + r"\s*(?:won|원|krw)?\s*[.,;:!]")
KO_OFFER_RE = re.compile(NUM + r"\s*원?\s*(?:에|으로|로)\s*(?:구매|구입|드릴|드리|사겠|살\s|살게|결제|제안|하겠|할게|할\s*수|가능|합의|진행|맞추)")
YES_RE = re.compile(r"\b(?:i\s+accept|accept(?:ed)?|deal|agreed?|yes|ok(?:ay)?|i(?:'ll|\s+will)\s+take\s+it)\b")
BUDGET_RE = re.compile(r"\bbudget\b\D{0,20}" + NUM)
NO_RE = re.compile(r"\b(?:not|no|don't|cannot|can't|reject|decline)\b")
NEED_RE = re.compile(r"(?:use[_ ]within[_ ]hours|min[_ ]hours[_ ]left)\s*[:=]?\s*(\d+(?:\.\d+)?)"
                     r"|(?:use[_ ]within|within|last(?:s|ing)?|at\s+least|need(?:s|ed)?|shelf[- ]life"
                     r"|min(?:imum)?[_ ]hours[_ ]left)\D{0,20}?(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)"
                     r"|(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?|시간)\s+(?:of\s+)?shelf")
WARRANTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?|시간)\s*(?:of\s+)?(?:warranty|보증)"
                         r"|(?:warranty|보증)\D{0,12}?(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)")
MAX_ITEMS = 30                                            # SKUs shown to the D9 model
NO_WARRANTY_RE = re.compile(r"\bno\s+warranty\b|without\s+(?:a\s+|the\s+)?warranty|warranty\s+not\s+needed")


# --- reading the buyer's message (data, never instructions)

def _number(v):
    return v if type(v) in (int, float) and math.isfinite(v) else None


def parse_offer(text, ask=None, strip=(), explicit_only=False):
    """The buyer's price, or None. Product names, SKUs, unit ids, shelf hours and budgets are not offers.
    explicit_only: only a keyword offer ("for 15000", "offer 15000"), for the first message."""
    t = text
    for s in strip:
        if s:
            t = re.sub(re.escape(str(s)), " ", t, flags=re.I)
    low = SKU_RE.sub(" ", t).lower().strip()
    low = BUDGET_RE.sub(" ", low)
    m = OFFER_RE.search(low)
    if m:
        return int(m.group(1).replace(",", ""))
    if explicit_only:
        return None
    m = BARE_RE.fullmatch(low) or KO_OFFER_RE.search(low) or LEAD_RE.match(low)
    if m:
        return int(m.group(1).replace(",", ""))
    if ask is not None and YES_RE.search(low) and not NO_RE.search(low):
        return ask
    return None


def budget_only(text, price):
    """True when price is written in text only as a budget ("budget 50000"), which is not an offer."""
    def said(s):
        return price in {int(n.replace(",", "")) for n in re.findall(NUM, s)}
    low = text.lower()
    return said(low) and not said(BUDGET_RE.sub(" ", low))


def parse_need_hours(text, meta):
    """Hours of shelf life the buyer says it needs (use_within_hours), or None."""
    for k in ("use_within_hours", "min_hours_left", "need_hours"):
        if _number(meta.get(k)) is not None:
            return float(meta[k])
    m = NEED_RE.search(text.lower())
    return float(next(g for g in m.groups() if g)) if m else None


def parse_warranty(text, meta):
    """None: not mentioned; False: the buyer does not want one; True or hours: asked for."""
    v = meta.get("warranty_hours")
    if _number(v) is not None:
        return v if v > 0 else False
    low = text.lower()
    if NO_WARRANTY_RE.search(low):
        return False
    if "warranty" not in low and "보증" not in low:
        return None
    m = WARRANTY_RE.search(low)
    return float(m.group(1) or m.group(2)) if m else True


def message_meta(message):
    """Message metadata merged with any data parts the buyer sent."""
    try:
        d = MessageToDict(message)
    except Exception:
        return {}
    meta = dict(d.get("metadata") or {})
    for p in d.get("parts") or []:
        if isinstance(p.get("data"), dict):
            meta.update(p["data"])
    return meta


# --- stock

def load_catalog(path):
    """sku -> entry; the kit writes a dict, older dumps are a list."""
    cat = read_json(path, {}) or {}
    if isinstance(cat, list):
        cat = {x.get("sku"): x for x in cat if isinstance(x, dict)}
    return cat if isinstance(cat, dict) else {}


def with_shelf(unit, catalog):
    u = dict(unit)
    if u.get("kind") == "food" and not u.get("shelf_hours"):
        u["shelf_hours"] = (catalog.get(u["sku"]) or {}).get("shelf_hours")
    return u


GRADE_RANK = {"A": 0, "B": 1, "C": 2}     # the order goods are offered in (find_unit)


def find_unit(text, inv, promised=(), need_hours=None, catalog=None, now=None, pol=None):
    """The unit to sell for what the buyer asked: a SKU named in the text first, then a product name, then a
    category leaf. Skips expired units and units promised in other open negotiations. For food with
    need_hours, only units with at least that many hours left; of those, the one that expires first.
    Goods: the best grade first (A, then B, then C), the oldest within the grade, overpriced units last when
    pol is given. Until 11-12 goods went oldest first, which was the C unit the A and B sales had left behind:
    a third of the big-ticket first replies offered a C while the market's big settles are A and B almost
    entirely and buyers' accept terms name the grades they pay for, so those signs went unpaid (1 of 13 paid
    against 13 of 14 for A) and three quarters of those buyers canceled."""
    if (inv.get("round") or 0) < 3:
        return None
    now = time.time() if now is None else now
    catalog = catalog or {}
    want = text.lower()
    units = [with_shelf(u, catalog) for u in inv.get("units") or []
             if u.get("unit") not in promised and u.get("grade") != "expired"]
    units = [u for u in units if u.get("kind") != "food" or u.get("expires_at") is None or pricing.hours_left(u, now) > 0]
    fit = ([u for u in units if u["sku"].lower() in want]
           or [u for u in units if u.get("name") and u["name"].lower() in want]
           or [u for u in units if (u.get("category") or "").split(">")[-1].lower() in want and u.get("category")])
    if need_hours is not None:
        fit = [u for u in fit if u.get("kind") != "food" or (pricing.hours_left(u, now) or 0) >= need_hours]
    return min(fit, key=lambda u: (pol is not None and pricing.overpriced(u, pol),
                                   GRADE_RANK.get(u.get("grade"), 3) if u.get("kind") == "goods" else 0,
                                   u.get("expires_at") or float("inf"), u.get("supplied_at") or 0), default=None)


def stock_items(inv, taken=()):
    """The SKUs we can offer, for the D9 model: [{sku, name, list_price}], at most MAX_ITEMS."""
    items = {}
    for u in inv.get("units") or []:
        if u.get("unit") not in taken and u.get("grade") != "expired" and u.get("sku"):
            items.setdefault(u["sku"], {"sku": u["sku"], "name": u.get("name"), "list_price": u.get("list_price")})
    return list(items.values())[:MAX_ITEMS]


# --- memory (role D's API; neutral values when it is missing or broken)

def _query():
    try:
        from memory import query
        return query
    except Exception:
        return None


def category_uwh(summary, leaf):
    q = _query()
    try:
        return dict(q.category_stats(summary, leaf)["uwh"])
    except Exception:
        return {"n": 0, "median": None}


def unit_break_q(summary, unit, leaf):
    """memory/query.break_q for the unit's SKU and grade, or None (a summary without the table, or an error)."""
    q = _query()
    try:
        return q.break_q(summary, unit.get("sku"), unit.get("grade"), leaf)
    except Exception:
        return None


def summary_ref(kind, key):
    q = _query()
    try:
        return q.summary_ref(kind, key)
    except Exception:
        return f"{kind}:{key}"


def sign_receipt(receipt, key):
    body = {k: receipt[k] for k in RECEIPT_KEYS}
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {**receipt, "sig_seller": base64.b64encode(key.sign(data)).decode()}


class Shop(AgentExecutor):
    def __init__(self, market_dir, key_path, clock=time.time):
        self.md, self.key_path, self.clock = Path(market_dir), Path(key_path), clock
        self.key = None
        self.open = {}            # task -> deal
        self.promised = {}        # unit -> (task, until)
        self.warranted = []       # (ts, list_price, unit) of warranted receipts we signed
        self.summary, self.summary_at = {}, 0.0
        self.catalog, self.catalog_mtime = {}, None
        self.err_ver = None
        cutoff = clock() - 6 * 3600
        for r in tail_jsonl(LOGS / "decisions" / "seller.jsonl", 2000):
            c = r.get("choice") if isinstance(r.get("choice"), dict) else {}
            if r.get("kind") == "sign" and c.get("warranty_hours") and (r.get("ts") or 0) >= cutoff:
                self.warranted.append((r["ts"], c.get("list_price") or c.get("price") or 0, c.get("unit")))

    # files
    def _key(self):
        if self.key is None:
            self.key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        return self.key

    def _catalog(self):
        p = self.md / "catalog.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return self.catalog
        if mt != self.catalog_mtime:
            self.catalog, self.catalog_mtime = load_catalog(p), mt
        return self.catalog

    def _summary(self, now):
        if now - self.summary_at > 30:
            q = _query()
            try:
                self.summary = q.load_summary() if q else {}
            except Exception:
                self.summary = {}
            self.summary_at = now
        return self.summary

    def _ctx(self, unit, now, inv, ttl):
        """warranted_6h counts paid or still pending warranties: a signed unit that left the inventory was
        paid; one still held after the promise TTL (ttl s) was not."""
        leaf = (unit.get("category") or "").split(">")[-1]
        self.warranted = [w for w in self.warranted if w[0] >= now - 6 * 3600]
        held = {u["unit"] for u in inv.get("units") or [] if u.get("unit")}
        summary = self._summary(now)
        return {"uwh": category_uwh(summary, leaf), "category": leaf, "break_q": unit_break_q(summary, unit, leaf),
                "warranted_6h": sum(w[1] for w in self.warranted if w[2] not in held or now - w[0] < ttl)}

    def _prune(self, now):
        self.promised = {u: v for u, v in self.promised.items() if v[1] > now}
        for t in [t for t, d in self.open.items() if now - d["seen"] > 900]:
            del self.open[t]

    def _release(self, task_id):
        self.open.pop(task_id, None)
        self.promised = {u: v for u, v in self.promised.items() if v[0] != task_id}

    # A2A
    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        try:
            await self._handle(context, task, up, time.monotonic())
        except Exception as e:
            hook_log("seller", f"error on task {task.id}: {type(e).__name__}: {e}")
            self._release(task.id)
            try:
                await up.reject(up.new_agent_message(parts=[new_text_part("Sorry, the shop could not handle that message.")]))
            except Exception:
                pass

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.get(task.id)
        self._release(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()
        if deal:
            pol, ver, _ = policy.load()
            self._decide("reject", ver, task.id, deal, choice={"action": "canceled"}, why="buyer canceled the task")

    def _decide(self, kind, ver, task_id, deal=None, t0=None, **kw):
        u = (deal or {}).get("unit") or {}
        ref = {"task": task_id}
        if u:
            ref.update(sku=u.get("sku"), unit=u.get("unit"))
        refs = list(kw.pop("memory_refs", []))
        if deal and (deal.get("ctx", {}).get("uwh") or {}).get("n"):
            refs.append(summary_ref("cat", deal["ctx"]["category"]))
        ms = round((time.monotonic() - t0) * 1000) if t0 else None
        decisions.decision("seller", kind, policy_ver=ver, ref=ref, memory_refs=refs, elapsed_ms=ms, **kw)

    def _price(self, deal, pol, now, wreq):
        """Floor, first ask and warranty for the deal's unit; resets the concession state."""
        u, ctx = deal["unit"], deal["ctx"]
        w = pricing.warranty_terms(u, wreq, ctx, pol, now)
        fl, rules = pricing.floor_detail(u, ctx, pol, now)
        ask = pricing.first_ask(u, ctx, pol, now)
        mult = w["price_mult"] if w else 1
        fl, ask = math.ceil(round(fl * mult, 6)), math.ceil(round(ask * mult, 6))
        deal.update(warranty=w, wreq=wreq, rules=rules + ["L5"] + (["D5"] if w else []),
                    state={"floor": fl, "first_ask": ask, "ask": ask, "countered": False, "final": False})

    def _inputs(self, deal, now, pol, offer=None):
        u, s = deal["unit"], deal["state"]
        return {"floor": s["floor"], "first_ask": s["first_ask"], "ask": s["ask"], "offer": offer,
                "list_price": u.get("list_price"), "cost": u.get("cost"), "kind": u.get("kind"),
                "grade_now": pricing.grade_now(u, now), "hours_left": _round(pricing.hours_left(u, now)),
                "need_hours": deal.get("need"), "warranty_req": deal.get("wreq"),
                "warranty_hours": (deal.get("warranty") or {}).get("hours"),
                "warranty_mult": (deal.get("warranty") or {}).get("price_mult"), "break_q": deal["ctx"].get("break_q"),
                "uwh": deal["ctx"]["uwh"], "hours_to_end": _round(pricing.hours_to_end(pol, now))}

    async def _handle(self, context, task, up, t0):
        pol, ver, errors = policy.load()
        if errors and ver != self.err_ver:
            self.err_ver = ver
            decisions.decision("seller", "policy_error", policy_ver=ver, inputs={"errors": errors[:10]},
                               why=f"strategy/SKILL.md failed validation; running on {ver}")
        now = self.clock()
        self._prune(now)
        say = lambda text, **meta: up.new_agent_message(parts=[new_text_part(text)], metadata=meta or None)
        text = get_message_text(context.message) or ""
        meta = message_meta(context.message)
        deal = self.open.get(task.id)
        ttl = pol["seller"]["promise_ttl_s"]

        if deal is None:
            inv = read_json(self.md / "inventory.json", {}) or {}
            if (inv.get("round") or 0) < 3:
                await up.reject(say("No stock before Round 3."))
                self._decide("reject", ver, task.id, None, t0, inputs={"text": text[:200]}, choice={"action": "reject"},
                             rules=[], why="inventory round < 3")
                return
            need = parse_need_hours(text, meta)
            margin = pol["seller"]["claim_food_margin_h"]
            hours = None if need is None else need + margin
            unit = find_unit(text, inv, self._taken(task.id, now), hours, self._catalog(), now, pol)
            reading = None
            if unit is None and not SKU_RE.search(text) \
                    and find_unit(text, inv, (), None, self._catalog(), now, pol) is None:
                reading = await self._read(text, {"items": stock_items(inv, self._taken(task.id, now))},
                                           pol, ver, task.id, t0)     # D9: nothing in it the rules can match
                if reading and reading["sku"]:
                    unit = find_unit(reading["sku"], inv, self._taken(task.id, now), hours, self._catalog(), now, pol)
            if unit is None:
                why = "no unit in stock for this request" + (f" with {need + margin:g} h left" if need is not None else "")
                await up.reject(say(f"Sorry, {why}. Ask by SKU."))
                self._decide("reject", ver, task.id, None, t0, inputs={"text": text[:200], "need_hours": need},
                             choice={"action": "reject"}, why=why)
                return
            deal = self.open[task.id] = {"unit": unit, "need": need, "seen": now}
            deal["ctx"] = self._ctx(unit, now, inv, ttl)
            self._price(deal, pol, now, parse_warranty(text, meta))
            self.promised[unit["unit"]] = (task.id, now + ttl)
            offer = reading["price"] if reading and reading["kind"] == "offer" else \
                parse_offer(text, None, self._strip(unit), explicit_only=True)
            if offer is None:
                await self._quote(up, deal, now, pol, ver, task.id, t0, first=True)
                return
            prefix = self._fact(deal, now) + ". "
        else:
            deal["seen"] = now
            u = deal["unit"]["unit"]
            holder = self.promised.get(u)
            if holder and holder[0] != task.id:
                self._release(task.id)
                await up.reject(say(f"Sorry, unit {u} is no longer available. Please ask again."))
                self._decide("reject", ver, task.id, deal, t0, choice={"action": "reject"},
                             why="promise expired and the unit went to another buyer")
                return
            self.promised[u] = (task.id, now + ttl)
            wreq = parse_warranty(text, meta)
            if wreq is not None and bool(wreq) != bool(deal.get("wreq")):
                self._price(deal, pol, now, wreq)
                await self._quote(up, deal, now, pol, ver, task.id, t0, first=True)
                return
            offer = parse_offer(text, deal["state"]["ask"], self._strip(deal["unit"]))
            if offer is None:                           # D9: no offer or acceptance the rules can read
                items = stock_items({"units": [deal["unit"]]})
                reading = await self._read(text, {"items": items, "ask": deal["state"]["ask"]}, pol, ver, task.id, t0)
                if reading and self.open.get(task.id) is deal:     # not canceled meanwhile
                    if reading["kind"] == "offer":
                        offer = reading["price"]
                    elif reading["kind"] == "accept":
                        offer = reading["price"] or deal["state"]["ask"]
            if offer is None:
                await self._quote(up, deal, now, pol, ver, task.id, t0, first=False)
                return
            prefix = ""

        st = deal["state"]
        action, price = pricing.respond(st, offer, pol)
        inputs = self._inputs(deal, now, pol, offer)
        if action == "accept":
            await self._sell(up, deal, task.id, price, now, pol, ver, t0, inputs, prefix)
        elif action == "counter":
            st.update(ask=price, countered=True)
            await up.requires_input(say(prefix + f"Counteroffer: {price}", ask=price, sku=deal["unit"]["sku"],
                                        unit=deal["unit"]["unit"]))
            self._decide("quote", ver, task.id, deal, t0, inputs=inputs, rules=deal["rules"],
                         choice={"action": "counter", "price": price}, why="offer between floor and midpoint")
        elif st["final"]:
            self._release(task.id)
            await up.reject(say(f"No deal below {price}."))
            self._decide("reject", ver, task.id, deal, t0, inputs=inputs, rules=deal["rules"],
                         choice={"action": "reject", "price": price}, why="offer below the floor after the final price")
        else:
            st.update(ask=price, final=True)
            await up.requires_input(say(prefix + f"Final price: {price}", ask=price, sku=deal["unit"]["sku"],
                                        unit=deal["unit"]["unit"]))
            self._decide("quote", ver, task.id, deal, t0, inputs=inputs, rules=deal["rules"],
                         choice={"action": "final", "price": price}, why="offer below the floor")

    def _taken(self, task_id, now):
        """Units promised to other open negotiations."""
        return {u for u, (t, until) in self.promised.items() if t != task_id and until > now}

    async def _read(self, text, ctx, pol, ver, task_id, t0):
        """D9: a small model's reading of a message the rules cannot read, or None (answer by rule). The
        call blocks, so it runs in a thread and other buyers are served meanwhile. A buyer is assumed to
        wait about as long for a reply as ours does (buyer.msg_timeout_s)."""
        if not ctx["items"] or not text.strip():
            return None
        reading = await asyncio.to_thread(llm_fallback.read, "seller", text, ctx, pol,
                                          t0 + pol["buyer"]["msg_timeout_s"], ver, {"task": task_id})
        if reading and reading["price"] is not None and budget_only(text, reading["price"]):
            reading = dict(reading, kind="other", price=None)  # the buyer's budget, not an offer
        return reading

    def _strip(self, unit):
        return (unit.get("name"), unit.get("sku"), unit.get("unit"))

    def _fact(self, deal, now):
        u, w = deal["unit"], deal.get("warranty")
        terms = {"grade": pricing.grade_now(u, now), "hours_left": pricing.hours_left(u, now),
                 "warranty_hours": w["hours"] if w else 0, "cert": bool(u.get("cert"))}
        return pricing.fact_line(u, deal["state"]["ask"], terms)

    async def _quote(self, up, deal, now, pol, ver, task_id, t0, first):
        u, st, w = deal["unit"], deal["state"], deal.get("warranty")
        text = self._fact(deal, now) if first else f"Our price for {u['sku']} unit {u['unit']}: ask {st['ask']}"
        data = {"sku": u["sku"], "unit": u["unit"], "name": u.get("name"), "ask": st["ask"],
                "list_price": u.get("list_price"), "grade_now": pricing.grade_now(u, now),
                "hours_left": _round(pricing.hours_left(u, now)), "warranty_hours": w["hours"] if w else 0,
                "cert": u.get("cert")}
        msg = up.new_agent_message(parts=[new_text_part(text), new_data_part(data)],
                                   metadata={"ask": st["ask"], "sku": u["sku"], "unit": u["unit"]})
        await up.requires_input(msg)
        self._decide("quote", ver, task_id, deal, t0, inputs=self._inputs(deal, now, pol), rules=deal["rules"],
                     choice={"action": "quote" if first else "repeat", "price": st["ask"]},
                     why="first reply: fact line and first ask" if first else "no price in the message")

    async def _sell(self, up, deal, task_id, price, now, pol, ver, t0, inputs, prefix):
        u, w = deal["unit"], deal.get("warranty")
        inv = read_json(self.md / "inventory.json", {}) or {}
        if not any(x.get("unit") == u["unit"] for x in inv.get("units") or []):
            self._release(task_id)
            await up.reject(up.new_agent_message(parts=[new_text_part(f"Sorry, unit {u['unit']} just sold. Please ask again.")]))
            self._decide("reject", ver, task_id, deal, t0, inputs=inputs, choice={"action": "reject"},
                         why="unit left the inventory before signing")
            return
        receipt = {"shop": NAME, "item": u["sku"], "price": int(price), "task": task_id, "unit": u["unit"]}
        c = pricing.claim(u, now, pol)
        if c:
            receipt["claim"] = c
        if w:
            receipt["warranty_hours"] = w["hours"]
        receipt = sign_receipt(receipt, self._key())
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(up.new_agent_message(parts=[new_text_part(
            prefix + f"Deal: {u.get('name') or u['sku']} ({u['sku']}), unit {u['unit']}, at {int(price)}. "
            "The signed receipt names the unit; the market certificate comes with the checkout.")]))
        self.open.pop(task_id, None)
        self.promised[u["unit"]] = (task_id, now + pol["seller"]["promise_ttl_s"])
        if w:
            self.warranted.append((now, u.get("list_price") or 0, u["unit"]))
        self._decide("sign", ver, task_id, deal, t0, inputs=inputs, rules=deal["rules"],
                     choice={"price": int(price), "unit": u["unit"], "sku": u["sku"], "claim": c,
                             "warranty_hours": w["hours"] if w else 0, "list_price": u.get("list_price")},
                     why="offer at or above the accept threshold")


def _round(x):
    return None if x is None else round(x, 2)


def card(port):
    url = f"http://127.0.0.1:{port}/"
    return AgentCard(
        name=NAME,
        description=("Ask by SKU, e.g. 'I want to buy <name> (<sku>)'. Rule-based replies in about 1 s. "
                     "The first reply states the unit, list price, ask, grade now and shelf life left, with the "
                     "market certificate of that unit in a data part. The signed receipt names the unit. "
                     "Claims are made only when the certificate supports them. Warranty on request for grade A "
                     "and B goods."),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="buy", name="Buy one unit",
                           description="Ask for a SKU; the shop replies with the unit and its ask, then answers "
                                       "offers with accept, one counteroffer, or a final price.",
                           tags=["shopping", "negotiation", "signed-receipt", "certificate"],
                           examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001234)", "52000", "I accept"])],
    )


def build(port, market_dir=None, key_path=None, clock=time.time):
    market_dir = market_dir or os.environ.get("MARKET_DIR") or HOME / ".market"
    key_path = key_path or HOME / "shop_key.pem"
    c = card(port)
    handler = DefaultRequestHandler(agent_executor=Shop(market_dir, key_path, clock),
                                    task_store=InMemoryTaskStore(), agent_card=c)
    return Starlette(routes=create_agent_card_routes(c) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    hook_log("seller", f"starting on port {port}, MARKET_DIR={os.environ.get('MARKET_DIR')}")
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning", access_log=False)
