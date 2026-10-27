"""Stateful A2A seller whose tool loop owns all commercial decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never

from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task
from google.protobuf.json_format import Parse
from google.protobuf.struct_pb2 import Value
from pydantic import JsonValue, TypeAdapter, ValidationError

from market_a2a.catalog_constraints import compatible
from market_a2a.models import (
    HarnessError,
    Product,
    ToolResult,
    json_value,
)
from market_a2a.seller_policy import PROMPT, TOOLS, Decision, Search, rejection_feedback

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

    from market_a2a.catalog import Catalog
    from market_a2a.harness import Harness


@dataclass(frozen=True, slots=True)
class Negotiation:
    """Keep one bounded commercial summary instead of replaying task history."""

    product: Product
    ask: int
    rounds: int = 0


@dataclass(frozen=True, slots=True)
class Turn:
    """Validated offer and short candidate set for one task action."""

    task_id: str
    updater: TaskUpdater
    current: Negotiation | None
    candidates: dict[str, Product]
    offer: int | None


class Negotiator:
    """Own bounded seller summaries and validated model tool actions."""

    def __init__(self, harness: Harness, catalog: Catalog, name: str) -> None:
        """Share one model client while isolating every negotiation."""
        self.harness: Harness = harness
        self.catalog: Catalog = catalog
        self.name: str = name
        self.negotiations: dict[str, Negotiation] = {}
        self.closed: set[str] = set()

    async def negotiate(self, task: Task, text: str, queue: EventQueue) -> None:
        """Expose retrieval and guarded commercial actions for one model turn."""
        updater = TaskUpdater(queue, task.id, task.context_id)
        current = self.negotiations.get(task.id)
        candidates: dict[str, Product] = {}
        offer = int(text.strip()) if re.fullmatch(r"[0-9]{1,9}", text.strip()) else None
        state: dict[str, JsonValue] = {
            "phase": "bargaining" if current else "opening",
            "buyer_text": text[:2000],
            "offer": offer,
            "offer_meets_reserve": (
                offer is not None
                and current is not None
                and offer >= current.product.reserve
            ),
            "catalog_count": len(self.catalog.products),
            "task_id": task.id,
            "selected": json_value(current.product) if current else None,
            "ask": current.ask if current else None,
            "rounds": current.rounds if current else 0,
        }
        self.harness.emit(
            "".join(
                (
                    f"seller.summary task={task.id} ",
                    f"selected={current.product.sku if current else '-'} ",
                    f"rounds={state['rounds']} offer={offer} history_replayed=0",
                )
            )
        )

        async def dispatch(name: str, arguments: str) -> ToolResult:
            try:
                tool = TypeAdapter(Literal["search_catalog", "decide"]).validate_python(
                    name
                )
                match tool:
                    case "search_catalog":
                        query = Search.model_validate_json(arguments)
                        found = tuple(
                            product
                            for product in self.catalog.search(query.query)
                            if current is not None or compatible(product, text)
                        )
                        candidates.clear()
                        candidates.update((product.sku, product) for product in found)
                        self.harness.emit(
                            "".join(
                                (
                                    f"seller.search task={task.id} ",
                                    f"candidates={len(found)} ",
                                    f"catalog={len(self.catalog.products)}",
                                )
                            )
                        )
                        return ToolResult(
                            data=[json_value(product) for product in found]
                        )
                    case "decide":
                        decision = Decision.model_validate_json(arguments)
                        return await self.commit(
                            Turn(task.id, updater, current, candidates, offer), decision
                        )
                    case _:
                        assert_never(tool)
            except ValidationError:
                return ToolResult(
                    data={"error": "Invalid tool arguments; correct their schema."}
                )

        phase = (
            "OPENING: no offer exists; search and QUOTE the initial asking price."
            if current is None
            else "BARGAINING: use the selected product and the actual numeric offer."
        )
        try:
            _ = await self.harness.run(phase + "\n" + PROMPT, state, TOOLS, dispatch)
        except HarnessError:
            self.harness.emit(
                f"seller.failure task={task.id} code=model_loop_exhausted"
            )
            await updater.failed(
                updater.new_agent_message(
                    [Part(text="Unable to choose a valid action.")]
                )
            )
            self.closed.add(task.id)
            _ = self.negotiations.pop(task.id, None)

    async def commit(self, turn: Turn, decision: Decision) -> ToolResult:
        """Commit one guarded model action to the authoritative task."""
        updater, current = turn.updater, turn.current
        match decision.action:
            case "reject":
                feedback = rejection_feedback(
                    current.rounds if current else None, turn.offer
                )
                if feedback is not None:
                    return feedback
                await updater.reject(
                    updater.new_agent_message([Part(text=decision.reason)])
                )
                self.closed.add(turn.task_id)
                _ = self.negotiations.pop(turn.task_id, None)
                return ToolResult(data={"state": "rejected"}, terminal=True)
            case "quote" | "accept":
                pass
            case _:
                assert_never(decision.action)
        product = current.product if current else turn.candidates.get(decision.sku)
        if product is None or product.sku != decision.sku:
            return ToolResult(
                data={"error": "Select a retrieved SKU; never switch an open SKU."}
            )
        if decision.price < product.reserve:
            self.harness.emit(
                "".join(
                    (
                        f"seller.guard task={turn.task_id} code=reserve ",
                        f"price={decision.price} reserve={product.reserve}",
                    )
                )
            )
            return ToolResult(
                data={"error": "Below reserve; choose a valid price or reject."}
            )
        match decision.action:
            case "quote":
                self.negotiations[turn.task_id] = Negotiation(
                    product, decision.price, current.rounds + 1 if current else 0
                )
                message = updater.new_agent_message(
                    [Part(text=f"{product.name}. Asking price: {decision.price}")],
                    metadata={
                        "sku": product.sku,
                        "item": product.kind,
                        "name": product.name,
                    },
                )
                await updater.requires_input(message)
            case "accept":
                if (
                    current is None
                    or turn.offer is None
                    or decision.price != turn.offer
                ):
                    return ToolResult(
                        data={
                            "error": "No matching offer. Use quote to state a price."
                        },
                    )
                receipt = {
                    "seller": self.name,
                    "item": product.kind,
                    "sku": product.sku,
                    "price": decision.price,
                }
                await updater.add_artifact(
                    [
                        Part(
                            data=Parse(json.dumps(receipt), Value()),
                            media_type="application/json",
                        )
                    ],
                    name="receipt",
                )
                await updater.complete(
                    updater.new_agent_message(
                        [Part(text=f"Deal: {product.name} at {decision.price}.")]
                    )
                )
                self.closed.add(turn.task_id)
                _ = self.negotiations.pop(turn.task_id, None)
                self.harness.emit(
                    "".join(
                        (
                            f"seller.receipt task={turn.task_id} ",
                            f"sku={product.sku} price={decision.price}",
                        )
                    )
                )
            case _:
                assert_never(decision.action)
        self.harness.emit(
            "".join(
                (
                    f"seller.action task={turn.task_id} action={decision.action} ",
                    f"sku={product.sku} price={decision.price}",
                )
            )
        )
        return ToolResult(
            data={"action": decision.action, "price": decision.price}, terminal=True
        )
