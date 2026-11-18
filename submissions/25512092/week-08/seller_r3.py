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

from harness import CMD, ask_json
from forum_post import promo
import decisions
from depts import sofa
from depts import receipt_exp  # 11/07: category receipt item, grade claim experiment
from depts import receipt_check  # 11/08: the receipt amount = the price the buyer agreed to
from depts import receipt_hold  # 11/08: how long a unit with a receipt stays held (receipt_hold.json)
from depts import high_ask  # 11/08 ~17:00: high-price first ask <= market average
from depts import subst_exp  # 11/08: substitute offers, option A experiment (subst_exp.json)
from depts import substitute  # 11/07: a named SKU I do not hold -> candidates, receipt only after a choice
from depts import open_expiry  # 11/09 ~11:30: a negotiation quiet for 10 min is closed as expired, its unit freed
from depts import nostock_cause  # 11/09 ~15:15: why "not in stock" for a held SKU (lock / receipt / policy / other)
from depts import b_warranty  # 11/09 ~11:30: B grade high-price warranty (TV / 냉방 on, 침대/매트리스 50% trial)
from depts import food_need  # 11/12 ~16:40: food only with the shelf life the buyer asked for (food_need.json)
WORDS = {48: "forty-eight", 72: "seventy-two"}
import strategy
from market_db import connect, load_stats, price_of
from pricing import floor
from depts.sales import first_ask  # noqa: E402  (sales department)

NAME = "Choo Shop"

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))

PROMPT = """You are the sales agent of a small online shop. A buyer is negotiating for one {name}.
The list price is {list_price}. Across the market it sells for {market} on average.
Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
Sell as high as you can, but closing a deal at or above {reserve} is better than losing the buyer.

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


def build_receipt(deal, u, price, task_id):
    """The receipt before signing (11/08: a function so tests can check the category-name receipt): item = the
    deal's receipt item (the order's category name in the category experiment and substitute arm A) or the SKU,
    the unit, the grade claim (claim arm, goods with a grade), the warranty."""
    receipt = {"shop": NAME, "item": deal.get("receipt_item") or u["sku"], "price": price, "task": task_id}
    if u["unit"]:
        receipt["unit"] = u["unit"]
    if deal.get("claim_arm") == "claim":            # 11/15 ~16:40 (person): decided now, food too (receipt_exp)
        g = receipt_exp.claim_now(u, time.time(), task=task_id)
        if g:
            receipt["claim"] = g
    if deal.get("warranty"):                        # [16] not under sig_seller; the kit puts it in the checkout
        receipt["warranty_hours"] = deal["warranty"]
    return receipt


def find_unit(text, taken=(), need_text=None):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation; 11/12 ~16:40: food only with the shelf life the buyer
    asked for in `need_text` (default `text`; depts/food_need.py, while food_need.json is on)."""
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
    n0 = len(fit)
    fit = food_need.keep(fit, need_text or text)          # 11/12 ~16:40: enough shelf life for this buyer
    if n0 and not fit:
        print(f"[food_need] {food_need.need_hours(need_text or text)} h asked: no unit with enough shelf life -> none",
              flush=True)
    named = [u for u in fit if u["sku"].lower() in want]
    fit = named or fit                                   # a SKU the buyer named comes first
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


def inventory_units():
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []


def record(task, deal, u, reserve, ask):
    try:
        db = connect()
        db.execute("INSERT OR REPLACE INTO shop_deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (task, __import__("time").time(), deal["case"], deal["key"], deal["variant"], u["sku"], u["unit"],
                    u["cost"], ask, reserve, "open", None, deal["warranty"]))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[record] {e}", flush=True)


def msg_log(task, who, text, sku=None):
    """Every message of a negotiation with its task id (decision 11/07 ~02:00: measure 'left after the first ask').
    Buyer text is stored as data only, never shown to a harness from here. market.db shop_msgs."""
    try:
        db = connect()
        db.execute("CREATE TABLE IF NOT EXISTS shop_msgs (task TEXT, ts REAL, who TEXT, sku TEXT, text TEXT)")
        db.execute("INSERT INTO shop_msgs VALUES (?,?,?,?,?)", (task, time.time(), who, sku, (text or "")[:500]))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[msg_log] {e}", flush=True)


HAGGLE_FLAG = Path(__file__).with_name("code_haggle.json")    # 11/07 experiment: {"enabled", "share", "since"}


# 11/07 ~15:45 (person's decision): in the code arm, a buyer message with a question about the unit (shelf life,
# warranty, certificate, grade, condition) goes to the harness, which can answer it; the code only repeated its counter
# (21 of 683 code-arm messages, 11:00-15:39). From code_haggle.json 'question_llm_since' (the 20:05 seller restart).
QUESTION = re.compile(r"\?|shelf|expir|warrant|certific|grade|condition|remaining|use within|how long|refund|"
                      r"유통|소비기한|보증|인증|증명|등급|상태|남은|환불|언제까지", re.I)


def question_to_llm(text):
    try:
        since = json.loads(HAGGLE_FLAG.read_text(encoding="utf-8")).get("question_llm_since")
    except (OSError, ValueError):
        return False
    return bool(since) and time.time() >= since and bool(QUESTION.search(text or ""))


def haggle_pick(unit=None):
    """'code' with the flag's share, else 'harness'; None while the experiment is off (read per negotiation).
    11/08 ~11:10 (person): with 'share_low' / 'share_high' in the flag, a unit whose cost is over qc.HIGH_MIN (100,000)
    uses share_high (0.3: the experiment goes on for high-price units), every other unit share_low (1.0: all code)."""
    try:
        j = json.loads(HAGGLE_FLAG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not j.get("enabled") or time.time() < (j.get("since") or 0):
        return None
    import random
    share = j.get("share", 0.3)
    if unit is not None and "share_low" in j:
        from depts.qc import HIGH_MIN
        share = j.get("share_high", share) if (unit.get("cost") or 0) > HIGH_MIN else j["share_low"]
    return "code" if random.random() < float(share) else "harness"


def haggle_log(task, deal, rule):
    """One row per finished negotiation: which haggling arm, how many harness calls it made (market.db shop_haggle)."""
    try:
        db = connect()
        db.execute("CREATE TABLE IF NOT EXISTS shop_haggle (task TEXT PRIMARY KEY, ts REAL, haggle TEXT, rule TEXT, "
                   "harness_calls INTEGER)")
        db.execute("INSERT OR REPLACE INTO shop_haggle VALUES (?,?,?,?,?)",
                   (task, time.time(), deal.get("haggle"), rule, deal.get("calls", 0)))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[haggle_log] {e}", flush=True)


def finish(task, outcome, price=None):
    try:
        db = connect()
        db.execute("UPDATE shop_deals SET outcome=?, price=? WHERE task=?", (outcome, price, task))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[record] {e}", flush=True)


CONC_FROM = __import__("datetime").datetime(2026, 11, 6, 22, 0).timestamp()   # person's decision: from 22:00
CONC_FLAG = Path(__file__).with_name("seller_concurrency.json")              # {"enabled": true, "limit": 3}
_sem = {}


def concurrency():
    """How many harness calls may run at once: 0 = the old way (one at a time, blocking), unless it is past
    CONC_FROM and seller_concurrency.json says enabled. Read on every call, so it can be switched off without a
    restart (delete the file or set enabled false)."""
    if time.time() < CONC_FROM:
        return 0
    try:
        j = json.loads(CONC_FLAG.read_text(encoding="utf-8"))
        return max(0, int(j.get("limit", 0))) if j.get("enabled") else 0
    except (OSError, ValueError):
        return 0


CODE_ACCEPT = 0.98           # person's decision 11/06: an offer this close to my last ask is accepted without the harness


def code_accept_on():
    """Code acceptance (saves harness calls): from CONC_FROM, while seller_concurrency.json has code_accept true."""
    if time.time() < CONC_FROM:
        return False
    try:
        return bool(json.loads(CONC_FLAG.read_text(encoding="utf-8")).get("code_accept"))
    except (OSError, ValueError):
        return False


async def harness_move(prompt):
    """The harness's next move. With concurrency on, the call runs in a worker thread (the event loop keeps
    serving other negotiations) and at most `limit` calls run at once (asyncio.Semaphore). Unit choice and the
    15-min receipt hold do not change: they happen between awaits, on the loop."""
    import asyncio
    lim = concurrency()
    if not lim:
        return ask_json(prompt)
    if _sem.get("limit") != lim:
        _sem.update(limit=lim, sem=asyncio.Semaphore(lim))
    async with _sem["sem"]:
        return await asyncio.to_thread(ask_json, prompt)


def recent_receipts(hold_s=900, negotiation_s=600):
    """Units my seller sent a receipt for lately, from shop_deals (outcome 'receipt'; ts is when the negotiation
    began, so the receipt went out within negotiation_s after it: counted as now, capped at ts + negotiation_s)."""
    try:
        db = connect()
        rows = db.execute("SELECT unit, ts FROM shop_deals WHERE outcome='receipt' AND unit IS NOT NULL AND ts>=?",
                          (time.time() - hold_s - negotiation_s,)).fetchall()
        db.close()
    except Exception as e:
        print(f"[record] restoring receipts failed: {e}", flush=True)
        return {}
    now = time.time()
    sold = {unit: min(now, ts + negotiation_s) for unit, ts in rows}
    print(f"[seller] {len(sold)} units with a receipt in the last {(hold_s + negotiation_s) // 60} min held back",
          flush=True)
    return sold


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines"}
        self.sold = recent_receipts()   # unit -> when its receipt went out: inventory.json can lag, so do not
                                        # offer it again; restored from shop_deals so a restart keeps the hold

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now0 = time.time()
        for tid in open_expiry.stale(self.open, now0):  # 11/09: quiet > 10 min -> expired, the unit is free again
            d = self.open.pop(tid)
            if d.get("unit"):
                finish(tid, "expired")
            msg_log(tid, "shop", f"expired: no message for {open_expiry.EXPIRE_S // 60} min", open_expiry.unit_of(d)[1])
            open_expiry.log_expired(tid, d, now0)
            print(f"[expired] {tid[:8]} {open_expiry.unit_of(d)}", flush=True)
        open_expiry.write_state(self.open, now0)
        deal = self.open.get(task.id)
        open_expiry.touch(deal, now0)

        if deal is None or deal.get("subst"):
            now = time.time()
            hold = receipt_hold.hold_s(now)         # 11/08 ~13:00: p99 of receipt -> payment, 3-15 min (was 15 min)
            self.sold = {k: t for k, t in self.sold.items() if now - t < hold}
            taken = {d["unit"]["unit"] for d in self.open.values() if d.get("unit") and d["unit"]["unit"]} \
                | set(self.sold)
            quoted = tier = subst_arm = n_off = need_text = None
            if deal is not None:                        # 11/07: waiting for the buyer to name one of my candidates
                pick = substitute.chosen(text, deal["subst"])
                if pick is None and deal.get("subst_arm") == "A":      # 11/08 experiment A: a number = an offer
                    n_off = subst_exp.number_offer(text)              # for the first candidate
                    if n_off is not None:
                        pick = next(iter(deal["subst"]))
                        subst_exp.log(task.id, "A", "number_reply", x=deal["x"], sku=pick, offer=n_off)
                        print(f"[subst A] {deal['x']}: number {n_off} -> offer for {pick}", flush=True)
                if pick is None:
                    msg_log(task.id, "buyer", text, deal["x"])
                    deal["tries"] += 1
                    if deal["tries"] >= 3:
                        msg_log(task.id, "shop", "subst no choice -> reject", deal["x"])
                        del self.open[task.id]
                        await up.reject(say("No deal."))
                        return
                    msg_log(task.id, "shop", "subst repeat", deal["x"])
                    await up.requires_input(say(f"{deal['x']} is not in stock. To buy an alternative, name its SKU: "
                                                + ", ".join(deal["subst"]) + "."))
                    return
                tier, quoted = deal["subst"][pick]
                need_text = deal["lines"][0]                # the first message carries the hours the buyer needs
                unit = find_unit(pick, taken, need_text)
                if unit is None or unit["sku"] != pick:
                    msg_log(task.id, "shop", f"subst {pick} gone -> reject", pick)
                    del self.open[task.id]
                    await up.reject(say(f"Sorry, {pick} has just sold out."))
                    return
                substitute.log_choice(connect, task.id, pick, tier)
                subst_arm = deal.get("subst_arm")
                subst_exp.log(task.id, subst_arm, "chosen", x=deal["x"], sku=pick, tier=tier, quoted=quoted)
                print(f"[subst] buyer chose {pick} (tier {tier}) instead of {deal['x']}, quoted {quoted}", flush=True)
            else:
                x = substitute.named_sku(text) if substitute.on() else None
                inv_units = inventory_units()
                cands = substitute.candidates(x, inv_units, taken) if x else None
                subst_arm = subst_exp.arm() if cands is not None else None
                why_ns = None
                if cands is not None:                   # 11/09 ~15:15: why "not in stock" for a SKU I may hold
                    try:
                        why_ns = nostock_cause.classify(x, inv_units, self.open, self.sold, now, open_expiry.expire_s())
                    except Exception as e:
                        print(f"[nostock] cause failed: {type(e).__name__}", flush=True)
                if subst_arm == "B" or (subst_arm == "A" and not subst_exp.near(cands or [])):
                    subst_exp.log(task.id, subst_arm, "no_offer", x=x, n=len(cands or []), cause=why_ns)
                    msg_log(task.id, "buyer", text, x)
                    msg_log(task.id, "shop", f"subst {subst_arm}: not in stock", x)
                    await up.reject(say(f"Sorry, {x} is not in stock."))
                    return
                if subst_arm == "A":
                    cands = subst_exp.near(cands)
                if cands is not None:                   # a named SKU I do not hold: offer candidates, no receipt yet
                    if not cands:
                        await up.reject(say(f"Sorry, we have no {x} and nothing else in its category in stock."))
                        return
                    case = strategy.current()[0]
                    offer, lines = {}, []
                    for t, c in cands:
                        avg, _ = price_of(c["sku"])
                        reserve, rule = floor(c, avg, case)
                        price = max(reserve, int(first_ask(c, avg, reserve, rule,
                                                           {"key": None, "variant": None, "case": case, "mult": 1.0})))
                        offer[c["sku"]] = (t, price)
                        lines.append(f"{len(lines) + 1}) {c['sku']} {c['name']}: {price}")
                    self.open[task.id] = {"subst": offer, "x": x, "tries": 0, "lines": [f"buyer: {text}"],
                                          "subst_arm": subst_arm}
                    substitute.log_offer(connect, task.id, x, [[k, v[0]] for k, v in offer.items()])
                    if subst_arm:
                        subst_exp.log(task.id, subst_arm, "offer", x=x, offered=[[k, v[0], v[1]] for k, v in offer.items()],
                                      cause=why_ns)
                    msg_log(task.id, "buyer", text, x)
                    msg_log(task.id, "shop", "subst " + ", ".join(f"{k} t{v[0]} {v[1]}" for k, v in offer.items()), x)
                    print(f"[subst] {x} not in stock -> offered {offer}", flush=True)
                    await up.requires_input(say(
                        f"{x} is not in stock. Alternatives in the same category: " + "; ".join(lines)
                        + ". To buy one, name its SKU (accept it or make an offer for it)."))
                    return
                unit = find_unit(text, taken)
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            case = strategy.current()[0]                # decided every 5 min by restock.py (strategy.tick)
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "lines": [f"buyer: {text}"],
                                         "case": case, "warranty": strategy.warranty_hours(unit, case, load_stats()),
                                         "key": None, "variant": None, "mult": 1.0,
                                         "haggle": haggle_pick(unit), "calls": 0,      # 11/07: code vs harness arm
                                         "quoted": quoted, "subst_tier": tier,
                                         "need_h": food_need.need_hours(need_text or text)}   # 11/12 ~16:40
            deal["receipt_item"] = None if quoted else receipt_exp.category_item(text, unit, substitute.named_sku(text))
            if quoted and subst_arm == "A":             # 11/08 experiment A: the order's category name on the receipt
                deal["receipt_item"] = subst_exp.category_name(unit)
                deal["subst_arm"] = "A"
            elif quoted and subst_arm:
                deal["subst_arm"] = subst_arm
            deal["claim_arm"] = receipt_exp.claim_arm(unit)
            deal["t"] = time.time()
            b_arm = b_warranty.arm(unit, case)          # 11/09: B grade high-price warranty, trial tag
            if b_arm:
                deal["b_warranty"] = b_arm
                b_warranty.log_deal(task.id, unit, b_arm, deal["warranty"])
            receipt_exp.log(connect, task.id, unit, deal["receipt_item"], deal["claim_arm"])
            if quoted:                                  # an offer in the choosing message counts (SKUs left out)
                deal["offer"] = receipt_check.first_number(substitute.strip_skus(text))   # 11/08: "27,000" -> 27000
                if n_off is not None:                   # 11/08 A: the number reply, read whole ("10,400")
                    deal["offer"] = n_off
            if case in ("normal", "overstock"):         # stage 2: one variant per negotiation, by weight
                key = "shop_normal_ask" if case == "normal" else "shop_overstock_floor"
                v, w = strategy.pick(key)
                deal.update(key=key, variant=v, mult=float(v) if case == "overstock" else 1.0)
                print(f"[variant] {key} -> {v} (weights {w})", flush=True)
        else:
            deal["lines"].append(f"buyer: {text}")
            n = receipt_check.first_number(substitute.strip_skus(text))   # SKUs out: G200e39a3 is not 200;
            deal["offer"] = n if n is not None else deal["offer"]          # 11/08: "27,000" -> 27000 (was 27)

        u = deal["unit"]
        msg_log(task.id, "buyer", text, u["sku"])
        avg, n = price_of(u["sku"])                     # market average, refreshed every 5 min by restock.py
        reserve, rule = floor(u, avg, deal["case"], overstock_mult=deal["mult"])   # pricing.py, the case's rules
        reserve = high_ask.floor(u, avg, reserve, deal["case"], rule=rule)   # 11/08: cleared SKUs (never under
        #   cost), and from 20:02 the high-price floor max(cost x 1.05, median x 0.8) (not sofas; lower markdowns kept)
        deal["rule"] = rule
        q_llm = deal.get("haggle") == "code" and len(deal["lines"]) > 1 and question_to_llm(text)
        if q_llm:                                       # 11/07: a question in the code arm goes to the harness
            deal["q_llm"] = deal.get("q_llm", 0) + 1
            print(f"[code haggle] {u['sku']} question in the buyer message -> harness", flush=True)
        if len(deal["lines"]) == 1:
            ask = first_ask(u, avg, reserve, rule, deal)    # stage 2: the code names the first ask, not the harness
            ask, why_hi = high_ask.first_ask(u, avg, reserve, ask)   # 11/08 ~17:00: high price <= market average
            if why_hi:
                print(f"[high ask] {u['sku']} first ask -> {ask} ({why_hi})", flush=True)
            if deal.get("quoted"):                          # 11/07: a chosen substitute starts at the price I quoted
                ask = max(reserve, deal["quoted"])
            print(f"[price] {u['sku']} cost {u['cost']}, market avg {avg} (n={n}), case {deal['case']}, rule {rule} "
                  f"-> floor {reserve}, first ask {ask}" + (f", warranty {deal['warranty']} h" if deal["warranty"] else ""),
                  flush=True)
            record(task.id, deal, u, reserve, ask)
            decisions.record("sales", "first_ask", u["sku"], ask, f"unit {u['unit']}, cost {u['cost']}, avg {avg}, "
                             f"case {deal['case']}, rule {rule}, floor {reserve}, variant {deal['key']} {deal['variant']}"
                             + (f", warranty {deal['warranty']} h" if deal["warranty"] else ""))
            deal["first_ask"] = ask
            move, raw = {"action": "counter", "price": ask}, "(first ask by code)"
            if deal.get("quoted") and deal["offer"] is not None and deal["offer"] >= ask:   # 11/07: chose a
                move, raw = {"action": "accept"}, f"(substitute: offer {deal['offer']} >= quoted {ask})"  # substitute
                print(f"[subst] {u['sku']} offer {deal['offer']} >= quoted {ask} -> accept", flush=True)  # at >= quote
        elif rule == "sofa" or (deal.get("haggle") == "code" and not q_llm):   # haggling by code (depts/sofa.py D): sofas, and the
            nxt = sofa.next_counter(deal, reserve)             # 'code' arm of the 11/07 experiment; no harness call
            tag = "sofa" if rule == "sofa" else "code haggle"
            if deal["offer"] is not None and deal["offer"] >= max(reserve, nxt):
                move, raw = {"action": "accept"}, f"({tag}: offer {deal['offer']} >= next counter {nxt})"
            else:
                move, raw = {"action": "counter", "price": nxt}, f"({tag}: counter {nxt}, floor {reserve})"
            print(f"[{tag}] {u['sku']} offer {deal['offer']}, last ask {deal['ask']} -> {raw}", flush=True)
        elif code_accept_on() and deal["offer"] is not None and deal["ask"] \
                and deal["offer"] >= CODE_ACCEPT * deal["ask"] and deal["offer"] >= reserve:
            move, raw = {"action": "accept"}, f"(code: offer {deal['offer']} >= {CODE_ACCEPT:.0%} of ask {deal['ask']})"
            print(f"[code] accept {deal['offer']} >= {CODE_ACCEPT:.0%} of ask {deal['ask']}, no harness call", flush=True)
        else:
            try:
                move, raw = await harness_move(PROMPT.format(name=u["name"], list_price=u["list_price"],
                                                             reserve=reserve,
                                                             market=int(avg) if avg is not None else "an unknown price",
                                                             transcript="\n".join(deal["lines"])))
                deal["calls"] = deal.get("calls", 0) + 1
                print(f"[harness] {raw}", flush=True)
            except Exception as e:                      # 11/10 ~15:00 (person): LLM down (limit, timeout) -> code haggle
                nxt = sofa.next_counter(deal, reserve)
                if deal["offer"] is not None and deal["offer"] >= max(reserve, nxt):
                    move, raw = {"action": "accept"}, f"(harness failed {type(e).__name__}: code accept {deal['offer']})"
                else:
                    move, raw = {"action": "counter", "price": nxt}, (f"(harness failed {type(e).__name__}: code "
                                                                       f"counter {nxt}, floor {reserve})")
                print(f"[code fallback] {u['sku']} {raw}", flush=True)
        action = (move or {}).get("action")
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below floor {reserve} -> counter", flush=True)
            decisions.record("cs", "guard", u["sku"], deal["offer"], f"harness accepted under floor {reserve}",
                             limit_hit="floor")
            action, move = "counter", {"price": deal["ask"] or u["list_price"]}
        if action == "accept":                      # 11/08 ~11:20 (person): the receipt amount checked by code
            buyer_text = next((ln[7:] for ln in reversed(deal["lines"]) if ln.startswith("buyer: ")), "")
            agreed, why = receipt_check.agreed(buyer_text, deal["offer"], deal["ask"])
            if why:
                if agreed is None or agreed < reserve:  # the buyer's own offer is under the floor: no receipt
                    print(f"[receipt_fixed] {u['sku']} receipt {deal['offer']} != agreed {agreed} ({why}); under "
                          f"floor {reserve} -> counter", flush=True)
                    decisions.record("cs", "receipt_fixed", u["sku"], [deal["offer"], agreed],
                                     f"task {task.id}: {why}; under floor {reserve}: counter {deal['ask']}, no receipt")
                    action, move = "counter", {"price": deal["ask"] or u["list_price"]}
                else:
                    print(f"[receipt_fixed] {u['sku']} receipt {deal['offer']} -> {agreed} ({why})", flush=True)
                    decisions.record("cs", "receipt_fixed", u["sku"], [deal["offer"], agreed],
                                     f"task {task.id}: {why}; receipt issued at {agreed}")
                    deal["offer"] = agreed
        if action == "accept" and not food_need.fits(u, deal.get("need_h")):   # 11/12 ~16:40: checked again
            alt = next((x for x in inventory_units() if x["sku"] == u["sku"] and x.get("grade") == u.get("grade")
                        and x["unit"] != u["unit"] and x["unit"] not in self.sold
                        and x["unit"] not in {d["unit"]["unit"] for d in self.open.values() if d.get("unit")}
                        and food_need.fits(x, deal.get("need_h"))), None)
            left = food_need.left_h(u)
            if alt:
                print(f"[food_need] {u['sku']} unit {u['unit']} {left:.1f} h < {deal['need_h']} h -> unit {alt['unit']}",
                      flush=True)
                deal["unit"] = u = alt
            else:
                print(f"[food_need] {u['sku']} {left:.1f} h < {deal['need_h']} h asked, no other unit -> no receipt",
                      flush=True)
                decisions.record("cs", "food_need_refuse", u["sku"], [round(left or 0, 1), deal["need_h"]],
                                 f"task {task.id}: shelf life {left:.1f} h < {deal['need_h']} h asked: no receipt")
                msg_log(task.id, "shop", f"food_need refuse {left:.1f} h < {deal['need_h']} h", u["sku"])
                finish(task.id, "food_need")
                del self.open[task.id]
                await up.reject(say(f"Sorry, the {u['name']} we have left has only {left:.1f} h of shelf life, "
                                    f"less than the {deal['need_h']:g} h you need. No deal."))
                return
        if action == "accept":
            price = deal["offer"]
            receipt = sign(build_receipt(deal, u, price, task.id))
            if deal.get("subst_arm"):
                subst_exp.log(task.id, deal["subst_arm"], "receipt", sku=u["sku"], unit=u["unit"], price=price,
                              item=receipt["item"])
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            finish(task.id, "receipt", price)
            haggle_log(task.id, deal, rule)
            msg_log(task.id, "shop", f"accept {price}", u["sku"])
            decisions.record("cs", "receipt", u["sku"], price, f"unit {u['unit']}, floor {reserve}, offer accepted")
            if u["unit"]:
                self.sold[u["unit"]] = time.time()
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("No deal."))
            finish(task.id, "rejected")
            haggle_log(task.id, deal, rule)
            msg_log(task.id, "shop", "reject", u["sku"])
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = deal["ask"] or u["list_price"]      # unparsed answer: repeat the last ask
        if rule == "volume" and deal.get("first_ask"):  # volume pricing: never counter above the first ask
            price = min(price, deal["first_ask"])
        elif rule not in ("normal", "sofa") and avg is not None:   # any markdown: never ask above the market average
            price = min(price, max(reserve, int(avg)))
        if price < reserve:                             # the harness may forget the cost; the code does not
            print(f"[guard] ask {price} below floor {reserve} -> {reserve}", flush=True)
            price = reserve
        deal["ask"] = int(price)
        first = len(deal["lines"]) == 1
        if first:                                       # what a buyer's accept policy checks, before any offer
            facts = f" SKU {u['sku']}, grade {u['grade']}"
            if u.get("expires_at"):
                facts += f", {max(0, (u['expires_at'] - time.time()) / 3600):.1f} h shelf life left"
            facts += (f", {WORDS.get(deal['warranty'], format(deal['warranty'], 'g'))} hour warranty from our balance" if deal["warranty"] else ", no warranty")
            facts += "; the market certificate of this unit is in the data part."
            reply = f"{u['name']}: {promo()}{facts} Asking {deal['ask']}. Send your offer as a number."
        else:
            reply = f"Counteroffer: {deal['ask']}."
        deal["lines"].append(f"you: {reply}")
        msg_log(task.id, "shop", f"ask {deal['ask']}" + (" (first)" if first else ""), u["sku"])
        if first and u.get("cert"):                     # [C] the certificate goes with the first reply too
            await up.requires_input(up.new_agent_message(parts=[new_text_part(reply),
                                                                new_data_part({"unit": u["unit"], "cert": u["cert"]})]))
        else:
            await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        d = self.open.pop(task.id, None)
        if d is not None:
            finish(task.id, "cancelled")
            haggle_log(task.id, d, d.get("rule"))
            msg_log(task.id, "cancel", "")
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
