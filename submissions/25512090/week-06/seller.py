"""Seller Agent implementing A2A v1.0 JSON-RPC binding, guardrails, and error handling."""
import argparse
import json
import math
import re
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, UnsupportedOperationError

DEFAULT_CONFIG = {
    "name": "Advanced A2A Seller",
    "description": "Sells catalog items with secure reserve guardrails.",
    "skill_description": "Negotiate purchase of items based on catalog and reserve.",
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
    "max_rounds": 5,
}

class SellerExecutor(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open_deals = {}
        self.terminal_tasks = set()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None and task.id in self.terminal_tasks:
            raise UnsupportedOperationError(message="Task is in terminal state")

        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""

        deal = self.open_deals.get(task.id)

        if deal is None:
            item = None
            reserves = self.cfg.get("reserve", {})
            catalog = self.cfg.get("catalog", [])
            
            text_lower = text.lower()
            for k in reserves:
                if k in text_lower:
                    item = k
                    break
            if item is None and catalog:
                best_match = None
                max_score = -1
                for prod in catalog:
                    pname = prod.get("name", "").lower()
                    pdesc = prod.get("description", "").lower()
                    score = sum(1 for word in text_lower.split() if word in pname or word in pdesc)
                    if score > max_score:
                        max_score = score
                        best_match = prod
                if best_match and max_score > 0:
                    item = best_match["name"]
                    reserves[item] = best_match["reserve"]
            
            if item is None:
                for default_item in ["lamp", "chair", "bookshelf"]:
                    if default_item in text_lower:
                        item = default_item
                        break
            if item is None:
                item = "lamp"
                
            reserve = reserves.get(item, 100)
            ask = math.ceil(reserve * 1.3)
            self.open_deals[task.id] = {"item": item, "ask": ask, "rounds": 0, "reserve": reserve}
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say("Please send your offer as a number."))
            return

        offer = int(m.group())
        reserve = deal["reserve"]

        if offer >= reserve:
            receipt = {"seller": self.cfg["name"], "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            self.terminal_tasks.add(task.id)
            self.open_deals.pop(task.id, None)
            return

        deal["rounds"] += 1
        max_r = self.cfg.get("max_rounds", 5)
        if deal["rounds"] >= max_r:
            await up.reject(say(f"No deal at {offer}. Offer is below reserve."))
            self.terminal_tasks.add(task.id)
            self.open_deals.pop(task.id, None)
            return

        new_ask = max(reserve, (offer + deal["ask"]) // 2)
        deal["ask"] = new_ask
        await up.requires_input(say(f"{offer} is too low. Counteroffer: {new_ask}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            self.terminal_tasks.add(task.id)
            self.open_deals.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


class VersionCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            version = request.headers.get("A2A-Version")
            if version != "1.0":
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32009,
                        "message": "Version Not Supported"
                    }
                })
        return await call_next(request)


def build_app(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description=cfg["skill_description"],
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=SellerExecutor(cfg), task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))
    app.add_middleware(VersionCheckMiddleware)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    cfg = dict(DEFAULT_CONFIG)
    if args.config:
        try:
            cfg.update(json.load(open(args.config, encoding="utf-8")))
        except Exception:
            pass
    if args.port:
        cfg["port"] = args.port
    uvicorn.run(build_app(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
