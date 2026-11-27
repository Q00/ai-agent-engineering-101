"""A2A v1.0 Seller Agent driven by Codex CLI harness.
Sells lamp (reserve 100), chair (reserve 180), and bookshelf (reserve 260).
"""
import argparse
import math
import os
import re
import subprocess
import tempfile
import uvicorn
from starlette.applications import Starlette

from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

RESERVES = {
    "lamp": 100,
    "chair": 180,
    "bookshelf": 260,
}


def call_harness(prompt: str) -> str:
    """Call Codex CLI harness in an empty directory with fallback protection."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp_path = tmp.name
    empty_dir = tempfile.gettempdir()
    p = None
    try:
        cmd = f'codex exec --skip-git-repo-check --ephemeral --cd "{empty_dir}" -m gpt-5.6-sol -o "{tmp_path}" -'
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
        )
        p.communicate(input=prompt, timeout=20)
        if os.path.exists(tmp_path):
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            return content
    except Exception as e:
        print(f"[Seller Harness Warning]: {e}")
        if p:
            try:
                p.kill()
            except Exception:
                pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return ""


class Seller(AgentExecutor):
    def __init__(self, port: int):
        self.port = port
        self.open = {}  # task_id -> {"item": str, "ask": int, "rounds": int}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""

        deal = self.open.get(task.id)
        if deal is None:
            # First message: identify item
            item = next((k for k in RESERVES if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: lamp, chair, bookshelf."))
                return

            reserve = RESERVES[item]
            default_ask = math.ceil(reserve * 1.3)

            prompt = (
                f"You are a merchant selling a {item}.\n"
                f"The minimum reserve price is {reserve}.\n"
                f"Decide your opening asking price (must be >= {default_ask}).\n"
                f"Answer with ONLY the integer number."
            )
            resp = call_harness(prompt)
            nums = re.findall(r"\d+", resp)
            if nums:
                ask = max(reserve, int(nums[-1]))
            else:
                ask = default_ask

            self.open[task.id] = {"item": item, "ask": ask, "rounds": 0}
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
            return

        # Subsequent messages: buyer's offer
        m = re.findall(r"\d+", text)
        if not m:
            await up.requires_input(say(f"Send your offer as a number. Current ask is {deal['ask']}."))
            return

        offer = int(m[-1])
        item = deal["item"]
        reserve = RESERVES[item]
        deal["rounds"] += 1

        prompt = (
            f"You are a merchant negotiating a {item}.\n"
            f"Reserve price: {reserve}. Your previous ask: {deal['ask']}.\n"
            f"Buyer's offer: {offer}. Negotiation round: {deal['rounds']}/5.\n"
            f"Rules:\n"
            f"1. If offer >= {reserve} and you accept the price, reply: DEAL {offer}\n"
            f"2. If offer < {reserve}, you CANNOT accept. Propose a counteroffer >= {reserve}, reply: COUNTER <number>\n"
            f"3. If round >= 5 or offer is too low to negotiate, reply: REJECT\n"
            f"Reply with ONLY one of these formats."
        )
        resp = call_harness(prompt).upper()

        # Guardrail: never accept below reserve
        if ("DEAL" in resp and offer >= reserve) or (offer >= reserve and deal["rounds"] >= 2):
            receipt = {"seller": "Codex Shop", "item": item, "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {offer}."))
            del self.open[task.id]
            return

        if deal["rounds"] >= 5 or "REJECT" in resp:
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
            return

        counter_nums = re.findall(r"\d+", resp)
        if counter_nums and int(counter_nums[-1]) >= reserve:
            new_ask = int(counter_nums[-1])
        else:
            new_ask = max(reserve, (offer + deal["ask"]) // 2)

        deal["ask"] = new_ask
        await up.requires_input(say(f"{offer} is too low. Counteroffer: {new_ask}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            self.open.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name="Codex Shop",
        description="Sells desk lamps, chairs, and bookshelves. Negotiates price driven by Codex harness.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description="Ask for an item, then negotiate price. Answers with counteroffer or receipt.",
                tags=["shopping", "negotiation"],
                examples=["I want to buy a lamp", "90"],
            )
        ],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(port), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
