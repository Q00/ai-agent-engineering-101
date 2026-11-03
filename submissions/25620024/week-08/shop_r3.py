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
    fit = [u for u in units if u.get("grade") != "expired" and u["unit"] not in taken and passes(u, need)]
    named = [u for u in fit if m and u["sku"] == m.group(1)]
    substitute = None
    if not named and m and (cat.get(m.group(1)) or {}).get("category"):
        same = [u for u in fit if u.get("category") == cat[m.group(1)]["category"]]
        if same:
            named, substitute = same, m.group(1)
    fit = named or [u for u in fit if (u.get("name") or "").lower() in want
                    or (u.get("category") or "").split(">")[-1].lower() in want]
    u = min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0), default=None)
    if u:
        u = dict(u, shelf_hours=(cat.get(u["sku"]) or {}).get("shelf_hours"), substitute_for=substitute,
                 stock_n=sum(1 for x in units if x["sku"] == u["sku"] and x.get("grade") != "expired"))
    return u, (m.group(1) if m else None), cat


def is_vip(c, text):
    """A buyer that names itself and has 2+ settled buys from us (checked on the ledger) is a VIP."""
    regular, _ = market_model.regulars(c)
    low = text.lower()
    return next((b for b in regular if b and len(b) >= 3 and b.lower() in low), None)


CLERK = ("clerk: no haggling needed, this is our price | need a shelf life, grade or warranty? say e.g. "
         "'need 48h, grade A/B' and I pick a unit that passes | bought here before? say your market name for VIP")


def warranty_note(u):
    h = promo.theta().get("warranty_goods_h") or 0
    return f" | warranty={h}h, full refund if it breaks" if u.get("kind") == "goods" and h else ""


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
    return (f"[sku {u['sku']}] {u['name'][:40]} | unit={u['unit']} grade={u.get('grade')} cert=market-signed{shelf}"
            f" | list={lp} ask={price} ({(price - lp) / lp:+.1%} vs list){tier} | receipt=Ed25519 shop_fp={FP}")


class Shop(AgentExecutor):
    def __init__(self):
        self.open = {}          # task id -> {"unit", "q", "ask", "turns", "code"}
        self.c = market_model.db()

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
            need = needs(text)
            u, sku, cat = find_unit(text, {d["unit"]["unit"] for d in self.open.values()}, need)
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
            deal = self.open[task.id] = {"unit": u, "q": q, "ask": q["price"], "turns": 0, "code": code, "vip": vip,
                                         "talk": 0}
            sub = f"substitute for {u['substitute_for']} (same category, in stock) | " if u.get("substitute_for") else ""
            log(f"{task.id[:8]} {u['sku']} cost {u['cost']} -> ask {q['price']} least {q['least']} ({q['why']})"
                + (f" VIP {vip}" if vip else "") + (f" substitute for {u['substitute_for']}" if sub else ""))
            await up.requires_input(say(f"{sub}{facts(u, q['price'], q, code)}{warranty_note(u)}"
                                        + (f" | VIP {vip}: -{promo.theta().get('vip_discount', 0):.0%} applied" if vip else "")
                                        + f" | asking {q['price']}. Reply with a number. | {CLERK}"))
            return

        u, q = deal["unit"], deal["q"]
        offer = number(text)
        deal["turns"] += 1
        if offer is not None and offer >= q["least"]:
            price = min(offer, deal["ask"])
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt.update(unit=u["unit"], claim=u.get("grade"))
            if u.get("kind") == "goods" and promo.theta()["warranty_goods_h"]:
                receipt["warranty_hours"] = promo.theta()["warranty_goods_h"]
            code = deal["code"] or promo.new_code(task.id)
            promo.count_purchase(self.c, code, price)
            promo.record(self.c, task.id, u, q, price, "deal")
            await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
            nxt = next((d for n, d in promo.theta()["tiers"] if promo.member(self.c, code)[0] >= n), None)
            await up.complete(say(f"Deal: {u['name'][:40]} at {price}. Signed receipt attached (Ed25519 shop_fp={FP}),"
                                  f" claim={u.get('grade')} = true grade. member={code}"
                                  + (f": quote it next time for -{nxt:.0%}" if nxt else ": quote it next time")))
            log(f"{task.id[:8]} deal {u['sku']} at {price} (cost {u['cost']}, member {code})")
            del self.open[task.id]
            return
        if offer is None and deal["talk"] < 3:          # the clerk answers instead of ending the talk
            deal["talk"] += 1
            deal["turns"] -= 1
            need = needs(text)
            vip = deal["vip"] or is_vip(self.c, text)
            if need["hours"] or need["grades"] or (vip and not deal["vip"]):
                taken = {d["unit"]["unit"] for k, d in self.open.items() if k != task.id}
                v, _, _ = find_unit(f"{text} {u['sku']}", taken, need)
                if v is None:
                    await up.requires_input(say(f"clerk: no unit of {u['sku']} or its category passes "
                                                f"{ {k: (sorted(x) if isinstance(x, set) else x) for k, x in need.items() if x} } now;"
                                                f" the best we have: {facts(u, deal['ask'], q, deal['code'])}"
                                                f" | asking {deal['ask']}. Reply with a number."))
                    return
                q = promo.quote(self.c, v, deal["code"], me=NAME, stock_n=v.get("stock_n", 1), vip=bool(vip))
                deal.update(unit=v, q=q, ask=min(q["price"], deal["ask"]) if v["sku"] == u["sku"] else q["price"], vip=vip)
                log(f"{task.id[:8]} clerk: {need} -> unit {v['unit']} ask {deal['ask']}" + (f" VIP {vip}" if vip else ""))
                await up.requires_input(say(f"clerk: picked a unit that passes | {facts(v, deal['ask'], q, deal['code'])}"
                                            f"{warranty_note(v)}" + (f" | VIP {vip}" if vip else "")
                                            + f" | asking {deal['ask']}. Reply with a number."))
                return
            await up.requires_input(say(f"clerk: {facts(u, deal['ask'], q, deal['code'])}{warranty_note(u)}"
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
        await up.requires_input(say(f"{facts(u, counter, q, deal['code'])} | counteroffer {counter}"
                                    + (" (final)." if final else ". Reply with a number.")))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            promo.record(self.c, task.id, deal["unit"], deal["q"], None, "miss")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=(f"Receipts signed with Ed25519 (shop_fp={FP}); every unit ships with the market-signed "
                     "certificate; the grade claimed is the unit's true grade; an ask never goes up. "
                     "Ask for a SKU: 'I want to buy <name> (<sku>)'. Quote a member code from an earlier deal."),
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
