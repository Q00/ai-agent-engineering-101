"""R3 seller: A2A shop that sells units from $MARKET_DIR/inventory.json. Pure-code pricing from policy.json.

    python hooks/seller.py --port {port}

- Finds the unit for the buyer's request (SKU in the text first, else category leaf or name), skipping units
  promised in another open negotiation.
- Prices: ask from policy.ask_price, concessions from policy.respond; never below cost (policy.guard_price is the
  last check before signing). Price is an int when signed, so the kit's checkout (int only) and sig_seller agree.
- Receipt adds unit, honest claim (= the unit's grade), cert shown, warranty_hours per policy (goods).
- v1.8 text: card/skill descriptions and the first reply (opening_text) state only what the receipt carries.
"""
import argparse
import atexit
import base64
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from common import volume_level, open_resident, P, Recorder, Ledger, hook_event, read_json  # noqa: F401
import buyer_log as BL   # v1.20 logging only (never read by a decision)

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

ENV_NAME = os.environ.get("R3_SHOP_NAME", "lee-sungsoo")
NAME_FILE_MAX = 64


def resolve_shop_name(env_name=None, path=None):
    """v120c-B r3c name (CoS 14:56, 성수님 14:31): <W>/.r3/shop_name overrides the kit env R3_SHOP_NAME (a seller-only
    restart cannot change the inherited kit env). Missing/empty/invalid/too long/unreadable -> env value. Revert = rename
    the file to shop_name.off (no delete). Returns (name, source)."""
    env_name = env_name if env_name is not None else ENV_NAME
    p = Path(path or os.environ.get("R3_SHOP_NAME_FILE", Path(os.environ.get("R3_STATE", ".r3")) / "shop_name"))
    try:
        raw = p.read_bytes()
    except OSError:
        return env_name, "env"
    try:
        v = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return env_name, "env:invalid"
    if not v or len(v) > NAME_FILE_MAX or "\n" in v or not v.isprintable() or not re.search(r"[0-9A-Za-z]", v):
        return env_name, "env:invalid"
    return v, "file"


NAME, NAME_SOURCE = resolve_shop_name()
if NAME != ENV_NAME:                       # ledger/volume (same process) see both old and new name as our shop
    os.environ["R3_SHOP_NAME_PREV"] = ENV_NAME
    os.environ["R3_SHOP_NAME"] = NAME
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
SKU_RE = re.compile(r"\b(G[0-9A-Za-z]{5,})\b")
HOLD_S = 120               # a unit promised in an open negotiation is held this long, then freed (buyers walk away)
TICK_S = float(os.environ.get("R3_STATS_TICK_S", "15"))   # background refresh/checkpoint check; 0 = off


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    assert type(body["price"]) is int, "signed price must be int"
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


NEED_RE = re.compile(r"(?<!\d)(?<!\d[.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h|시간)(?![0-9A-Za-z])",
                     re.I)                                     # v2.1a: 1,000 hours -> 1000, 48h,72h -> 72
NEED_MARGIN_H = 0.25          # v2.1: the settle lands minutes after the quote
UNKNOWN_NEED_SHARE = 0.75     # v2.1: a food buyer needs 0.10/0.25/0.50/0.75 of the shelf; 0.75 covers every buyer
DECLINE_MIN_CHARS = 12        # v2.2: a shorter shop.decline_texts entry matches nothing ('the' declines no one)


def declined_phrase(text, pol):
    """v2.2: the shop.decline_texts entry the request carries (case and runs of whitespace ignored), else None. The
    list lives in policy.json, so a phrase is added or dropped without a restart; anything but a list of strings of
    12 characters or more matches nothing. One buyer's template 'Quote the total price. Require a market
    certificate.' left 1 star on all 63 of its paid deals with us in the 24 h to 11-05 14:40."""
    lst = (pol.get("shop") or {}).get("decline_texts")
    if not isinstance(lst, list):
        return None
    t = " ".join((text or "").split()).casefold()
    for e in lst:
        e = " ".join(e.split()).casefold() if isinstance(e, str) else ""
        if len(e) >= DECLINE_MIN_CHARS and e in t:
            return e
    return None


def stated_need_hours(text, names=()):
    """v2.1: shelf life the request asks for, in hours: the largest '<number> h / hour(s) / 시간' in it after SKU
    tokens and product `names` are removed ('I need it to last 180.0 hours: ... at least 198.0 hours' -> 198.0,
    'need: shelf life 541.0 h or more' -> 541.0; catalog G56d000c7 is named '... 48시간 구운계란 ...'). None when
    it names no hours. v2.1a: names and their own hour tokens come out case-insensitively, so '24h 숙성 김치' for
    '24H 숙성 김치' and a shortened '48시간 구운계란' state no need."""
    t = SKU_RE.sub(" ", text or "")
    names = sorted({n for n in names if n}, key=len, reverse=True)
    for nm in names:
        t = re.sub(re.escape(nm), " ", t, flags=re.I)
    for tok in sorted({m.group(0) for nm in names for m in NEED_RE.finditer(nm)}, key=len, reverse=True):
        t = re.sub(r"(?<!\d)(?<!\d[.,])" + re.escape(tok) + r"(?![0-9A-Za-z])", " ", t, flags=re.I)
    hs = [float(m.group(1).replace(",", "")) for m in NEED_RE.finditer(t)]
    return max(hs) if hs else None


def pick_food(food, need, shelf, now):
    """v2.1 (24 h to 11-05 13:30 KST: 71 of our 75 two-star food reviews read 'Only N h of shelf life left; I
    needed M h'): ship a unit that lasts the buyer's need. A stated need keeps the units with hours left >=
    need + NEED_MARGIN_H; none -> None (no sale rather than a two-star review). An unknown need is taken as
    UNKNOWN_NEED_SHARE x the catalog shelf_hours; when no unit lasts that long, the unit with the most hours left.
    Among the units that last, the one that expires first (stock keeps rotating; our grade C food reviews 4.75)."""
    left = lambda u: P.hours_left(u, now)
    key = lambda u: (left(u), u.get("supplied_at") or 0)
    if need is not None:
        return min((u for u in food if left(u) >= need + NEED_MARGIN_H), key=key, default=None)
    ok = [u for u in food if shelf(u["sku"]) and left(u) >= UNKNOWN_NEED_SHARE * shelf(u["sku"]) + NEED_MARGIN_H]
    return min(ok, key=key) if ok else max(food, key=lambda u: (left(u), -(u.get("supplied_at") or 0)))


def find_unit(text, inv, taken=(), pol=None, now=None, shelf=None, why=None):
    """`why` (a dict, optional) gets need_h when food units match but none lasts the stated need."""
    now = time.time() if now is None else now
    want = text.lower()
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired" and u["unit"] not in taken
             and (pol is None or P.sellable(u, pol, now))]
    skus = {s.lower() for s in SKU_RE.findall(text)}
    named = [u for u in units if u["sku"].lower() in skus]
    fit = named or [u for u in units if P.leaf(u.get("category")).lower() in want
                    or (u.get("name") or "").lower() in want]
    food = [u for u in fit if u.get("kind") == "food" and u.get("expires_at")]
    if food and len(food) == len(fit):          # v2.1: food by the buyer's shelf-life need (v2.1a: food-only fits)
        need = stated_need_hours(text, [u.get("name") for u in food])
        u = pick_food(food, need, shelf or shelf_hours, now)
        if u is None and why is not None:
            why["need_h"] = need
        return u
    # goods: ship the unit that expires first among those whose grade is A/B; else the first to expire.
    good = [u for u in fit if u.get("grade") in ("A", "B")]
    return min(good or fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0), default=None)


CARD_DESCRIPTION = "Signed receipts with certificate; grades never overstated. Ask by item or SKU."
SKILL_DESCRIPTION = "Ask for an item by name or SKU, then send a whole-number price."


def opening_text(unit, pol, ask, stats=None, shelf_hours=None, now=None):
    """v1.8: the first reply states exactly what the receipt would claim at this moment (P.receipt_terms: the grade,
    or none when a food grade cut is within claim_window_s or the grade is unknown), shelf life for food, and the
    ask as the last number of the text (readers of the last number, like our offer_in, get the price). The
    certificate is on the card (every receipt has cert); no warranty words (receipt_terms decides it per price)."""
    t = P.receipt_terms(unit, pol, stats=stats, price=ask, now=now, shelf_hours=shelf_hours)
    g = t.get("claim")
    gtxt = f"grade {g}" if g in ("A", "B", "C") else "grade not stated"
    h = P.hours_left(unit, now)
    life = f", {int(h)} h shelf life left" if unit.get("kind") == "food" and h is not None else ""
    return f"{unit['name']} ({unit['sku']}), {gtxt}{life}: asking {ask}. Offer a whole number."


def offer_in(text):
    t = SKU_RE.sub(" ", text)
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", t)
    if not nums:
        return None
    try:
        return int(float(nums[-1].replace(",", "")))
    except ValueError:
        return None


def shelf_hours(sku):
    cat = read_json(MARKET_DIR / "catalog.json", {}) or {}
    if isinstance(cat, list):
        cat = {x["sku"]: x for x in cat}
    return (cat.get(sku) or {}).get("shelf_hours")


class Seller(AgentExecutor):
    def __init__(self, pol=None):
        self.pol = pol or P.load()
        # v1.16: re-read policy.json when its hash changes (no kit restart); a given pol (tests) is never replaced,
        # R3_POLICY_RELOAD=0 turns it off
        self._pw = (P.PolicyWatcher(pol=self.pol) if pol is None and os.environ.get("R3_POLICY_RELOAD", "1") != "0"
                    else None)
        self.open = {}
        self._shelf = {}
        self._vol = (0.0, 0.0, 0)
        self._R, self._rerr, self._rt, self.checkpoints = None, None, 0.0, 0
        self._lock = threading.Lock()                   # Resident is used by A2A calls and the tick thread
        self._ticker = None
        self._blog = {}                                 # v1.20 logging only: task id -> (buyer_ref, source)

    def _bl(self, event, tid, **kw):
        """v1.20 logging only; never raises, never changes a decision."""
        try:
            b, src = self._blog.get(tid) or ("unknown", "none")
            BL.write(event, b, src, tid, **kw)
        except Exception:                               # noqa: BLE001
            pass

    def refresh_policy(self, now=None):
        """v1.16: switch to a changed policy.json between deals; on a bad file keep the current one and warn (stderr
        + metric policy_reload_failed). Open deals keep the policy they were opened with (deal["pol"])."""
        if LIVE is not None:                             # v120c-B r2: LLM flags re-read here (reload, no restart)
            _on, _chg = LIVE.refresh()
            if _chg:
                print(f"INFO: seller: llm live {"ON" if _on else "OFF"}", file=sys.stderr, flush=True)
                self._metric("llm_live", {"on": _on})
        if self._pw is None:
            return self.pol
        try:
            pol, ev = self._pw.get(now)
        except Exception as e:                           # noqa: BLE001
            pol, ev = self.pol, ("reload_failed", None, f"{type(e).__name__}: {str(e)[:150]}")
        if ev and ev[0] == "reload":
            self.pol = pol
            self._vol = (0.0, 0.0, 0)                    # volume level from the new policy at the next deal
            print(f"INFO: seller: policy reloaded {ev[1]} -> {ev[2]}", file=sys.stderr, flush=True)
            self._metric("policy_reload", {"old": ev[1], "new": ev[2]})
        elif ev:
            print(f"WARNING: seller: policy.json {ev[1]} not loaded ({ev[2]}); keeping {self.pol['_hash']}",
                  file=sys.stderr, flush=True)
            self._metric("policy_reload_failed", {"hash": ev[1], "error": ev[2], "kept": self.pol["_hash"]})
        return self.pol

    def stats(self):
        """Policy stats from the in-process aggregates.Resident (finding d + 09:32 wiring): opened once (one
        snapshot load), refresh() = only new ledger events, called per new deal. Any failure -> None (policy
        priors) and a retry of the open after 60 s; the error goes to the metric (stats_error)."""
        if self._R is None:
            if time.time() - self._rt < 60:
                return None
            self._rt = time.time()
            cat = read_json(MARKET_DIR / "catalog.json", {}) or {}
            self._R, self._rerr = open_resident({x["sku"]: x for x in cat} if isinstance(cat, list) else cat)
            if self._R is None:
                return None
            atexit.register(self.close_stats)
            if TICK_S > 0:
                self._ticker = threading.Thread(target=self._tick_loop, name="stats-tick", daemon=True)
                self._ticker.start()
        try:
            with self._lock:
                self._R.refresh()
            return self._R.A
        except Exception as e:                  # noqa: BLE001
            self._R, self._rerr = None, f"{type(e).__name__}: {str(e)[:150]}"
            return None

    def tick(self):
        """Refresh + checkpoint-if-due without a deal: the shop ledger only grows when restock ingests (60 s), so
        without this a quiet seller would never checkpoint and restock's StatsView would stay empty."""
        self.after_reply(refresh=True)

    def _tick_loop(self):
        while self._R is not None:
            time.sleep(TICK_S)
            self.tick()

    def after_reply(self, refresh=False):
        """Checkpoint (snapshot + StatsView shards) when 500 events or 30 s are due; never fails the task."""
        try:
            with self._lock:
                if self._R is not None and refresh:
                    self._R.refresh()
                if self._R is not None and self._R.maybe_checkpoint():
                    self.checkpoints += 1
        except Exception as e:                  # noqa: BLE001
            self._rerr = f"checkpoint {type(e).__name__}: {str(e)[:140]}"

    def close_stats(self):
        try:
            with self._lock:
                if self._R is not None:
                    self._R.close()
        except Exception:                       # noqa: BLE001
            pass

    def volume(self):
        """Min-volume relax level for the shop side, re-read at most every 30 s."""
        if time.time() - self._vol[0] > 30:
            self._vol = (time.time(), *volume_level("shop", self.pol))
        return self._vol[1], self._vol[2]

    def shelf(self, sku):
        if sku not in self._shelf:
            self._shelf[sku] = shelf_hours(sku)
        return self._shelf[sku]

    def decide(self, deal, offer):
        """Policy move for a buyer offer, behind the hard guard. A PolicyError/TypeError (price not strictly above
        cost, not an int) is never a task error: counter at the floor (or reject after 8 rounds), metric guard_refused."""
        u = deal["unit"]
        pol = deal.get("pol") or self.pol                # v1.16: the policy the deal was opened with
        try:
            hist = deal.get("offers") or []             # v1.6: the buyer's earlier offers (patience, stall, cap)
            move, price = P.respond(u, pol, deal["ask"], offer, deal["rounds"], deal["inv"], relax=deal["relax"],
                                    prev_offer=hist[-1] if hist else None, history=list(hist))
            if move == "accept":
                price = P.guard_price(u, price)                 # hard guard: strictly above cost, int only
            return move, price
        except (P.PolicyError, TypeError) as e:
            floor = P.floor_price(u, pol, deal["inv"], relax=deal["relax"])
            self._metric("guard_refused", {"sku": u["sku"], "unit": u["unit"], "offer": offer, "floor": floor,
                                           "error": str(e)[:120]}, pol=pol)
            return ("counter", floor) if deal["rounds"] < 8 else ("reject", None)

    def _metric(self, decision, outcome, pol=None):
        """pol: the deal's own policy (v1.16 reload: a deal opened before a reload is tagged with its own hash)."""
        r = Recorder("seller", policy_hash=(pol or self.pol)["_hash"])
        r.write(decision, Ledger().last_seq(), outcome=outcome)

    async def execute(self, context, event_queue):
        try:
            await self._execute(context, event_queue)
        finally:
            self.after_reply()                  # after the answer is queued: checkpoint if due

    async def _execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now = time.time()
        for tid in [k for k, d in self.open.items() if now - d["t"] > HOLD_S and k != task.id]:
            _d = self.open[tid]
            del self.open[tid]
            self._bl("outcome", tid, outcome="no_response", sku=_d["unit"].get("sku"), last_ask=_d.get("ask"),
                     rounds=_d.get("rounds"), idle_s=round(now - _d["t"], 1))
            self._blog.pop(tid, None)
        deal = self.open.get(task.id)
        if deal is None:
            try:
                self._blog[task.id] = BL.buyer_ref(context)
            except Exception:                           # noqa: BLE001
                self._blog[task.id] = ("unknown", "none")
            self._bl("request", task.id, text=BL.clean(text, 200))   # v121c: masked
            self.refresh_policy()                       # v1.16: only between deals
            if declined_phrase(text, self.pol):         # v2.2: shop.decline_texts, before any stock is read
                self._metric("declined", {"request": BL.clean(text, 80)})
                self._bl("outcome", task.id, outcome="declined", request=BL.clean(text, 80))
                self._blog.pop(task.id, None)
                await up.reject(say("Sorry, we can't take this order."))
                return
            inv = read_json(MARKET_DIR / "inventory.json", {}) or {}
            why = {}
            unit = find_unit(text, inv, {d["unit"]["unit"] for d in self.open.values()}, self.pol, shelf=self.shelf,
                             why=why)
            if unit is None:
                need = why.get("need_h")                # v2.1: food in stock, none lasts the stated need
                self._metric("no_stock", dict({"request": BL.clean(text, 80)}, **why))
                self._bl("outcome", task.id, outcome="no_stock", request=BL.clean(text, 80))
                self._blog.pop(task.id, None)
                await up.reject(say("Sorry, we have none of that in stock." if need is None
                                    else f"Sorry, none of our units has {need:g} h of shelf life left."))
                return
            sh = self.shelf(unit["sku"])
            relax, nset = self.volume()
            st = self.stats()
            ask = P.ask_price(unit, self.pol, stats=st, inv=inv, shelf_hours=sh, relax=relax)
            deal = self.open[task.id] = {"unit": unit, "ask": ask, "rounds": 0, "t": time.time(), "inv": inv, "sh": sh,
                                         "relax": relax, "stats": st, "pol": self.pol}
            self._metric("open", {"sku": unit["sku"], "unit": unit["unit"], "cost": unit["cost"], "ask": ask,
                                  "floor": P.floor_price(unit, self.pol, inv, relax=relax), "relax": relax,
                                  "settled": nset, "stats": st is not None, "stats_error": self._rerr,
                                  "stats_seq": getattr(st, "s", {}).get("seq") if st is not None else None})
            self._bl("quote", task.id, kind="open", sku=unit["sku"], unit=unit["unit"], category=unit.get("category"),
                     grade=unit.get("grade"), ask=ask, cost=unit.get("cost"), policy_hash=self.pol.get("_hash"))
            await up.requires_input(say(opening_text(unit, self.pol, ask, st, sh)))
            return
        deal["rounds"] += 1
        deal["t"] = now
        offer = offer_in(text)
        if offer is None and LIVE is not None and LIVE.on:   # v120c-B: LLM interpreter assist, caps v1, rule first, 8 s
            offer, _src = await LIVE.offer(text, offer_in, task.id, deal["ask"])
            self._metric("llm_parse", {"src": _src, "got": offer is not None})
        u = deal["unit"]
        move, price = self.decide(deal, offer)
        self._bl("offer", task.id, sku=u["sku"], offer=offer, round=deal["rounds"], prev_ask=deal["ask"], move=move,
                 price=price, **BL.offer_fields(text))   # v121b (CoS 결정 18:02): first 80 chars, secrets masked
        if offer is not None:
            deal.setdefault("offers", []).append(offer)  # v1.5/v1.6: next respond's prev_offer/history (pinned by tests_lead/test_v16_wiring.py)
        if move == "accept":
            receipt = dict({"shop": NAME, "item": u["sku"], "price": price, "task": task.id},
                           **P.receipt_terms(u, deal.get("pol") or self.pol, stats=deal.get("stats"), price=price, shelf_hours=deal["sh"]))
            receipt = sign(receipt)
            try:                                        # v1.3: cost layer for this unit before its sale is ingested
                with Ledger() as L:
                    L.first_seen_unit(u)
            except Exception as e:                      # noqa: BLE001  (never fail a sale on bookkeeping)
                self._metric("ledger_error", {"unit": u.get("unit"), "error": str(e)[:120]}, pol=deal.get("pol"))
            self._metric("accept", {"sku": u["sku"], "unit": u["unit"], "cost": u["cost"], "price": price,
                                    "below_cost": price < u["cost"], "rounds": deal["rounds"],
                                    "relax": deal["relax"], "claim": receipt.get("claim"),
                                    "warranty_hours": receipt.get("warranty_hours", 0)}, pol=deal.get("pol"))
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            self._bl("outcome", task.id, outcome="accept", sku=u["sku"], price=price, rounds=deal["rounds"])
            self._blog.pop(task.id, None)
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if move == "reject":
            self._metric("reject", {"sku": u["sku"], "offer": offer, "rounds": deal["rounds"]}, pol=deal.get("pol"))
            self._bl("outcome", task.id, outcome="reject", sku=u["sku"], offer=offer, rounds=deal["rounds"],
                     last_ask=deal["ask"])
            self._blog.pop(task.id, None)
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        deal["ask"] = price
        self._bl("quote", task.id, kind="counter", sku=u["sku"], ask=price, round=deal["rounds"])
        await up.requires_input(say(f"Counteroffer: {price}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task is None:
            return
        _had = self.open.pop(task.id, None)
        self._bl("outcome", task.id, outcome="cancel", open=_had is not None)
        self._blog.pop(task.id, None)
        self._metric("cancel", {"task": task.id})
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


try:                                              # v120c-B: default OFF; any import/config error -> no LLM
    from llm.live_v120c import Live as _Live
    LIVE = _Live()                                # OFF unless flags; refresh() re-reads them on policy reload
except Exception:                                 # noqa: BLE001
    LIVE = None


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME, description=CARD_DESCRIPTION,
        version="3.1.0", default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description=SKILL_DESCRIPTION, tags=["shopping"],
                           examples=["I want to buy 종가 포기김치 10kg (G00001)", "52000"])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
