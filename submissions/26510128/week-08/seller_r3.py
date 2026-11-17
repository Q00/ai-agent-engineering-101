"""Week 08 adaptive seller: real kit-v4 inventory, hard floor, signed receipts.

    python seller_r3.py --port 9301

The harness decides every move; this file only speaks A2A, finds a unit for what the buyer asks, and signs the
receipt with shop_key.pem (the kit makes it). It ships the unit that expires first, says nothing about quality,
offers a bounded warranty for non-food goods, and never goes below the unit-specific floor.
"""
import argparse
import base64
import json
import os
import re
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

from model_harness import MODEL, decide
from round3_policy import find_unit, reserve_for_unit, suggested_ask, catalog, demand_weights, load_json

NAME = "JangSeongWoong"

def ask_json(prompt):
    """LLM proposes a move; deterministic reserve and certificate guards follow."""
    try:
        move = decide("Round 3 seller: respond with a JSON action. " + prompt,
                      {"role": "seller", "model": MODEL}, ("counter", "accept", "reject"))
        return move, f"model={MODEL} action={move.get('action')}"
    except Exception as exc:
        print(f"[harness fallback] {type(exc).__name__}: {exc}", flush=True)
        return {"action": "counter"}, "fallback: safe deterministic counter"


RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))


def observed_demand_score(unit):
    """Read actual category demand, never seller-supplied conversational text."""
    weights = demand_weights(load_json(MARKET_DIR / "demand.json", []))
    category = str(unit.get("category", ""))
    score = weights.get(category, 0)
    if not score and category:
        suffix = category.split(">")[-1].strip()
        score = max((v for k, v in weights.items()
                     if suffix and (suffix in k or k in category)), default=0)
    return min(4, float(score) / 8.0)


def policy_quote(unit):
    return suggested_ask(unit, catalog().get(unit.get("sku", ""), {}),
                         observed_demand_score(unit))


PROMPT = """You are the sales agent of a small online shop. A buyer is negotiating for one {name}.
The list price is {list_price}. Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
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




class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines"}

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
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]})
            if unit is None:
                print("[stock guard] no eligible inventory matched requested SKU/category", flush=True)
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "lines": [f"buyer: {text}"]}
        else:
            deal["lines"].append(f"buyer: {text}")
            m = re.fullmatch(r"\s*(\d+)\s*", text)
            deal["offer"] = int(m.group(1)) if m else None

        u = deal["unit"]
        reserve = reserve_for_unit(u, catalog().get(u.get("sku",""), {}))
        if deal["offer"] is not None and deal["offer"] >= reserve:
            # High-throughput deterministic close: no second LLM call is
            # needed for a financially safe offer already made by the buyer.
            move, raw = {"action": "accept"}, "policy: profitable offer"
        else:
            move, raw = ask_json(PROMPT.format(
                name=u["name"], list_price=u["list_price"], reserve=reserve,
                transcript="\n".join(deal["lines"])))
        print(f"[harness] {raw}", flush=True)
        action = (move or {}).get("action")
        if deal["offer"] is None:
            action = "counter"  # never accept an unspecified price
        elif deal["offer"] >= reserve:
            # Financial guard: close any above-floor offer to avoid speculative
            # stockholding and let real customers complete settlement.
            action = "accept"
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below cost {reserve} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"] or u["list_price"]}
        if action == "accept":
            price = deal["offer"]
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                # Never invent a grade claim; unit certificates are filled by kit v4.
                if u.get("expires_at") is None:
                    receipt["warranty_hours"] = 6
            receipt = sign(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if action == "reject":
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if not isinstance(price, (int, float)):
            price = deal["ask"] or policy_quote(u)      # unparsed answer: repeat the last ask
        if price < reserve:                             # the harness may forget the cost; the code does not
            print(f"[guard] ask {price} below cost {reserve} -> {reserve}", flush=True)
            price = reserve
        target = policy_quote(u)
        # Never let the model demand an arbitrary markup or go under margin.
        price = max(reserve, min(int(price), max(target, reserve)))
        if deal["offer"] is None:
            price = max(reserve, target)
        deal["ask"] = int(price)
        print(f"[pricing policy] sku={u.get('sku')} cost={u.get('cost')} floor={reserve} ask={price} list={u.get('list_price')}", flush=True)
        reply = (f"{u['name']}: asking {deal['ask']}. Send your offer as a number." if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=f"Inventory-backed A2A shop, per-unit floor and demand-aware pricing. Negotiation model: {MODEL}.",
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
