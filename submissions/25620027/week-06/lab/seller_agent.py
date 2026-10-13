#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.11"
# dependencies = [
#   "a2a-sdk[http-server]==1.2.2", "fastapi==0.143.0", "uvicorn==0.54.0",
#   "pydantic==2.14.1", "anyio>=4.9,<5",
# ]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# uv run --frozen seller_agent.py
# ──────────────────
"""Textbook checkpoint reconstruction; the omitted counter policy is explicit."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final, NewType, override

import anyio
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError
from fastapi import FastAPI
from google.protobuf.json_format import Parse
from google.protobuf.struct_pb2 import Value
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

TaskId = NewType("TaskId", str)
LOW_OFFER_LIMIT: Final = 3
DESCRIPTION: Final = (
    "Sells desk lamps, chairs and bookshelves.",
    "Negotiates the price with the buyer.",
)
SKILL_DESCRIPTION: Final = (
    "Ask for an item, then send a price.",
    "The shop answers with a counteroffer or a receipt.",
)


@dataclass(frozen=True, slots=True)
class Product:
    """Fixed demonstration prices; counter prices reconstruct the omitted branch."""

    name: str
    reserve: int
    counter: int

    @property
    def opening(self) -> int:
        """Round 130 percent upward, as specified in the teaching fragment."""
        return (self.reserve * 13 + 9) // 10


PRODUCTS: Final = (
    Product("lamp", 100, 105),
    Product("chair", 180, 180),
    Product("bookshelf", 260, 260),
)


@dataclass(frozen=True, slots=True)
class Negotiation:
    """One immutable snapshot of an open transaction."""

    product: Product
    rounds: int = 0


class Receipt(BaseModel):
    """Machine-readable completed purchase artifact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    seller: str = "Lamp Shop"
    item: str
    price: int = Field(ge=0)


class Seller(AgentExecutor):
    """Hold mutable negotiations while the SDK owns task and history storage."""

    def __init__(self) -> None:
        """Start one independent in-memory seller."""
        self.negotiations: dict[TaskId, Negotiation] = {}

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Interpret natural-language item requests and numeric follow-up offers."""
        if context.task_id is None or context.context_id is None:
            raise InvalidParamsError(
                message="Task and context identifiers are required."
            )
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message] if context.message is not None else [],
                )
            )
        task_id = TaskId(context.task_id)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        text = context.get_user_input().strip()
        deal = self.negotiations.get(task_id)
        if deal is None:
            product = next(
                (product for product in PRODUCTS if product.name in text.lower()), None
            )
            if product is None:
                await updater.reject(
                    updater.new_agent_message(
                        [Part(text="We only sell: lamp, chair, bookshelf.")]
                    )
                )
                return
            self.negotiations[task_id] = Negotiation(product)
            await updater.requires_input(
                updater.new_agent_message(
                    [
                        Part(
                            text="".join(
                                (
                                    f"{product.name}: asking {product.opening}. ",
                                    "Send your offer as a number.",
                                )
                            )
                        )
                    ]
                )
            )
            return
        number = re.search(r"\d+", text)
        if number is None:
            await updater.requires_input(
                updater.new_agent_message([Part(text="Send your offer as a number.")])
            )
            return
        offer = int(number.group())
        if offer >= deal.product.reserve:
            receipt = Receipt(item=deal.product.name, price=offer)
            await updater.add_artifact(
                [
                    Part(
                        data=Parse(receipt.model_dump_json(), Value()),
                        media_type="application/json",
                    )
                ],
                name="receipt",
            )
            await updater.complete(
                updater.new_agent_message(
                    [Part(text=f"Deal: {deal.product.name} at {offer}.")]
                )
            )
            del self.negotiations[task_id]
            return
        updated = replace(deal, rounds=deal.rounds + 1)
        if updated.rounds >= LOW_OFFER_LIMIT:
            await updater.reject(
                updater.new_agent_message([Part(text="Three low offers. No deal.")])
            )
            del self.negotiations[task_id]
            return
        self.negotiations[task_id] = updated
        counter_text = f"{offer} is too low. Counteroffer: {deal.product.counter}."
        await updater.requires_input(
            updater.new_agent_message([Part(text=counter_text)])
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel an open task through the SDK task lifecycle."""
        if context.task_id is None or context.context_id is None:
            raise InvalidParamsError(
                message="Task and context identifiers are required."
            )
        _ = self.negotiations.pop(TaskId(context.task_id), None)
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def create_app() -> FastAPI:
    """Expose the teaching seller on the official A2A 1.0 SDK routes."""
    card = AgentCard(
        name="Lamp Shop",
        description=" ".join(DESCRIPTION),
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url="http://127.0.0.1:9999/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=" ".join(SKILL_DESCRIPTION),
                tags=["shopping", "negotiation"],
                examples=["I want to buy a lamp", "90"],
            )
        ],
    )
    handler = DefaultRequestHandler(Seller(), InMemoryTaskStore(), card)
    app = FastAPI(title="Lamp Shop")
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    return app


def main() -> None:
    """Serve the lab endpoint; restarting clears its in-memory tasks."""
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=9999))
    anyio.run(server.serve)


if __name__ == "__main__":
    main()
