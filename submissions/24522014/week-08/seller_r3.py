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

from r3_harness import CMD, ask_json
from harness import HarnessError
from seller import purchase_offer
from r3_policy import selling_terms
from market_state import read, write, task_file
from kit_runtime import OFFER_SECONDS
from market_intel import fresh
from strategy_policy import effective

NAME = "kcwarni Inventory Shop"
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
    if terms and terms["discount_stage"]:
        notice += f" Clearance reason: {terms['discount_reason']}; stage {terms['discount_stage']}."
    notice += " Confirm the certificate at checkout; condition can change while negotiating."
    notice += f" Warranty: {details['warranty_hours']} hours."
    return notice, details


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation."""
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
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


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
              "features": ["actual_grade_receipt", "bounded_warranty_v1"]})
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
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None,
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
            terms = selling_terms(u, catalog, signals=signals, runtime=effective(MARKET_DIR))
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
        (log_dir / (uuid4().hex + ".json")).write_text(json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(), "task": task.id,
             "unit": u.get("unit"), "sku": u["sku"], "cost": u["cost"],
             "grade_observed": u.get("grade"), "expires_at": u.get("expires_at"), **terms},
            ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            if deal["ask"] is not None and deal["offer"] == deal["ask"] and deal["offer"] >= reserve:
                move, raw = {"action": "accept"}, "deterministic acceptance of the disclosed quote"
            else:
                move, raw = await asyncio.to_thread(ask_json, PROMPT.format(
                name=u["name"], list_price=terms["quote"], reserve=reserve,
                transcript="\n".join(deal["lines"])))
        except (HarnessError, ValueError) as error:
            self.open.pop(task.id, None)
            print(f"[harness] decision failed: {error}", flush=True)
            await up.failed(say("Unable to obtain a valid seller decision."))
            return
        print(f"[harness] {raw}", flush=True)
        action = (move or {}).get("action")
        # A model call takes time: verify the unit and terms again before signing.
        if u.get("unit"):
            before = deal.get("disclosed", (u.get("grade"), u.get("expires_at"), terms["quote"]))
            try:
                inventory = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
                u = next(x for x in inventory["units"] if x["unit"] == u["unit"])
                if u["unit"] in self.committed_units:
                    raise ValueError("Unit already committed")
                terms = selling_terms(u, catalog, signals=signals, runtime=effective(MARKET_DIR))
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
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                receipt.update(claim=u["grade"], cert=True, warranty_hours=terms.get("warranty_hours", 0))
            receipt = sign(receipt)
            if u.get("unit"):
                write(task_file(MARKET_DIR, "signed-offers", task.id),
                      {"task": task.id, "unit": u["unit"], "sku": u["sku"], "price": price,
                       "cost": u["cost"], "strategy_version": terms.get("strategy_version"),
                       "grade": u.get("grade"), "warranty_hours": terms.get("warranty_hours", 0),
                       "warranty_trial_group": deal.get("coverage", {}).get("group"),
                       "strategy_experiment": terms.get("strategy_experiment"),
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
          "features": ["actual_grade_receipt", "bounded_warranty_v1"]})
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
