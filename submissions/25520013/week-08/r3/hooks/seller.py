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

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

NAME = os.environ.get("R3_SHOP_NAME", "lee-sungsoo")
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


def find_unit(text, inv, taken=(), pol=None, now=None):
    want = text.lower()
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired" and u["unit"] not in taken
             and (pol is None or P.sellable(u, pol, now))]
    skus = {s.lower() for s in SKU_RE.findall(text)}
    named = [u for u in units if u["sku"].lower() in skus]
    fit = named or [u for u in units if P.leaf(u.get("category")).lower() in want
                    or (u.get("name") or "").lower() in want]
    now = time.time()
    # food: most shelf life left first is better for the buyer's accept; but expiring stock must go first.
    # Ship the unit that expires first among those whose grade is still A/B; else the first to expire.
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

    def refresh_policy(self, now=None):
        """v1.16: switch to a changed policy.json between deals; on a bad file keep the current one and warn (stderr
        + metric policy_reload_failed). Open deals keep the policy they were opened with (deal["pol"])."""
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
            del self.open[tid]
        deal = self.open.get(task.id)
        if deal is None:
            self.refresh_policy()                       # v1.16: only between deals
            inv = read_json(MARKET_DIR / "inventory.json", {}) or {}
            unit = find_unit(text, inv, {d["unit"]["unit"] for d in self.open.values()}, self.pol)
            if unit is None:
                self._metric("no_stock", {"request": text[:80]})
                await up.reject(say("Sorry, we have none of that in stock."))
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
            await up.requires_input(say(opening_text(unit, self.pol, ask, st, sh)))
            return
        deal["rounds"] += 1
        deal["t"] = now
        offer = offer_in(text)
        u = deal["unit"]
        move, price = self.decide(deal, offer)
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
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if move == "reject":
            self._metric("reject", {"sku": u["sku"], "offer": offer, "rounds": deal["rounds"]}, pol=deal.get("pol"))
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        deal["ask"] = price
        await up.requires_input(say(f"Counteroffer: {price}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task is None:
            return
        self.open.pop(task.id, None)
        self._metric("cancel", {"task": task.id})
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


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
