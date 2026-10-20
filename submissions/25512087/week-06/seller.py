"""Harness-driven A2A v1 seller server for the Week 06 trade test."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Any

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

from harness import CommandHarness
from policy import SellerPolicy


RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def find_item(text: str) -> str | None:
    """Return the supported item named in a buyer message."""
    lowered = text.lower()
    return next((item for item in RESERVES if item in lowered), None)


def build_card(port: int) -> AgentCard:
    """Build the public Agent Card independently of private reserves."""
    return AgentCard(
        name="Harness Furniture Shop",
        description="A harness-driven shop for lamps, chairs, and bookshelves.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://127.0.0.1:{port}/",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate-furniture",
                name="Negotiate a furniture purchase",
                description="Name a product, then continue the task with numeric offers.",
                tags=["shopping", "negotiation", "furniture"],
                examples=["I want to buy a lamp", "110"],
            )
        ],
    )


class SellerAgent(AgentExecutor):
    """Translate A2A tasks to decisions made by the shared harness adapter."""

    def __init__(self, policy: SellerPolicy) -> None:
        self.policy = policy
        self.open: dict[str, dict[str, Any]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        say = lambda text: updater.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:
            item = find_item(text)
            if item is None:
                await updater.reject(say("We only sell: lamp, chair, bookshelf."))
                return
            reserve = RESERVES[item]
            decision = await asyncio.to_thread(self.policy.opening, item, reserve)
            ask = max(reserve, int(decision.price or reserve))
            self.open[task.id] = {"item": item, "ask": ask, "rounds": 0}
            self._log(task.id, "opening", item=item, ask=ask, reserve=reserve)
            await updater.requires_input(
                say(f"{item}: asking {ask}. Send your offer as a number.")
            )
            return

        match = re.search(r"\d+", text)
        if match is None:
            await updater.requires_input(say("Send your offer as a number."))
            return
        offer = int(match.group())
        item = str(deal["item"])
        reserve = RESERVES[item]
        decision = await asyncio.to_thread(
            self.policy.respond,
            item,
            reserve,
            int(deal["ask"]),
            offer,
            int(deal["rounds"]),
        )
        self._log(
            task.id,
            "response",
            item=item,
            offer=offer,
            reserve=reserve,
            decision=decision.action,
            decision_price=decision.price,
        )
        if decision.action == "accept":
            receipt = {"seller": "Harness Furniture Shop", "item": item, "price": offer}
            await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await updater.complete(say(f"Deal: {item} at {offer}."))
            self.open.pop(task.id, None)
            return
        if decision.action == "reject":
            await updater.reject(say(f"No deal at {offer}."))
            self.open.pop(task.id, None)
            return
        deal["rounds"] = int(deal["rounds"]) + 1
        deal["ask"] = max(reserve, int(decision.price or reserve))
        await updater.requires_input(
            say(f"{offer} is too low. Counteroffer: {deal['ask']}.")
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            return
        self.open.pop(task.id, None)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await updater.cancel()

    @staticmethod
    def _log(task_id: str, event: str, **values: object) -> None:
        print(
            json.dumps(
                {"event": event, "task_id": task_id, **values},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )


def build_app(port: int, harness_command: str | None = None) -> Starlette:
    card = build_card(port)
    policy = SellerPolicy(CommandHarness(harness_command))
    handler = DefaultRequestHandler(
        agent_executor=SellerAgent(policy),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--harness-cmd")
    args = parser.parse_args()
    uvicorn.run(
        build_app(args.port, args.harness_cmd),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

