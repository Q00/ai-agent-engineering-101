"""A rule-based seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller_agent.py                       # lamp shop on :9999
    python seller_agent.py --config cards/b.json # another seller from a config file

No model inside: the seller asks for an offer (INPUT_REQUIRED), counters, and
either closes with a receipt artifact (COMPLETED) or walks away (REJECTED).
"""
import argparse
import json
import math
import re

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

DEFAULT = {
    "name": "Lamp Shop",
    "description": "Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
    "skill_description": "Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
    "max_rounds": 3,
}


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open = {}  # task id -> {"item", "ask", "rounds"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:  # first message: which item?
            item = next((k for k in self.cfg["reserve"] if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(self.cfg["reserve"]) + "."))
                return
            ask = math.ceil(self.cfg["reserve"][item] * 1.3)
            self.open[task.id] = {"item": item, "ask": ask, "rounds": 0}
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say("Send your offer as a number."))
            return
        offer, reserve = int(m.group()), self.cfg["reserve"][deal["item"]]
        if offer >= reserve:
            receipt = {"seller": self.cfg["name"], "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            del self.open[task.id]
            return
        deal["rounds"] += 1
        if deal["rounds"] >= self.cfg["max_rounds"]:
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
            return
        deal["ask"] = max(reserve, (offer + deal["ask"]) // 2)
        await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description=cfg["skill_description"],
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(cfg), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.load(open(args.config)))
    if args.port:
        cfg["port"] = args.port
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
