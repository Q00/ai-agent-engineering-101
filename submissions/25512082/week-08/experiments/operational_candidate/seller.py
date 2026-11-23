"""A2A v1 Seller candidate with deterministic B-safe-1 policy."""
from __future__ import annotations

import base64
import argparse
import json
import math
import re
import contextlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
import uvicorn
from operational_candidate.ledger import LedgerError, TradeLedger

NAME = "Atlas Shop B-safe-1 candidate"


def canon(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt: dict, key_path: Path) -> dict:
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    body = {key: receipt[key] for key in ("shop", "item", "price", "task")}
    return {**receipt, "sig_seller": base64.b64encode(key.sign(canon(body))).decode()}


def load_inventory(market_dir: Path) -> dict:
    try:
        value = json.loads((market_dir / "inventory.json").read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def find_unit(text: str, inventory: dict, taken: set[str] | None = None, now: int = 0) -> dict | None:
    want, taken = text.lower(), taken or set()
    units = inventory.get("units", []) if inventory.get("round", 0) >= 3 else []
    fit = [u for u in units if isinstance(u, dict) and u.get("unit") not in taken
           and u.get("grade") != "expired" and isinstance(u.get("cost"), (int, float))
           and (not u.get("expires_at") or u["expires_at"] > now)
           and ((u.get("category") or "").split(">")[-1].lower() in want
                or (u.get("name") or "").lower() in want or str(u.get("sku", "")).lower() in want)]
    named = [u for u in fit if str(u.get("sku", "")).lower() in want]
    return min(named or fit, key=lambda u: (u.get("expires_at") or math.inf, u.get("supplied_at", 0)), default=None)


def terms(unit: dict) -> tuple[int, int]:
    floor = math.ceil(unit["cost"] * 1.05)
    ask = max(round(unit["list_price"] * 0.82), floor)
    return ask, floor


class CandidateSeller(AgentExecutor):
    def __init__(self, market_dir: Path, key_path: Path, ledger_path: Path) -> None:
        self.market_dir, self.key_path, self.open = market_dir, key_path, {}
        self.ledger = TradeLedger(ledger_path)

    def _failure(self, task_id: str, reason: str, unit_id: str | None = None) -> None:
        with contextlib.suppress(LedgerError):
            self.ledger.append({"transaction_id": task_id, "order_id": task_id,
                                "unit_id": unit_id or f"none:{task_id}", "status": "rejected",
                                "rejection_reason": reason})

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        if deal is None:
            taken = {row["unit"]["unit"] for row in self.open.values()}
            taken.update(row.get("unit_id") for row in self.ledger.rows if row.get("unit_id"))
            unit = find_unit(text, load_inventory(self.market_dir), taken)
            if unit is None:
                self._failure(task.id, "no_verified_inventory")
                await updater.reject(say("No verified, unexpired inventory is available.")); return
            ask, floor = terms(unit)
            self.open[task.id] = {"unit": unit, "ask": ask, "floor": floor, "rounds": 0}
            await updater.requires_input(say(f"{unit['sku']} asking price: {ask}")); return
        numbers = re.findall(r"\d+", text)
        if not numbers:
            self._failure(task.id, "invalid_offer", deal["unit"]["unit"])
            self.open.pop(task.id, None); await updater.reject(say("Offer has no valid price.")); return
        offer = int(numbers[-1]); deal["rounds"] += 1
        if offer >= deal["floor"] and offer <= deal["ask"]:
            unit = deal["unit"]
            try:
                self.ledger.append({"transaction_id": task.id, "order_id": task.id, "unit_id": unit["unit"],
                                    "status": "a2a_agreed", "rejection_reason": ""})
            except LedgerError:
                self.open.pop(task.id, None); await updater.reject(say("Duplicate or unrecordable trade.")); return
            receipt = sign_receipt({"shop": NAME, "item": unit["sku"], "price": offer,
                                    "task": task.id, "unit": unit["unit"]}, self.key_path)
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            self.open.pop(task.id, None); await updater.complete(say(f"Deal accepted at {offer}")); return
        if deal["rounds"] >= 3 or offer < deal["floor"]:
            # One guarded counter is allowed for a low offer; hard stop after configured rounds.
            if deal["rounds"] >= 3:
                self._failure(task.id, "no_price_agreement", deal["unit"]["unit"])
                self.open.pop(task.id, None); await updater.reject(say("No price agreement.")); return
        deal["ask"] = max(deal["floor"], round(deal["ask"] * 0.94))
        await updater.requires_input(say(f"Counteroffer: {deal['ask']}"))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task:
            self.open.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port: int, market_dir: Path, key_path: Path, ledger_path: Path) -> Starlette:
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(name=NAME, description="Inventory-backed B-safe-1 operating candidate", version="1.0.0",
        default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate", description="Ask by SKU or product name",
                           tags=["shopping"], examples=["I want SKU-X"])])
    handler = DefaultRequestHandler(agent_executor=CandidateSeller(market_dir, key_path, ledger_path),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--market-dir", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(build_app(args.port, args.market_dir, args.key, args.ledger), host="127.0.0.1", port=args.port,
                log_level="warning")
