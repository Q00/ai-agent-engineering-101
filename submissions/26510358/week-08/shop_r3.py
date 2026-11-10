"""Round 3 shop: sells units from $MARKET_DIR/inventory.json over A2A v1.0 and signs receipts with shop_key.pem.

    python3 shop_r3.py --port 9301

Differences from the kit's starter (seller_r3.py):
  - prices come from memory: the market's median settled price for the SKU (else category) sets the first ask,
    and our own recent close rate nudges it (r3lib.pricing.unit_ask / ask_adjustment);
  - the floor follows the unit's situation: cost + margin normally, lower for food near its date and for any
    stock near the end of the round, because unsold stock is a total loss;
  - honest grade claims with the market's certificate, so claim_honest is always true on the ledger;
  - a warranty on goods whenever the market-wide break rate for that grade makes it cheap (memory.break_rate);
  - the unit shipped satisfies the shelf life the buyer asked for, when it says so.
Rounds before 3 have no stock: the week-07 items are sold at fixed reserves so the shop keeps trading.
"""
import argparse
import base64
import hashlib
import json
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

from r3lib import config, market, pricing, scout
from r3lib.memory import Memory

LEGACY = {"lamp": (100, 130), "chair": (180, 234), "bookshelf": (260, 338)}   # reserve, first ask (Round 0-2)
OPEN_TTL = 3 * 60   # an open negotiation holds its unit this long (15 min until 2026-11-07: 467 deals in 24 h all closed
                    #   within 97 s, p99 47 s, while 28 of 35 SKU requests turned away in 6 h were for a SKU quoted
                    #   to a buyer under 10 min earlier, i.e. held by a negotiation the buyer had walked away from)
SKU_RE = re.compile(r"\b([A-Z][0-9a-f]{6,}|[A-Z]\d{4,})\b")
CODE_RE = re.compile(re.escape(config.LOYALTY_PREFIX) + r"[0-9a-f]{8}\b", re.I)


def log(event, **kw):
    print(json.dumps({"event": event, "ts": round(time.time(), 1), **kw}, ensure_ascii=False), flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


_KEY = None


def sign(receipt):
    global _KEY
    if _KEY is None:
        _KEY = serialization.load_pem_private_key((config.ROOT / "shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(_KEY.sign(canon(body))).decode())


def member_code(task_id):
    """A member code bound to the deal's task and our key, so it cannot be guessed from the prefix."""
    return config.LOYALTY_PREFIX + hashlib.sha256(f"{task_id}:".encode() + _secret()).hexdigest()[:8]


_SECRET = None


def _secret():
    global _SECRET
    if _SECRET is None:
        _SECRET = hashlib.sha256((config.ROOT / "shop_key.pem").read_bytes()).digest()
    return _SECRET


def parse_offer(text, name=None):
    """The buyer's price: the last number in the message, ignoring the product name, SKUs, member codes, hours and
    kilograms. Without the name, '... 리튬 코인 배터리 CR1220 (G...)' read as an offer of 1220 and closed a deal on
    the opening message (loyalty smoke test, 2026-11-04); '20cm' or '85x200' in a name counted as low offers."""
    t = text or ""
    if name:
        t = t.replace(name, " ")
    t = CODE_RE.sub(" ", t)
    t = SKU_RE.sub(" ", t)
    t = re.sub(r"\d+(\.\d+)?\s*(h|hours?|시간|kg|g|ml|l|개|EA|ea)\b", " ", t, flags=re.I)
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", t)]
    return nums[-1] if nums else None


def wanted_hours(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)", text or "", re.I)
    return float(m.group(1)) if m else 0.0


def find_unit(text, taken):
    """In-stock unit for what the buyer asked (SKU first, else category or name). Among fitting units, the one
    that expires first while still lasting what the buyer asked for; goods: best grade first, then oldest."""
    inv = market.read_json("inventory.json", {}) or {}
    if (inv.get("round") or 0) < 3:
        want = (text or "").lower()
        item = next((k for k in LEGACY if k in want), None)
        return ({"legacy": True, "sku": item, "name": item, "unit": None, "cost": LEGACY[item][0],
                 "list_price": LEGACY[item][1], "kind": "goods"} if item else None), inv
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired" and u["unit"] not in taken]
    skus = set(SKU_RE.findall(text or ""))
    want = (text or "").lower()
    fit = [u for u in units if u["sku"] in skus]
    if not fit and not skus:
        fit = [u for u in units if market.leaf(u.get("category")).lower() in want
               or (u.get("name") or "").lower() in want]
    if not fit:
        return None, inv
    now, need = time.time(), wanted_hours(text)
    if fit[0].get("kind") == "food":
        shelf = (market.catalog().get(fit[0]["sku"]) or {}).get("shelf_hours") or 0
        # A buyer that states no need still has one: 0.25, 0.5 or 0.75 of the shelf life (2026-11-09: buyers that
        # never say it rated 38 short-lived units 2.0 against 4.7). Try to cover the longest first.
        needs = [need] if need or not shelf else [f * shelf for f in config.UNSTATED_NEED_SHARES]
        for n in needs:
            lasting = [u for u in fit if not u.get("expires_at") or (u["expires_at"] - now) / 3600 >= n + 1]
            if lasting:
                return min(lasting, key=lambda u: (u.get("expires_at") or float("inf"))), inv
        # Nothing lasts what the order needs (stated, or at least the smallest unstated share): a unit that does not
        # last is worth 0 to the buyer and earns 1-2 stars from accounts with few deals, which weigh a whole
        # account's mean (2026-11-10: buyer-01 3.80 / buyer-02 3.52 on "Only 35.4 h left; I needed 42.0 h").
        return None, inv
    return min(fit, key=lambda u: ("ABC".find(u.get("grade") or "C"), u.get("supplied_at") or 0)), inv


def requested_category(text):
    """(category, sku) a buyer asked for, from a SKU in the text or a category leaf name; (None, None) if unclear."""
    cat = market.catalog()
    for sku in SKU_RE.findall(text or ""):
        if sku in cat:
            return cat[sku]["category"], sku
    want = (text or "").lower()
    leaves = {}
    for i in cat.values():
        leaves.setdefault(market.leaf(i["category"]).lower(), i["category"])
    hits = [k for k in leaves if k and k in want]
    return (leaves[max(hits, key=len)], None) if hits else (None, None)


def buyer_style(text, unit):
    """A fingerprint of the buyer agent's opening message: its wording with the product, SKU, Korean words and
    numbers masked. Buyers do not tell the shop who they are, but each agent has its own template."""
    t = (text or "").lower()
    for word in (unit.get("name"), unit.get("sku")):
        if word:
            t = t.replace(str(word).lower(), " <item> ")
    t = SKU_RE.sub(" <sku> ", t)
    t = re.sub(r"[가-힣]+", " <k> ", t)
    t = re.sub(r"\d[\d,.]*", " # ", t)
    t = re.sub(r"\s+", " ", t).strip()[:200]
    return hashlib.sha1(t.encode()).hexdigest()[:12]


class Shop(AgentExecutor):
    def __init__(self, memory):
        self.mem = memory
        self.open = {}

    def _gc(self):
        for k in [k for k, d in self.open.items() if time.time() - d["t0"] > OPEN_TTL]:
            self._end(k, "timeout")

    def _end(self, task_id, outcome, price=None):
        d = self.open.pop(task_id, None)
        if d and not d["unit"].get("legacy"):
            try:
                self.mem.record_negotiation("shop", task_id, None, d["unit"]["sku"], d["first_ask"], price,
                                            d["offers"], outcome, style=d.get("style"))
            except Exception as e:                    # memory trouble must not lose a sale
                log("memory_error", error=str(e)[:200])

    def _terms(self, u):
        """First ask, floor and warranty for one unit, from memory."""
        if u.get("legacy"):
            return u["list_price"], u["cost"], 0
        ratio, n = self.mem.price_ratio(sku=u["sku"], category=u.get("category"))
        adj = pricing.ask_adjustment(self.mem.seller_outcomes(u.get("category")) or self.mem.seller_outcomes())
        try:
            premium = scout.ask_premium(self.mem, u.get("category"))
        except Exception as e:                        # scout trouble must not lose a sale
            premium = 0.0
            log("scout_error", error=str(e)[:200])
        adj += premium
        ask = pricing.unit_ask(u, ratio, adj)
        if u.get("kind") == "food" and not u.get("shelf_hours"):
            u = dict(u, shelf_hours=(market.catalog().get(u["sku"]) or {}).get("shelf_hours"))
        floor = pricing.unit_floor(u)
        p_break = self.mem.break_rate_for(u.get("grade"), u.get("category"))
        warranty = pricing.warranty_hours_for(u, ask, p_break)
        ask = pricing.warranty_ask(ask, u, warranty, p_break)
        floor = pricing.warranty_floor(floor, u, ask, warranty, p_break)
        grade_ratio, _ = self.mem.grade_price_ratio(u["sku"], u.get("grade"))
        ask = pricing.cap_ask(ask, u["list_price"], grade_ratio, floor)
        log("terms", sku=u["sku"], grade=u.get("grade"), cost=u["cost"], list_price=u["list_price"], ask=ask,
            floor=floor, market_ratio=ratio, grade_ratio=grade_ratio, evidence=n, adjust=adj, scarcity=premium, warranty=warranty,
            p_break=round(p_break, 4))
        return ask, floor, warranty

    def _pitch(self, u, ask, warranty, member=None):
        if u.get("legacy"):
            return f"{u['name']}: asking {ask}. Send your offer as a number."
        bits = [f"{u['name']} ({u['sku']})", f"grade {u.get('grade')} with the market's certificate"]
        if u.get("kind") == "food" and u.get("expires_at"):
            bits.append(f"{max(0, (u['expires_at'] - time.time()) / 3600):.0f} h of shelf life left")
        if warranty:
            bits.append(f"{warranty} h warranty")
        if member:
            bits.append(f"member price for {member}")
        return ", ".join(bits) + f". Asking {ask}. Send your offer as a number."

    async def execute(self, context, event_queue):
        self._gc()
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        d = self.open.get(task.id)

        if d is None:
            taken = {x["unit"]["unit"] for x in self.open.values() if x["unit"].get("unit")}
            u, _ = find_unit(text, taken)
            if u is None:
                log("no_stock", request=text[:120])
                try:
                    k, sku = requested_category(text)
                    if k:
                        self.mem.record_stockout(k, sku)
                except Exception as e:                # memory trouble must not lose a sale
                    log("memory_error", error=str(e)[:200])
                await up.reject(say("Sorry, none of that in stock."))
                return
            style = buyer_style(text, u)
            harsh = (self.mem.get("harsh_styles") or {}).get(style) if not u.get("legacy") else None
            if harsh:
                log("declined_harsh_style", style=style, ratings=harsh[0], mean_stars=harsh[1])
                await up.reject(say("Sorry, we cannot take this order."))
                return
            ask, floor, warranty = self._terms(u)
            member = None
            m = CODE_RE.search(text)
            if m and not u.get("legacy") and config.LOYALTY_ENABLED:
                code = config.LOYALTY_PREFIX + m.group(0)[len(config.LOYALTY_PREFIX):].lower()
                try:
                    if self.mem.redeem_code(code):
                        member = code
                        ask = max(floor, int(ask * (1 - config.LOYALTY_DISCOUNT)))
                        log("loyalty", code=member, sku=u["sku"], ask=ask, floor=floor)
                except Exception as e:                # memory trouble must not lose a sale
                    log("memory_error", error=str(e)[:200])
            hold = None
            try:
                prof = self.mem.buyer_style_profile(style)
                if prof["deals"] >= 3 and prof["close_ratio"] and not u.get("legacy"):
                    hold = max(floor, int(u["list_price"] * prof["close_ratio"] * 0.98))
                    log("returning_style", style=style, profile=prof, hold=hold)
            except Exception as e:                    # memory trouble must not lose a sale
                log("memory_error", error=str(e)[:200])
            d = self.open[task.id] = {"unit": u, "ask": ask, "floor": floor, "warranty": warranty, "first_ask": ask,
                                      "offers": [], "t0": time.time(), "style": style, "hold": hold,
                                      "member": member}
            offer = parse_offer(text, u.get("name"))
            if offer is None:
                await up.requires_input(say(self._pitch(u, ask, warranty, member)))
                return
        else:
            offer = parse_offer(text, d["unit"].get("name"))
        if offer is not None:
            d["offers"].append(offer)
        action, price = pricing.seller_reply(d["ask"], d["floor"], offer, len(d["offers"]), hold=d.get("hold"))
        u = d["unit"]
        if action == "accept":
            receipt = {"shop": config.SHOP_NAME, "item": u["sku"], "price": int(price), "task": task.id}
            if u.get("unit"):
                receipt.update(unit=u["unit"], claim=u.get("grade"))      # honest: the grade the certificate shows
                if d["warranty"]:
                    receipt["warranty_hours"] = d["warranty"]
            await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
            closing = f"Deal: {u['name']} at {price}."
            if not u.get("legacy") and config.LOYALTY_ENABLED:
                code = d.get("member") or member_code(task.id)
                try:
                    self.mem.issue_code(code, u["sku"], d.get("style"))
                    closing += (f" Member code {code}: quote it on your next order for "
                                f"{config.LOYALTY_DISCOUNT:.0%} off our first ask.")
                except Exception as e:                # memory trouble must not lose a sale
                    log("memory_error", error=str(e)[:200])
            await up.complete(say(closing))
            log("deal", sku=u["sku"], price=price, cost=u["cost"], ask=d["first_ask"], offers=d["offers"],
                warranty=d["warranty"])
            self._end(task.id, "deal", price)
            return
        if len(d["offers"]) >= 8:
            await up.reject(say(f"Final price was {d['ask']}. No deal."))
            self._end(task.id, "reject")
            return
        d["ask"] = price
        await up.requires_input(say(f"Counteroffer: {price}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self._end(task.id, "cancel")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=config.SHOP_NAME,
        description=("Shop with real stock. Every unit ships with the market's certificate and an honest grade claim; "
                     "goods carry a warranty when we can stand behind them. Ask for a product by name or SKU, "
                     "optionally with the shelf life you need (e.g. 'need 24 h'), then send a price."
                     + (" Every deal gives a member code; quote it on a later order for 3% off the first ask."
                        if config.LOYALTY_ENABLED else "")),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 포기김치 10kg (G00001), need 24 h", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Shop(Memory()), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    log("shop_started", name=config.SHOP_NAME, port=port)
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
