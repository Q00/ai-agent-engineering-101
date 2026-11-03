import argparse
import base64
import json
import os
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

NAME = "Jaehoonang"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))

def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())

def find_unit(text, taken=()):
    inv_path = MARKET_DIR / "inventory.json"
    if not inv_path.exists():
        return None
    try:
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    units = inv.get("units", [])
    fit = [u for u in units if u["id"] not in taken]
    # Check query matching
    matched = [u for u in fit if u["sku"].lower() in text.lower() or u["name"].lower() in text.lower()]
    pool = matched if matched else fit
    return min(pool, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)

class Seller(AgentExecutor):
    async def execute(self, context, event_queue):
        task = context.current_task
        text = get_message_text(task)
        taken = [t["unit"] for t in context.session_history if t.get("unit")]
        unit = find_unit(text, taken)

        if not unit:
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).add_artifact(
                [new_text_part("Out of stock or no matching inventory.")],
                is_last=True
            )
            return

        # Check strategy.json for monopoly markup & cost protection
        strat_path = Path("strategy.json")
        markup = 1.40
        if strat_path.exists():
            try:
                st = json.loads(strat_path.read_text(encoding="utf-8"))
                markup = st.get("monopoly_supplier", {}).get("monopoly_markup_ratio", 1.40)
            except Exception:
                pass

        cost = unit["cost"]
        price = max(int(cost * markup), cost + 10)

        receipt = sign({
            "shop": NAME,
            "item": unit["name"],
            "sku": unit["sku"],
            "unit": unit["id"],
            "price": price,
            "task": task.id,
            "warranty_hours": 1
        })

        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).add_artifact(
            [new_data_part(receipt), new_text_part(f"Offering {unit['name']} (SKU: {unit['sku']}) for {price} (Cost: {cost}, Markup: {markup}x). Signed with Ed25519.")],
            is_last=True
        )

    async def cancel(self, context, event_queue):
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()

def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Jaehoonang Monopolistic Hardware Supplier & Restructuring Engine",
        interfaces=[AgentInterface(url=url, protocol="jsonrpc")],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(id="buy", name="Buy Goods", description="Negotiate and buy certified hardware items.")]
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore())
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
