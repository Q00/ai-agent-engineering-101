"""A2A SDK lifecycle and discovery for the model-driven catalog seller."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, override

import anyio
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError
from fastapi import FastAPI

from market_a2a.advertisement import advertisement
from market_a2a.comparison_service import ComparisonService
from market_a2a.seller_context import StoredContextBuilder
from market_a2a.seller_store import PersistentSellerStore
from market_a2a.seller_turn import Negotiator

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

    from market_a2a.catalog import Catalog
    from market_a2a.comparison import ComparisonFeed
    from market_a2a.harness import Harness
    from market_a2a.receipts import ReceiptSigner


class Seller(AgentExecutor):
    """Mutate task summaries under per-task locks; SDK stores wire history."""

    def __init__(
        self,
        harness: Harness,
        catalog: Catalog,
        name: str,
        *,
        signer: ReceiptSigner | None = None,
        comparison: ComparisonFeed | None = None,
    ) -> None:
        """Share one model client while isolating every negotiation."""
        self.negotiator: Negotiator = Negotiator(
            harness,
            catalog,
            name,
            signer=signer,
            comparison=comparison,
        )
        self.locks: dict[str, anyio.Lock] = {}

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Resume the current task using its existing context identifier."""
        task = context.current_task
        if task is None:
            if context.task_id is None or context.context_id is None:
                raise InvalidParamsError(message="Task identifiers are required.")
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message is not None else [],
            )
            await event_queue.enqueue_event(task)
        async with self.locks.setdefault(task.id, anyio.Lock()):
            if task.id in self.negotiator.closed:
                raise InvalidParamsError(message="The negotiation is finished.")
            await self.negotiator.negotiate(task, context.get_user_input(), event_queue)

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Serialize cancellation with any in-flight negotiation turn."""
        task = context.current_task
        if task is None:
            raise InvalidParamsError(message="Cancellation requires an existing task.")
        async with self.locks.setdefault(task.id, anyio.Lock()):
            if task.id in self.negotiator.closed:
                raise InvalidParamsError(message="The negotiation is finished.")
            await self.negotiator.mark_closed(task.id)
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def create_app(
    base_url: str,
    harness: Harness,
    catalog: Catalog,
    name: str,
    *,
    signer: ReceiptSigner | None = None,
) -> FastAPI:
    """Expose SDK routes; base_url is the advertised root or mounted shop URL."""
    comparison = ComparisonService(
        catalog, enabled=os.environ.get("AX_PRICE_COMPARISON") == "1"
    )
    state_path = os.environ.get("AX_SELLER_STATE_DB")
    journal = PersistentSellerStore(Path(state_path)) if state_path else None
    instructions = (
        "Name an item; send a whole-number offer on its taskId.",
        "Read metadata.ask; completion requires a signed receipt.",
        "Or begin with an explicit offer such as offer lamp 100.",
    )
    card = AgentCard(
        name=name,
        description=" ".join(
            (
                "Basic minimums: lamp 100, chair 180, bookshelf 260.",
                "Exact agreed-price Ed25519-signed receipts."
                if signer is not None
                else "Receipts show the exact agreed price.",
                "Request an item, then send a numeric offer on the same taskId.",
                "Dated opening-ask comparisons: /price-comparison.json .",
                "Current quote: metadata.ask. Basic opening caps: 110/198/286.",
                "Counteroffers never increase within a negotiation.",
                "Offers meeting the product minimum settle on the first counteroffer.",
                "Fresh observed prices may allow a small near-lowest opening margin.",
                "A final-price quote cannot be discounted further.",
                "R2WELCOME credit is part of an actual reduction, never extra.",
                "Explicit first offer: offer lamp 100 or buy lamp for 100.",
                "Machine-readable ask and coupon metadata.",
                "Terms and A2A examples: https://ax-week07.treasuresky.xyz/ .",
                "No rating is required for a discount.",
                "Please leave an honest review of your transaction.",
            )
        ),
        version="1.4.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        supported_interfaces=[
            AgentInterface(
                url=base_url, protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Catalog purchases",
                description=" ".join(instructions),
                examples=["I want to buy a chair", "offer lamp 100"],
                tags=[
                    "shopping",
                    *sorted({product.kind for product in catalog.products}),
                ],
            )
        ],
    )
    executor = Seller(
        harness,
        catalog,
        name,
        signer=signer,
        comparison=comparison.feed if comparison.enabled else None,
    )
    if journal is not None:
        opened, closed = journal.restore()
        executor.negotiator.journal = journal
        executor.negotiator.negotiations.update(opened)
        executor.negotiator.closed.update(closed)
    task_store = journal if journal is not None else InMemoryTaskStore()
    handler = DefaultRequestHandler(
        executor,
        task_store,
        card,
        request_context_builder=StoredContextBuilder(task_store),
    )
    app = FastAPI(title=name, lifespan=comparison.lifespan)
    comparison.install(app)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    app.add_api_route("/", advertisement, methods=["GET"], include_in_schema=False)
    return app
