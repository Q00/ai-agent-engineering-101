"""Stateful A2A seller whose tool loop owns all commercial decisions."""

# allow: SIZE_OK — one per-task tool/state machine keeps guards before mutation;
# wire publication is already isolated in seller_messages.py.

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, assert_never

from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task
from pydantic import JsonValue, TypeAdapter, ValidationError

from market_a2a.catalog_constraints import compatible
from market_a2a.comparison import compare_price
from market_a2a.models import HarnessError, Product, ToolResult, json_value
from market_a2a.seller_history import SellerHistory
from market_a2a.seller_messages import (
    AgreedSale,
    QuoteNotice,
    complete_sale,
    quote_message,
)
from market_a2a.seller_policy import (
    PROMPT,
    TOOLS,
    Decision,
    Negotiation,
    Search,
    Turn,
    decision_feedback,
    price_feedback,
    search_result,
    settlement_required,
)

if TYPE_CHECKING:
    from a2a.server.events import EventQueue

    from market_a2a.catalog import Catalog
    from market_a2a.comparison import ComparisonFeed
    from market_a2a.harness import Harness
    from market_a2a.receipts import ReceiptSigner


class Negotiator:
    """Own bounded seller summaries and validated model tool actions."""

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
        self.harness: Harness = harness
        self.catalog: Catalog = catalog
        self.name: str = name
        self.signer: ReceiptSigner | None = signer
        self.comparison: ComparisonFeed | None = comparison
        self.negotiations: dict[str, Negotiation] = {}
        self.closed: set[str] = set()
        self.price_history: SellerHistory = SellerHistory()

    async def negotiate(self, task: Task, text: str, queue: EventQueue) -> None:
        """Expose retrieval and guarded commercial actions for one model turn."""
        updater = TaskUpdater(queue, task.id, task.context_id)
        current = self.negotiations.get(task.id)
        snapshot = self.comparison.snapshot if self.comparison is not None else None
        candidates: dict[str, Product] = {}
        offer = int(text.strip()) if re.fullmatch(r"[0-9]{1,9}", text.strip()) else None
        if current is not None and offer is not None:
            current = replace(current, incoming_offers=current.incoming_offers + 1)
            self.negotiations[task.id] = current
        state: dict[str, JsonValue] = {
            "phase": "bargaining" if current else "opening",
            "buyer_text": text[:2000] if current is None else None,
            "offer": offer,
            "offer_meets_reserve": (
                offer is not None
                and current is not None
                and offer >= current.product.reserve
            ),
            "offer_meets_ask": (
                offer is not None and current is not None and offer >= current.ask
            ),
            "catalog_count": len(self.catalog.products),
            "selected": json_value(current.product) if current else None,
            "ask": current.ask if current else None,
            "coupon_used": current.coupon_used if current else False,
            "own_price_history": (
                json_value(self.price_history.snapshot(current.product.sku))
                if current
                else None
            ),
            "rounds": current.rounds if current else 0,
            "incoming_offers": current.incoming_offers if current else 0,
            "settlement_required": settlement_required(current, offer),
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
                        return search_result(found, self.price_history, snapshot)
                    case "decide":
                        decision = Decision.model_validate_json(arguments)
                        return await self.commit(
                            Turn(
                                task.id, updater, current, candidates, offer, snapshot
                            ),
                            decision,
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

    async def quote(self, turn: Turn, product: Product, decision: Decision) -> None:
        """Publish a guarded quote with transparent coupon accounting."""
        current = turn.current
        coupon_applied = (
            current is not None
            and not current.coupon_used
            and decision.price < current.ask
        )
        self.negotiations[turn.task_id] = Negotiation(
            product,
            decision.price,
            current.rounds + 1 if current else 0,
            coupon_applied or (current.coupon_used if current else False),
            current.incoming_offers if current else 0,
        )
        message = quote_message(
            turn.updater,
            QuoteNotice(
                product,
                decision.price,
                current.ask if current else None,
                coupon_applied,
                turn.snapshot,
            ),
        )
        await turn.updater.requires_input(message)

    async def commit(self, turn: Turn, decision: Decision) -> ToolResult:
        """Commit one guarded model action to the authoritative task."""
        updater, current = turn.updater, turn.current
        feedback = decision_feedback(current, turn.offer, decision)
        if feedback is not None:
            return feedback
        match decision.action:
            case "reject":
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
        price_error = price_feedback(
            product.reserve,
            decision,
            current_ask=current.ask if current else None,
            opening_limit=compare_price(product, turn.snapshot).ceiling,
        )
        if price_error is not None:
            guard = f"seller.guard task={turn.task_id} code={price_error.code}"
            self.harness.emit(
                f"{guard} price={decision.price} reserve={product.reserve}"
            )
            return ToolResult(data=json_value(price_error))
        match decision.action:
            case "quote":
                await self.quote(turn, product, decision)
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
                await complete_sale(
                    updater,
                    AgreedSale(product, decision.price, turn.task_id, self.name),
                    self.signer,
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
        self.price_history.record(decision.action, product.sku, decision.price)
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
