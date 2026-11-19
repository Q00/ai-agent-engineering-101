"""Round 3 profit-aware seller.

Fast deterministic negotiation:
- never sells below cost;
- prices by cost, list price, grade, age and expiry;
- accepts good offers immediately;
- uses at most two counteroffers;
- prioritises selling expiring food and ageing goods.
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

NAME = "Harness Shop"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))


def canon(d):
    return json.dumps(
        d, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(
        Path("shop_key.pem").read_bytes(), password=None
    )
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(
        receipt,
        sig_seller=base64.b64encode(key.sign(canon(body))).decode()
    )


def find_unit(text, taken=()):
    want = text.lower()

    try:
        inv = json.loads(
            (MARKET_DIR / "inventory.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None

    units = inv.get("units", [])

    fit = [
        u for u in units
        if u.get("grade") != "expired"
        and u.get("unit") not in taken
        and (
            ((u.get("category") or "").split(">")[-1].lower() in want)
            or ((u.get("name") or "").lower() in want)
            or ((u.get("sku") or "").lower() in want)
        )
    ]

    named = [
        u for u in fit
        if (u.get("sku") or "").lower() in want
    ]
    fit = named or fit

    # Ship expiring food first; otherwise oldest stock first.
    return min(
        fit,
        key=lambda u: (
            u.get("expires_at") or float("inf"),
            u.get("supplied_at") or 0,
        ),
        default=None,
    )


def pricing(u):
    now = time.time()

    cost = float(u["cost"])
    list_price = float(u.get("list_price") or cost * 1.30)
    grade = str(u.get("grade") or "B").upper()
    kind = u.get("kind") or "goods"

    expires_at = u.get("expires_at")
    hours_left = (
        (float(expires_at) - now) / 3600
        if expires_at
        else None
    )

    # Market anchor: higher grade can stay closer to list price.
    anchor = {
        "A": 0.95,
        "B": 0.90,
        "C": 0.82,
    }.get(grade, 0.88)

    if kind == "food" and hours_left is not None:
        if hours_left <= 24:
            anchor = min(anchor, 0.70)
            min_margin = 0.00
        elif hours_left <= 72:
            anchor = min(anchor, 0.78)
            min_margin = 0.02
        elif hours_left <= 168:
            anchor = min(anchor, 0.85)
            min_margin = 0.05
        else:
            min_margin = 0.08
    else:
        age_days = max(
            0.0,
            (now - float(u.get("supplied_at") or now)) / 86400,
        )

        # Goods cost 1%/day to store, so old stock gets cheaper faster.
        anchor -= min(0.10, age_days * 0.02)

        min_margin = {
            "A": 0.08,
            "B": 0.06,
            "C": 0.04,
        }.get(grade, 0.05)

    floor_price = max(
        cost,
        round(cost * (1.0 + min_margin))
    )

    target_price = max(
        floor_price,
        round(list_price * anchor)
    )

    # Lead with the target price so buyers are more likely to engage.
    initial_ask = target_price
        

    return {
        "cost": round(cost),
        "floor": round(floor_price),
        "target": round(target_price),
        "ask": round(initial_ask),
        "hours_left": hours_left,
        "grade": grade,
        "kind": kind,
    }


def parse_offer(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return None

    try:
        return round(float(nums[-1]))
    except ValueError:
        return None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}

    async def execute(self, context, event_queue):
        task = context.current_task

        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        up = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )

        say = lambda text: up.new_agent_message(
            parts=[new_text_part(text)]
        )

        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            taken = {
                d["unit"]["unit"]
                for d in self.open.values()
                if d["unit"].get("unit")
            }

            unit = find_unit(text, taken)

            if unit is None:
                await up.reject(
                    say("Sorry, we have none of that in stock.")
                )
                return

            policy = pricing(unit)

            deal = self.open[task.id] = {
                "unit": unit,
                "policy": policy,
                "round": 0,
                "ask": policy["ask"],
            }

            expiry = (
                f"{policy['hours_left']:.1f}h"
                if policy["hours_left"] is not None
                else "none"
            )

            print(
                f"[strategy] open sku={unit['sku']} "
                f"cost={policy['cost']} "
                f"floor={policy['floor']} "
                f"target={policy['target']} "
                f"ask={policy['ask']} "
                f"grade={policy['grade']} "
                f"kind={policy['kind']} "
                f"expiry={expiry}",
                flush=True,
            )

            await up.requires_input(
                say(
                    f"{unit['name']}: asking {policy['ask']}. "
                    "Send your offer as a number."
                )
            )
            return

        offer = parse_offer(text)
        p = deal["policy"]
        u = deal["unit"]

        if offer is None:
            await up.requires_input(
                say(f"Counteroffer: {deal['ask']}.")
            )
            return

        print(
            f"[strategy] sku={u['sku']} round={deal['round']} "
            f"offer={offer} floor={p['floor']} target={p['target']}",
            flush=True,
        )

        # Strong offer: close immediately.
        if offer >= p["target"]:
            action = "accept"

        # After one counter, accept any still-profitable offer.
        elif deal["round"] >= 1 and offer >= p["floor"]:
            action = "accept"

        # Final chance: for very low-margin inventory, accept at cost.
        elif deal["round"] >= 2 and offer >= p["cost"]:
            action = "accept"

        else:
            action = "counter"

        if action == "accept":
            price = max(offer, p["cost"])

            receipt = {
                "shop": NAME,
                "item": u["sku"],
                "price": price,
                "task": task.id,
            }

            if u.get("unit"):
                receipt["unit"] = u["unit"]
            
            receipt["cert"] = True
            receipt["claim"] = p["grade"]

            if p["kind"] == "goods":
                receipt["warranty_hours"] = 6

            receipt = sign(receipt)

            await up.add_artifact(
                parts=[new_data_part(receipt)],
                name="receipt",
            )

            await up.complete(
                say(f"Deal: {u['name']} at {price}.")
            )

            print(
                f"[strategy] ACCEPT sku={u['sku']} "
                f"price={price} cost={p['cost']} "
                f"profit={price - p['cost']}",
                flush=True,
            )

            del self.open[task.id]
            return

        # First counter goes straight to target.
        if deal["round"] == 0:
            counter = p["target"]

        # Second and final counter goes to our profitable floor.
        elif deal["round"] == 1:
            counter = p["floor"]

        else:
            print(
                f"[strategy] REJECT sku={u['sku']} "
                f"offer={offer} below floor={p['floor']}",
                flush=True,
            )
            await up.reject(say("No deal."))
            del self.open[task.id]
            return

        deal["round"] += 1
        deal["ask"] = max(counter, p["cost"])

        print(
            f"[strategy] COUNTER sku={u['sku']} "
            f"price={deal['ask']}",
            flush=True,
        )

        await up.requires_input(
            say(f"Counteroffer: {deal['ask']}.")
        )

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)

        await TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        ).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"

    card = AgentCard(
        name=NAME,
        description=(
            "A profit-aware Round 3 shop with adaptive pricing "
            "based on cost, grade, inventory age and expiry."
        ),
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=url,
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=(
                    "Ask for an in-stock item by name, category or SKU."
                ),
                tags=["shopping", "negotiation", "adaptive-pricing"],
                examples=["I want to buy G00001", "52000"],
            )
        ],
    )

    handler = DefaultRequestHandler(
        agent_executor=Seller(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    return Starlette(
        routes=create_agent_card_routes(card)
        + create_jsonrpc_routes(handler, "/")
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port

    uvicorn.run(
        build(port),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
