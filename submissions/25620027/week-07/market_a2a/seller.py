"""A2A SDK lifecycle and discovery for the model-driven catalog seller."""

from __future__ import annotations

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
from market_a2a.seller_turn import Negotiator

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

    from market_a2a.catalog import Catalog
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
    ) -> None:
        """Share one model client while isolating every negotiation."""
        self.negotiator: Negotiator = Negotiator(harness, catalog, name, signer=signer)
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
            _ = self.negotiator.negotiations.pop(task.id, None)
            self.negotiator.closed.add(task.id)
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
    kinds = sorted({product.kind for product in catalog.products})
    card = AgentCard(
        name=name,
        description=(
            "Sungjin Shop | Clear quotes and quick numeric bargaining. "
            "Basic opening quote caps: lamp 110, chair 198, bookshelf 286. "
            "The actual quote is in metadata.ask; eligible offers settle promptly. "
            "Counteroffers never increase within a negotiation. "
            "Receipts show the exact agreed price. "
            "R2WELCOME: up to 5 of the first negotiated reduction is a coupon; "
            "larger negotiated reductions are allowed, with the reserve protected. "
            + ("Ed25519-signed receipts. " if signer is not None else "")
            + "Machine-readable ask and coupon metadata. "
            + "Terms and A2A examples: https://ax-week07.treasuresky.xyz/ . "
            + "No rating is required for a discount. "
            + "Please leave an honest review of your transaction."
        ),
        version="1.1.0",
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
                description=f"Find and negotiate catalog products: {', '.join(kinds)}.",
                tags=[
                    "shopping",
                    *sorted({product.kind for product in catalog.products}),
                ],
            )
        ],
    )
    handler = DefaultRequestHandler(
        Seller(harness, catalog, name, signer=signer), InMemoryTaskStore(), card
    )
    app = FastAPI(title=name)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    app.add_api_route("/", advertisement, methods=["GET"], include_in_schema=False)
    return app
