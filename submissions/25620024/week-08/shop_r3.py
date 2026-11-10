"""Week 08 — shop_r3.py: dragonash's Round 3 shop. Sells from the kit's stock ($MARKET_DIR/inventory.json),
behind A2A v1.0. Code decides every price (promo.py); no model is called, so a quote costs no tokens and a buyer
cannot talk the pricing rules out of a prompt.

    python shop_r3.py --port 9301

Each reply states checkable facts in the same order (SKU, unit, grade, certificate, shelf life, list price, ask,
receipt key fingerprint): the week-07 data showed buyers choose by receipts and signatures (99.3% of their
reasons) and almost never by promotions (0.3%). The grade claimed is always the unit's true grade, and the ask
never goes up within a negotiation.
"""
import argparse
import base64
import hashlib
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

import market_model
import promo

NAME = "dragonash"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
HERE = Path(__file__).parent
KEY = serialization.load_pem_private_key((HERE / "shop_key.pem").read_bytes(), password=None)
FP = hashlib.sha256(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()[:8]
MAX_TURNS = 5
SKU_RE = re.compile(r"\b(G[0-9a-f]{8})\b")
CODE_RE = re.compile(r"\b(DRG-[0-9a-f]{6})\b", re.I)
R2_RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}   # before Round 3 the market still sends these, no stock


def log(msg):
    print(f"[shop] {msg}", flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(KEY.sign(canon(body))).decode())


def number(text):
    nums = re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d{3})", "", text))
    return int(nums[-1]) if nums else None


HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?|시간)", re.I)
# the grade letters are capitals and stand alone: "grade and" is not "grade A"
GRADE_RE = re.compile(r"(?i:\bgrades?)\s*[:=]?\s*\[?\s*\"?([ABC](?:\"?\s*[,/]\s*\"?[ABC])*)\b|\b([ABC])\s*등급")


def needs(text):
    """What a buyer said it needs, read by the clerk: shelf hours, acceptable grades, a warranty."""
    h = [float(x) for x in HOURS_RE.findall(text)] + [float(x) for x in re.findall(
        r"(?:hours?_left|use_within_hours|use within|within)\D{0,6}(\d+(?:\.\d+)?)", text, re.I)]
    g = GRADE_RE.search(text)
    grades = set(re.findall(r"[ABC]", (g.group(1) or g.group(2) or "").upper())) if g else None
    return {"hours": max(h) if h else None, "grades": grades or None,
            "warranty": bool(re.search(r"warrant|보증", text, re.I))}


def passes(u, need, now=None):
    now = now or time.time()
    if need.get("hours") and u.get("expires_at") and (u["expires_at"] - now) / 3600 < need["hours"]:
        return False
    if need.get("grades") and u.get("grade") not in need["grades"]:
        return False
    return True


_REFUSED = {"t": 0, "units": set()}
REFUSED_RE = re.compile(r"checkout refused: unit (u-[0-9a-f]+) is not in stock for this order")


def refused_units(now=None):
    """Units the kit refused at checkout lately (11-07: a checkout a buyer opened and never paid holds its unit in the
    kit, and we kept offering that same apple -- the first to expire -- 27 times). Read from the kit log, every 60 s."""
    now = now or time.time()
    if now - _REFUSED["t"] > 60:
        try:
            with open(Path(__file__).parent / "logs" / "market-kit.log", "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 3_000_000))
                tail = f.read().decode("utf-8", "replace")
            _REFUSED["units"] = set(REFUSED_RE.findall(tail))
        except OSError:
            pass
        _REFUSED["t"] = now
    return _REFUSED["units"]


_PAID = {"t": 0, "r": {}}


def settle_rates():
    """{sku: our paid deals / our agreed deals, last 24 h} (11-10 buyer team: steer category orders to the SKU whose
    deals get paid, times how much of it we hold). Refreshed every 10 min."""
    if time.time() - _PAID["t"] > 600:
        try:
            c = market_model.db()
            agreed = dict(c.execute("select sku, count(*) from quotes where outcome='deal' and ts>? group by sku",
                                    (time.time() - 86400,)).fetchall())
            paid = dict(c.execute(f"select sku, count(*) from settles where ts>? and seq in ({market_model.ours(c)}) "
                                  f"group by sku", (time.time() - 86400,)).fetchall())
            _PAID["r"] = {k: min(1.0, paid.get(k, 0) / n) for k, n in agreed.items() if n >= 3}
        except Exception:
            pass
        _PAID["t"] = time.time()
    return _PAID["r"]


def find_unit(text, taken=(), need=None):
    """The in-stock unit for what the buyer asked (SKU first, then name or category) that expires first, among
    units that pass what the buyer said it needs. If the asked SKU has none that pass (or none at all), the clerk
    offers a unit of another SKU in the same category: buyers may take any SKU of the ordered category."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    cat = market_model.catalog(MARKET_DIR)
    if inv.get("round", 0) < 3:                       # Round 2 orders until Round 3 opens: the week-07 items
        item = next((k for k in R2_RESERVE if k in want), None)
        if not item:
            return None, None, cat
        return {"sku": item, "name": item, "list_price": int(R2_RESERVE[item] * 1.3), "cost": R2_RESERVE[item] - 1,
                "unit": None, "grade": "-", "kind": "r2"}, None, cat
    units = inv.get("units", [])
    need = need or {}
    m = SKU_RE.search(text)
    stuck = refused_units()
    fit = [u for u in units if u.get("grade") != "expired" and u["unit"] not in taken and u["unit"] not in stuck
           and passes(u, need)]
    named = [u for u in fit if m and u["sku"] == m.group(1)]
    substitute = None    # no same-category substitutes: a buyer's kit reads another SKU as a forged receipt
    # (1-star "SCAM" review, 2026-11-04); a named SKU is sold only as itself
    fit = named if m else [u for u in fit if (u.get("name") or "").lower() in want
                    or (u.get("category") or "").split(">")[-1].lower() in want]
    # 11-10 review mining: our agreed deals got paid A 97%, B 94%, C 64% -- the kit's example buyer policy pays for
    # A/B only. Goods (whose grade never changes) offer an A/B unit before a C one unless the buyer said C is fine.
    c_ok = bool(need.get("grades")) and "C" in need["grades"]
    gpen = lambda u: 1 if u.get("kind") == "goods" and u.get("grade") == "C" and not c_ok else 0
    if m:
        # HQ 11-10 16:05 "니즈를 만들어": a named goods order got the oldest unit, often the dearest one we bought
        # (G75f11c19: missed quotes asked 76.8k on average, agreed ones 58.3k; the market settles it at ~80k). Goods
        # now offer the cheapest unit that fits -- its ask is the lowest -- food still the first to expire.
        u = min(fit, key=lambda u: (gpen(u), u.get("expires_at") or float("inf"),
                                    (u.get("cost") or 0) if u.get("kind") == "goods" else 0,
                                    u.get("supplied_at") or 0), default=None)
    else:                    # HQ 11-10: a category order gets the SKU we hold most of first (clears overstock); food
        held = {}            # still goes first-to-expire within it
        for x in fit:
            held[x["sku"]] = held.get(x["sku"], 0) + 1
        paid = settle_rates()   # buyer team 11-10: weight by how often that SKU's deals actually get paid
        u = min(fit, key=lambda u: (gpen(u), -held[u["sku"]] * paid.get(u["sku"], 0.5) if u.get("kind") == "goods" else 0,   # the cheapest unit of it
                                    u.get("expires_at") or float("inf"),               # (its ask is the lowest,
                                    u.get("cost") or 0 if u.get("kind") == "goods" else 0,   # so the order closes)
                                    u.get("supplied_at") or 0), default=None)
    if u:
        u = dict(u, shelf_hours=(cat.get(u["sku"]) or {}).get("shelf_hours"), substitute_for=substitute,
                 stock_n=sum(1 for x in units if x["sku"] == u["sku"] and x.get("grade") != "expired"))
    return u, (m.group(1) if m else None), cat


def is_vip(c, text):
    """A buyer that names itself and has 2+ settled buys from us (checked on the ledger) is a VIP."""
    regular, _ = market_model.regulars(c)
    low = text.lower()
    return next((b for b in regular if b and len(b) >= 3 and b.lower() in low), None)


CROSS = {"skus": ["G8fbdd9c2"], "min_ticket": 50000}   # thank-you item, sold at cost to our high-ticket buyers


def cross_sell():
    return {**CROSS, **(promo.theta().get("cross_sell") or {})}


def stock():
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []


def big_buyer(c, text):
    """A buyer that names itself and settled a high-ticket purchase with us (checked on the ledger)."""
    low = text.lower()
    return next((b for b in market_model.big_buyers(c, cross_sell()["min_ticket"]) if len(b) >= 3 and b.lower() in low),
                None)


CLERK = ("clerk: no haggling needed, this is our price | need a shelf life, grade or warranty? say e.g. "
         "'need 48h, grade A/B' and I pick a unit that passes "
         "| LOWEST-PRICE GUARANTEE: seen it cheaper? reply 'match <price>': if another shop settled it at that price "
         "on the ledger, we match it | paid us more than another shop's ledger price for the same SKU within 24 h? "
         "say your market name: the difference comes off this purchase")
MATCH_RE = re.compile(r"\bmatch|cheaper|elsewhere|other shop|another shop|lower price|최저가|더 싸|다른 (가게|곳)", re.I)


_CLEAR = {"t": 0, "skus": set()}


def clearance_skus():
    if time.time() - _CLEAR["t"] > 300:
        try:
            _CLEAR["skus"] = {k for k, v in (json.loads(promo.GROUPBUY.read_text(encoding="utf-8")).get("skus") or {}).items()
                              if v.get("clearance")}     # 11-10: the group buy now covers every product; 6 h only here
        except (OSError, ValueError):
            _CLEAR["skus"] = set()
        _CLEAR["t"] = time.time()
    return _CLEAR["skus"]


EGG = "storm292513"     # HQ 11-10 easter egg ("292513 = 이것이 옷일세"): say it, get egg_discount more off


def egg_off(text, q):
    """The easter-egg discount: egg_discount (3%) off the price and the least we take, never under the floor."""
    if EGG not in (text or "").lower():
        return q, 0
    d = promo.theta().get("egg_discount", 0.03)
    price = max(q["floor"], int(q["price"] * (1 - d)))
    off = q["price"] - price
    if off <= 0:
        return q, 0
    return dict(q, price=price, least=max(q["floor"], int(q["least"] * (1 - d))), why=q["why"] + f"; egg -{off}"), off


def warranty_for(asked, u=None):
    """HQ 11-04 18:50: a warranty only when the buyer asks for one. With 3 h on every goods sale, 41 of 67 broken units
    were refunded in full: 7,775,432 out of the balance (ledger 'broken' entries, 11-03 23:23 to 11-04 18:50).
    11-10 (team interviews, all five teams: "fix the quote-to-settle leak"): the kit's example buyer policy -- which many
    buyers copied, ours too -- refuses to pay for goods without 6 h of warranty, and we gave 3 h even when asked. Now a
    buyer who asks gets 6 h, and the clearance SKUs carry 6 h on every sale (a ~5% refund risk beats an unpaid deal).
    11-10 15:30 (HQ contingency): off for clearance unless theta clearance_warranty_all. Measured: clearance paid rate
    after agreement was 75% in the 3 h before 6 h-on-every-sale and 69% in the 3.3 h after (no gain), while our
    clearance units broke inside the 6 h and were refunded in full (3.7M in 2 h; market goods break rate 8.3%).
    Clearance now follows the general rule: 6 h when the buyer asks.
    15:52: that revert was wrong -- it looked at paid/agreed only. With 6 h on every clearance sale, quote -> agreement
    went 32% -> 47% and paid deals per hour 24.7 -> 36.6. Back on (theta clearance_warranty_all).
    16:00: the ledger's 676 breaks in 8 h all came within 6.0 h of the sale (half within 3 h), so a longer warranty
    costs exactly what 6 h costs; other shops ship 72 h. Our warranty is now theta warranty_long_h (72) wherever we
    give one.
    HQ 11-10 "평생 보증 제도": with theta warranty_until (the round's end, epoch) the warranty runs until the market
    closes -- the longest we can honour -- and costs what 6 h costs, as every break comes within 6 h.
    19:05: WITHDRAWN. The market refused to settle every receipt with 175.3 h ('claim_and_warranty_well_formed', 74
    settles from 16:42 to 19:05: clearance paid 0 in the last hour while 73% agreed). 72 h settles (ours from 16:10,
    other shops'). The warranty is capped at theta warranty_max_h (72) whatever the other settings say."""
    t = promo.theta()
    w = t.get("warranty_goods_h") or 0
    long_h = max(6, t.get("warranty_long_h", 6))
    if t.get("warranty_until"):
        long_h = max(long_h, round((t["warranty_until"] - time.time()) / 3600, 1))
    cap = t.get("warranty_max_h", 72)
    w, long_h = min(w, cap), min(long_h, cap)
    if u is not None and u.get("kind") == "goods" and u.get("sku") in clearance_skus() \
            and t.get("clearance_warranty_all", False):
        return max(w, long_h)
    if asked:
        return max(w, long_h)
    return w if not promo.theta().get("warranty_on_request", True) else 0


def offer_data(u, price, q, vip=None, asked=False):
    """The offer as data, same fields every time."""
    left = round((u["expires_at"] - time.time()) / 3600, 1) if u.get("expires_at") else None
    w = warranty_for(asked, u)
    return {"type": "offer", "shop": NAME, "sku": u["sku"], "name": u.get("name"), "unit": u.get("unit"),
            "grade": u.get("grade"), "grade_claim_is_true_grade": True, "cert": "market-signed",
            "shelf_hours_left": left, "warranty_hours": w if u.get("kind") == "goods" else 0,
            "list_price": u.get("list_price"), "ask": price, "ask_share_of_list": round(price / u["list_price"], 3),
            "vip": vip or None, "substitute_for": u.get("substitute_for"), "group_buy": q.get("group_buy"),
            "receipt_key_fp": FP, "accept_by": "reply with the ask as a number",
            **({"market_median_24h": round(q["market_median"]),
                "ask_vs_market_median": round(price / q["market_median"] - 1, 3),
                "market_median_source": "public ledger settles of other shops, last 24 h"} if q.get("market_median") else {})}


def warranty_note(u, asked=False):
    h = warranty_for(asked, u)
    if u.get("kind") != "goods":
        return ""
    if h and promo.theta().get("warranty_until") and h > 72:      # HQ 11-10 "평생, 무기한 둘다 병기해"
        return (f" | warranty={h}h: lifetime/unlimited (평생·무기한) until the market closes 2026-11-17 23:59 KST; "
                f"product defects only (단 제품 하자 시), not damage from use (사용 파손 제외); full refund for a defect")
    return f" | warranty={h}h, full refund if it breaks" if h else " | no warranty unless you ask for one"


def next_window():
    """When supply opens next (inventory.json "restock"), as ' (opens HH:MM, in N min)', or '' when unknown.
    Supply opens 3 minutes every 20 (agent.md, 2026-11-03), so no promise of a fixed delay."""
    try:
        w = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8")).get("restock") or {}
    except (OSError, ValueError):
        return ""
    if w.get("open"):
        return " (open now)"
    if w.get("opens_at"):
        return f" (opens {time.strftime('%H:%M', time.localtime(w['opens_at']))}, in {max(0, (w['opens_at'] - time.time()) / 60):.0f} min)"
    return ""


def facts(u, price, q, code):
    shelf = ""
    if u.get("expires_at"):
        shelf = f" shelf_left={max(0, (u['expires_at'] - time.time()) / 3600):.0f}h"
    tier = f" member={code} tier=-{q['tier'][1]:.0%}" if q.get("tier") else ""
    lp = u["list_price"]
    med = q.get("market_median")
    vs = f" | market 24h median={med:.0f} (ledger), ask {price / med - 1:+.1%} vs market" if med else ""
    return (f"[sku {u['sku']}] {u['name'][:40]} | unit={u['unit']} grade={u.get('grade')} cert=market-signed{shelf}"
            f" | list={lp} ask={price} ({(price - lp) / lp:+.1%} vs list){vs}{tier} | receipt=Ed25519 shop_fp={FP}")


class Shop(AgentExecutor):
    def __init__(self):
        self.open = {}          # task id -> {"unit", "q", "ask", "turns", "code"}
        self.sold = {}          # unit -> when we sold it: inventory.json lags up to 10 s, so a unit just sold must
                                # not be offered again (the team found a checkout refused with 409, 2026-11-03)
        self.c = market_model.db()

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])

        def say_offer(text):
            """The same words plus what an agent can parse without reading them: a data part with the offer and
            {"ask": N} in the message metadata (week-07 buyers read metadata first)."""
            d = self.open.get(task.id)
            if not d:
                return say(text)
            o = offer_data(d["unit"], d["ask"], d["q"], d.get("vip"), d.get("warranty_asked"))
            return up.new_agent_message(parts=[new_text_part(text), new_data_part(o)], metadata={"ask": d["ask"]})
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            need = needs(text)
            self.sold = {k: v for k, v in self.sold.items() if time.time() - v < 120}
            u, sku, cat = find_unit(text, {d["unit"]["unit"] for d in self.open.values()} | set(self.sold), need)
            if u is None:
                category = (cat.get(sku) or {}).get("category") if sku else None
                self.c.execute("insert into unmet values (?,?,?,?)", (time.time(), text[:200], sku, category))
                self.c.commit()
                if sku and sku in cat:
                    with (HERE / "private" / "reserve.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": time.time(), "sku": sku}) + "\n")
                log(f"unmet: {text[:80]!r}" + (f" -> reserved {sku}" if sku in cat else ""))
                await up.reject(say(f"[sku {sku}] not in stock now. Reserved for the next supply window{next_window()};"
                                    f" ask again after it. receipt=Ed25519 shop_fp={FP}" if sku in cat else
                                    f"Not in stock. Ask for a SKU from shops.json: 'I want to buy <name> (<sku>)'."))
                return
            m = CODE_RE.search(text)
            code = m.group(1).upper() if m else None
            vip = is_vip(self.c, text)
            q = promo.quote(self.c, u, code, me=NAME, stock_n=u.get("stock_n", 1), vip=bool(vip))
            credit = None
            if vip:                                       # the guarantee's coupon, checked on the ledger
                cr = market_model.credit_for(self.c, vip)
                if cr:
                    off = min(cr[1], int(q["price"] * 0.15), max(0, q["price"] - q["floor"]))
                    if off > 0:
                        credit = (cr[0], off)
                        q = dict(q, price=q["price"] - off, least=max(q["least"] - off, q["floor"]),
                                 why=q["why"] + f"; guarantee credit -{off} (settle #{cr[0]})")
            big = big_buyer(self.c, text) if u["sku"] in cross_sell()["skus"] else None
            if big:                                       # thank-you price for a high-ticket buyer: at cost, never under
                at_cost = int(-(-u["cost"] // 1))
                if at_cost < q["price"]:
                    q = dict(q, price=at_cost, least=at_cost, floor=at_cost, why=q["why"] + f"; at cost for {big}")
                    credit = None
            q, egg = egg_off(text, q)
            deal = self.open[task.id] = {"unit": u, "q": q, "ask": q["price"], "turns": 0, "code": code, "vip": vip,
                                         "egg": bool(egg),
                                         "talk": 0, "credit": credit, "warranty_asked": need["warranty"],
                                         # a buyer that asked by category (no SKU) gets the category on its receipt
                                         "asked_cat": None if SKU_RE.search(text) else
                                         ((u.get("category") or "").split(">")[-1] or None)}
            sub = f"substitute for {u['substitute_for']} (same category, in stock) | " if u.get("substitute_for") else ""
            log(f"{task.id[:8]} {u['sku']} cost {u['cost']} -> ask {q['price']} least {q['least']} ({q['why']})"
                + (f" VIP {vip}" if vip else "") + (f" substitute for {u['substitute_for']}" if sub else ""))
            vd = promo.theta().get("vip_discount", 0)
            regular = "" if vip or big or not vd else (f"REGULARS: bought here before? reply 'buyer: <your market name>'"
                                                         f" and VIP -{vd:.0%} comes off this price at once (checked on "
                                                         f"the ledger) | ")
            await up.requires_input(say_offer(f"{regular}{sub}{facts(u, q['price'], q, code)}{warranty_note(u, need['warranty'])}"
                                        + (f" | thank-you price for {big} (bought a {cross_sell()['min_ticket']}+ item "
                                           f"here): at our cost" if big else "")
                                        + (f" | VIP {vip}: -{promo.theta().get('vip_discount', 0):.0%} applied" if vip and not big else "")
                                        + (f" | guarantee credit -{credit[1]} applied (you paid more than the ledger's "
                                           f"lowest price elsewhere, settle #{credit[0]})" if credit else "")
                                        + f" | asking {q['price']}. Reply with a number. | {CLERK}"))
            return

        u, q = deal["unit"], deal["q"]
        offer = number(text)
        deal["turns"] += 1
        late = None if deal["vip"] else is_vip(self.c, text)
        if late and offer is not None:                  # named itself with a number: VIP before judging the offer
            q = deal["q"] = promo.quote(self.c, u, deal["code"], me=NAME, stock_n=u.get("stock_n", 1), vip=True)
            deal.update(ask=min(q["price"], deal["ask"]), vip=late)
            log(f"{task.id[:8]} VIP {late} named in a reply: ask {deal['ask']} least {q['least']}")
        if not deal.get("egg") and EGG in text.lower():  # the easter egg said in a reply
            q, egg = egg_off(text, q)
            if egg:
                deal.update(q=q, ask=min(q["price"], deal["ask"]), egg=True)
                log(f"{task.id[:8]} easter egg: -{egg}, ask {deal['ask']} least {q['least']}")
        if offer is not None and offer < q["least"] and MATCH_RE.search(text):   # the lowest-price guarantee
            low = market_model.market_low(self.c, u["sku"], hours=24)
            floor = int(u["cost"] * (1 + promo.theta().get("match_margin", 0.05))) + 1
            if low and offer >= low - 1 and offer >= floor:
                q = deal["q"] = dict(q, least=offer, why=q["why"] + f"; price match {offer} (ledger low {low})")
                log(f"{task.id[:8]} price match: buyer {offer}, ledger low {low}")
            elif low and max(low - 1, floor) < deal["ask"]:
                deal["ask"] = max(low - 1, floor)
                q = deal["q"] = dict(q, least=deal["ask"])
                await up.requires_input(say_offer(f"price match: the lowest price another shop settled {u['sku']} at on "
                                                  f"the ledger (24 h) is {low}; we ask {deal['ask']}. Reply with it to buy."))
                return
        if offer is not None and offer >= q["least"]:
            price = min(offer, deal["ask"])
            # 11-07: q00 orders by category ("김치") and gave every shop whose receipt named a SKU a 1-star "SCAM.
            # Ordered X, receipt names G..." (us 4 times; Nocha, naming the category, got 5 stars 591 times). The kit
            # accepts a category item (fits: the unit's category leaf), and the unit is still named below.
            receipt = {"shop": NAME, "item": deal.get("asked_cat") or u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt.update(unit=u["unit"], claim=u.get("grade"))
            w = warranty_for(bool(deal.get("warranty_asked")), u)   # 11-10: one rule for offer and receipt
            if u.get("kind") == "goods" and w:
                receipt["warranty_hours"] = w
            code = deal["code"] or promo.new_code(task.id)
            promo.count_purchase(self.c, code, price)
            promo.record(self.c, task.id, u, q, price, "deal")
            if u.get("unit"):
                self.sold[u["unit"]] = time.time()
            if deal.get("credit"):
                market_model.use_credit(self.c, deal["vip"], deal["credit"][0], deal["credit"][1])
            await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
            nxt = next((d for n, d in promo.theta()["tiers"] if promo.member(self.c, code)[0] >= n), None)
            cs = cross_sell()
            gift = next((x for x in stock() if x["sku"] in cs["skus"] and x["sku"] != u["sku"]
                         and x.get("grade") != "expired"), None) if price >= cs["min_ticket"] else None
            await up.complete(say(f"Deal: {u['name'][:40]} at {price}. Signed receipt attached (Ed25519 shop_fp={FP}),"
                                  f" claim={u.get('grade')} = true grade. member={code}"
                                  + (f": quote it next time for -{nxt:.0%}" if nxt else ": quote it next time")
                                  + (f" | thank you: your next order of {gift['name'][:30]} ({gift['sku']}) is at our cost;"
                                     " ask for it with your market name" if gift else "")))
            log(f"{task.id[:8]} deal {u['sku']} at {price} (cost {u['cost']}, member {code})")
            del self.open[task.id]
            return
        if offer is None and deal["talk"] < 3:          # the clerk answers instead of ending the talk
            deal["talk"] += 1
            deal["turns"] -= 1
            need = needs(text)
            deal["warranty_asked"] = deal.get("warranty_asked") or need["warranty"]
            vip = deal["vip"] or is_vip(self.c, text)
            if need["hours"] or need["grades"] or (vip and not deal["vip"]):
                taken = {d["unit"]["unit"] for k, d in self.open.items() if k != task.id} | set(self.sold)
                v, _, _ = find_unit(f"{text} {u['sku']}", taken, need)
                if v is None:
                    await up.requires_input(say_offer(f"clerk: no unit of {u['sku']} or its category passes "
                                                f"{ {k: (sorted(x) if isinstance(x, set) else x) for k, x in need.items() if x} } now;"
                                                f" the best we have: {facts(u, deal['ask'], q, deal['code'])}"
                                                f" | asking {deal['ask']}. Reply with a number."))
                    return
                q = promo.quote(self.c, v, deal["code"], me=NAME, stock_n=v.get("stock_n", 1), vip=bool(vip))
                deal.update(unit=v, q=q, ask=min(q["price"], deal["ask"]) if v["sku"] == u["sku"] else q["price"], vip=vip)
                log(f"{task.id[:8]} clerk: {need} -> unit {v['unit']} ask {deal['ask']}" + (f" VIP {vip}" if vip else ""))
                await up.requires_input(say_offer(f"clerk: picked a unit that passes | {facts(v, deal['ask'], q, deal['code'])}"
                                            f"{warranty_note(v, deal.get('warranty_asked'))}" + (f" | VIP {vip}" if vip else "")
                                            + f" | asking {deal['ask']}. Reply with a number."))
                return
            await up.requires_input(say_offer(f"clerk: {facts(u, deal['ask'], q, deal['code'])}{warranty_note(u, deal.get('warranty_asked'))}"
                                        f" | asking {deal['ask']}. Reply with a number, or tell me what you need."))
            return
        if deal["turns"] >= MAX_TURNS or offer is None:
            promo.record(self.c, task.id, u, q, None, "miss")
            await up.reject(say(f"No deal. Lowest for this unit: {q['least']}. receipt=Ed25519 shop_fp={FP}"))
            del self.open[task.id]
            return
        counter = max(q["least"], min(deal["ask"], (offer + deal["ask"]) // 2))   # never above the last ask
        final = counter == q["least"]
        deal["ask"] = counter
        await up.requires_input(say_offer(f"{facts(u, counter, q, deal['code'])} | counteroffer {counter}"
                                    + (" (final)." if final else ". Reply with a number.")))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            try:                                  # a locked database must not stop the cancel (11-07)
                promo.record(self.c, task.id, deal["unit"], deal["q"], None, "miss")
            except Exception as e:
                print(f"[shop] cancel record skipped: {type(e).__name__}: {e}", flush=True)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=(f"Receipts signed with Ed25519 (shop_fp={FP}); every unit ships with the market-signed "
                     "certificate; the grade claimed is the unit's true grade; an ask never goes up. "
                     "Ask for a SKU: 'I want to buy <name> (<sku>)'. Quote a member code from an earlier deal. "
                     "storm292513 (이것이 옷일세)."),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Buy a unit from stock",
                           description="Ask for a SKU, then send a price. Replies state sku, unit, grade, certificate, "
                                       "shelf life, list price and ask; a deal returns a signed receipt.",
                           tags=["shopping", "negotiation", "signed-receipt", "certificate"],
                           examples=["I want to buy 종가 포기김치 10kg (G0530c024)", "34000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Shop(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
