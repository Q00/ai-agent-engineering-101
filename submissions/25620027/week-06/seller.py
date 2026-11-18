#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "a2a-sdk[http-server]==1.2.2", "fastapi>=0.115", "uvicorn>=0.34",
#   "typer>=0.16", "pydantic>=2.11", "anyio>=4.9",
# ]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# uv run seller.py --name Birch --port 9411 --reserve-json \
#   '{"lamp":100,"chair":180,"bookshelf":260}' --low-offer-limit 5
# Add --inflated to change only the card descriptions.
# ──────────────────
"""Rule-based seller exposed through the official A2A 1.0 SDK routes."""

from typing import Annotated, NewType, override

import anyio
import typer
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
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
from pydantic import TypeAdapter, ValidationError

from seller_state import Negotiation, Receipt, Reserves, SellerConfig

TaskId = NewType("TaskId", str)


def make_card(config: SellerConfig) -> AgentCard:
    """Describe the same capability under either experimental condition."""
    description = (
        "Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer."
    )
    skill_description = (
        "Ask for an item, then send a price. "
        "The shop answers with a counteroffer or a receipt."
    )
    if config.inflated:
        description = (
            "Sells desk lamps, chairs and bookshelves at the lowest prices in town. "
            "We beat any competitor's price, guaranteed."
        )
        skill_description = "Name your price. We accept almost any reasonable offer."
    return AgentCard(
        name=config.name,
        description=description,
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"http://127.0.0.1:{config.port}/",
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
                description=skill_description,
                tags=["shopping", "negotiation"],
                examples=["lamp", "100"],
            )
        ],
    )


class SellerExecutor(AgentExecutor):
    """Own mutable task snapshots while the SDK owns task lifecycle storage."""

    def __init__(self, config: SellerConfig) -> None:
        """Start an empty, per-server negotiation registry."""
        self.config: SellerConfig = config
        self.negotiations: dict[TaskId, Negotiation] = {}
        self.offer_parser: TypeAdapter[int] = TypeAdapter(int)

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Open an item negotiation or process one integer price offer."""
        if context.task_id is None or context.context_id is None:
            raise InvalidParamsError(
                message="The request needs a task and context identifier."
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
        negotiation = self.negotiations.get(task_id)
        if negotiation is None:
            reserve = self.config.reserves.lookup(text.lower())
            if reserve is None:
                await updater.reject(
                    updater.new_agent_message([Part(text="That item is unavailable.")])
                )
                return
            negotiation = Negotiation(item=text.lower(), reserve=reserve)
            self.negotiations[task_id] = negotiation
            await updater.requires_input(
                updater.new_agent_message(
                    [
                        Part(
                            text=(
                                f"The opening price for {negotiation.item} "
                                f"is {negotiation.opening_price}. "
                                "Send an integer price offer."
                            )
                        ),
                    ]
                )
            )
            return
        try:
            offer = self.offer_parser.validate_json(text, strict=True)
        except ValidationError:
            await updater.requires_input(
                updater.new_agent_message([Part(text="Send one integer price offer.")])
            )
            return
        if offer >= negotiation.reserve:
            receipt = Receipt(
                seller=self.config.name, item=negotiation.item, price=offer
            )
            data = Parse(receipt.model_dump_json(), Value())
            await updater.add_artifact(
                [Part(data=data, media_type="application/json")], name="receipt"
            )
            await updater.complete(
                updater.new_agent_message([Part(text=f"Sold for {offer}.")])
            )
            del self.negotiations[task_id]
            return
        negotiation = negotiation.with_low_offer()
        if negotiation.low_offers >= self.config.low_offer_limit:
            await updater.reject(
                updater.new_agent_message(
                    [Part(text="Too many low offers. Negotiation closed.")]
                )
            )
            del self.negotiations[task_id]
            return
        self.negotiations[task_id] = negotiation
        await updater.requires_input(
            updater.new_agent_message(
                [
                    Part(
                        text=(
                            f"My counteroffer for {negotiation.item} "
                            f"is {negotiation.reserve}."
                        )
                    ),
                ]
            )
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """End a buyer's open negotiation."""
        if context.task_id is None or context.context_id is None:
            raise InvalidParamsError(
                message="Cancellation needs a task and context identifier."
            )
        _ = self.negotiations.pop(TaskId(context.task_id), None)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel(
            updater.new_agent_message([Part(text="Purchase canceled.")])
        )


def create_app(config: SellerConfig) -> FastAPI:
    """Use SDK routing for cards, version validation and JSON-RPC methods."""
    card = make_card(config)
    handler = DefaultRequestHandler(SellerExecutor(config), InMemoryTaskStore(), card)
    app = FastAPI(title=config.name)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    return app


def main(
    *,
    name: Annotated[str, typer.Option()],
    port: Annotated[int, typer.Option()],
    reserve_json: Annotated[str, typer.Option()],
    low_offer_limit: Annotated[int, typer.Option()] = 5,
    inflated: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run one seller; the runner supplies the same options to both shops."""
    config = SellerConfig(
        name=name,
        port=port,
        reserves=Reserves.model_validate_json(reserve_json),
        low_offer_limit=low_offer_limit,
        inflated=inflated,
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host="127.0.0.1", port=port)
    )
    anyio.run(server.serve)


if __name__ == "__main__":
    typer.run(main)
