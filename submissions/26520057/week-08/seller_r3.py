"""Round 3 SHOP: certified inventory, deterministic protected pricing, local model for ambiguous bids."""
import argparse
import asyncio
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
from harness import ask_json
from shop_strategy import read_json, signals, market_now, quality, prices, reserve_warranty, release_quote_warranty, params

NAME = "cheapest-in-town"
ROOT = Path(__file__).resolve().parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def event(kind, **fields):
    path = ROOT / "logs/shop-decisions.jsonl"
    with path.open("a") as log:
        log.write(json.dumps({"ts": time.time(), "event": kind, **fields}, ensure_ascii=False) + "\n")


def select_unit(text, units, taken, catalog, now):
    want = text.lower()
    explicit = re.search(r"\bG[0-9a-f]{8}\b", text, flags=re.I)
    available = [u for u in units if u["unit"] not in taken]
    if explicit:
        available = [u for u in available if u["sku"].lower() == explicit.group().lower()]
    else:
        available = [u for u in available if u.get("name", "").lower() in want
            or (u.get("category", "").split(">")[ -1] and u["category"].split(">")[-1].lower() in want)]
    fits = []
    for unit in available:
        q = quality(unit, catalog, now)
        if q["grade"] == "expired":
            continue
        # Avoid claiming a grade that could change during the next checkout.
        if unit.get("kind") == "food":
            if not signals(MARKET_DIR).get("updated"):
                continue
            shelf = catalog.get(unit["sku"], {}).get("shelf_hours", 0) * 3600
            remaining = unit.get("expires_at", 0) - now
            if shelf and min(abs(remaining - shelf * cut) for cut in [.8, .45, 0]) < 90:
                continue
        fits.append(unit)
    return min(fits, key=lambda u: ("ABC".find(quality(u, catalog, now)["grade"]),
              u.get("expires_at") or float("inf"), u["cost"]), default=None)


def parse_offer(text, ask):
    cleaned = text.strip().replace(",", "")
    if re.fullmatch(r"[₩$]?\s*\d+\s*(?:원|credits?)?[.!]?", cleaned, flags=re.I):
        return int(re.search(r"\d+", cleaned).group())
    match = re.search(r"(?:I (?:can )?offer|my offer is|pay|제안|제시)\s*[:=]?\s*[₩$]?\s*(\d+)", cleaned, flags=re.I)
    if match:
        return int(match.group(1))
    if re.fullmatch(r"(?:accept|deal|agreed|yes|I accept|I'll take it|살게요|사겠습니다|좋아요)[.!]?", cleaned, flags=re.I):
        return ask
    return None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}
        self.pending = {}

    async def execute(self, context, event_queue):
        started = time.monotonic()
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        inv = read_json(MARKET_DIR / "inventory.json", {})
        catalog = read_json(MARKET_DIR / "catalog.json", {})
        now = market_now(MARKET_DIR)
        for expired in [k for k, d in self.open.items() if time.time() - d["updated"] >= 180]:
            release_quote_warranty(MARKET_DIR, expired)
            self.open.pop(expired, None)
        self.pending = {k: until for k, until in self.pending.items() if until > time.time()}
        deal = self.open.get(task.id)
        fresh = deal is None
        if fresh:
            taken = {d["unit"]["unit"] for d in self.open.values()} | set(self.pending)
            unit = select_unit(text, inv.get("units", []), taken, catalog, now)
            if unit is None:
                event("no_fitting_stock", task=task.id)
                await up.reject(say("No matching unit is currently available. No purchase was agreed."))
                return
            q = quality(unit, catalog, now)
            p = prices(unit, signals(MARKET_DIR), q["grade"])
            warranty = reserve_warranty(MARKET_DIR, task.id, unit["unit"], p["ask"]) if q["warranty_hours"] else 0
            deal = self.open[task.id] = {"unit": unit, "ask": p["ask"], "updated": time.time(),
                                       "counters": 0, "warranty_hours": warranty}
        unit = next((u for u in inv.get("units", []) if u["unit"] == deal["unit"]["unit"]), None)
        if unit is None:
            self.open.pop(task.id, None)
            await up.reject(say("That unit is no longer in stock. No purchase was agreed."))
            return
        q = quality(unit, catalog, now)
        if q["grade"] == "expired":
            self.open.pop(task.id, None)
            await up.reject(say("That unit has expired and cannot be sold."))
            return
        q["warranty_hours"] = deal["warranty_hours"]
        policy = prices(unit, signals(MARKET_DIR), q["grade"])
        deal["ask"] = min(deal["ask"], policy["ask"])
        deal["updated"] = time.time()
        offer = None if fresh else parse_offer(text, deal["ask"])
        if not fresh and offer is None and len(text) <= 600:
            try:
                schema = {"type": "object", "properties": {"intent": {"type": "string", "enum": ["accept", "offer", "question"]}, "offer": {"type": "integer", "minimum": 1}}, "required": ["intent"], "additionalProperties": False}
                move, _ = await asyncio.to_thread(ask_json, params("prompts")["seller_classify"] + json.dumps(text), schema)
                if move.get("intent") == "accept":
                    offer = deal["ask"]
                elif move.get("intent") == "offer" and type(move.get("offer")) is int:
                    offer = move["offer"]
            except Exception:
                pass  # Clarify without agreement; no paid fallback or invented price.
        if offer is not None and offer >= policy["floor"]:
            price = min(offer, deal["ask"])
            if q["warranty_hours"]:
                reserve_warranty(MARKET_DIR, task.id, unit["unit"], deal["ask"], commit=True)
            receipt = sign({"shop": NAME, "item": unit["sku"], "price": price, "task": task.id,
                "unit": unit["unit"], "claim": q["grade"], "cert": True,
                "warranty_hours": q["warranty_hours"]})
            self.pending[unit["unit"]] = time.time() + 30
            self.open.pop(task.id, None)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {unit['name']} at {price}; grade {q['grade']}, certificate included, warranty {q['warranty_hours']} h."))
            event("agreed", task=task.id, sku=unit["sku"], unit=unit["unit"], price=price,
                  cost=unit["cost"], expected_gross=price-unit["cost"], grade=q["grade"],
                  warranty_hours=q["warranty_hours"], seconds=round(time.monotonic()-started, 4))
            return
        if offer is not None:
            deal["counters"] += 1
            if deal["counters"] >= params("shop")["max_counters"]:
                self.open.pop(task.id, None)
                release_quote_warranty(MARKET_DIR, task.id)
                await up.reject(say("We cannot agree at that price. No purchase was made."))
                event("below_floor_exit", task=task.id, sku=unit["sku"])
                return
            concession = max(1, int(unit["list_price"] * params("shop")["concession"]))
            deal["ask"] = max(policy["floor"], deal["ask"] - concession)
        details = {"sku": unit["sku"], "unit": unit["unit"], "ask": deal["ask"],
            "list_price": unit["list_price"], "grade": q["grade"], "hours_left": q["hours_left"],
            "warranty_hours": q["warranty_hours"], "certificate": unit.get("cert")}
        extra = f", about {q['hours_left']:.1f} h shelf life left" if q["hours_left"] is not None else ""
        reply = f"{unit['name']} ({unit['sku']}), grade {q['grade']}{extra}. Market certificate included; warranty {q['warranty_hours']} h. Asking price: {deal['ask']}. Send one price as a number, or accept."
        if params("shop")["reply_note"]:
            reply += " " + params("shop")["reply_note"]
        await up.requires_input(up.new_agent_message(parts=[new_text_part(reply), new_data_part(details)]))
        event("quote", task=task.id, sku=unit["sku"], unit=unit["unit"], ask=deal["ask"],
              floor=policy["floor"], cost=unit["cost"], grade=q["grade"], seconds=round(time.monotonic()-started,4))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        release_quote_warranty(MARKET_DIR, task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    card = AgentCard(name=NAME,
        description="Market-certified stock with truthful current grades. Fast SKU quotes, prices never increase within a negotiation. Eligible grade A goods may include a 6-hour breakage refund warranty, stated in the quote and receipt. Food states remaining shelf life. Ask by SKU or category, then offer a number or accept. Free local Qwen handles ambiguous replies; price and payment limits are enforced in code.",
        version="2.0.0", default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/", protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Buy certified inventory", description="Ask for a stocked SKU, inspect its grade, certificate, price and warranty, then negotiate or accept.", tags=["shopping", "negotiation", "certificates"])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9301)
    args = parser.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
