"""Stateful A2A seller whose tool loop owns all commercial decisions."""

# SIZE_OK: one per-task tool/state machine keeps guards before mutation;
# wire publication is already isolated in seller_messages.py.

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, Literal, assert_never

from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task
from pydantic import JsonValue, TypeAdapter, ValidationError

from market_a2a.catalog_constraints import compatible
from market_a2a.comparison import compare_price
from market_a2a.models import HarnessError, Product, ToolResult, json_value
from market_a2a.round_mode import margin_mode
from market_a2a.seller_history import SellerHistory
from market_a2a.seller_messages import (
    AgreedSale,
    QuoteNotice,
    complete_sale,
    quote_message,
)
from market_a2a.seller_offer import (
    accepts_quote,
    bargaining_offer,
    basic_request,
    opening_offer,
)
from market_a2a.seller_policy import (
    PROMPT,
    TOOLS,
    Decision,
    Negotiation,
    Search,
    Turn,
    decision_feedback,
    opening_settlement_feedback,
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
    from market_a2a.seller_store import PersistentSellerStore


class Negotiator:
    """Own bounded seller summaries and validated model tool actions."""

    def __init__(  # noqa: PLR0913 - independent optional adapter capabilities
        self,
        harness: Harness,
        catalog: Catalog,
        name: str,
        *,
        signer: ReceiptSigner | None = None,
        comparison: ComparisonFeed | None = None,
        journal: PersistentSellerStore | None = None,
    ) -> None:
        """Share one model client while isolating every negotiation."""
        self.harness: Harness = harness
        self.catalog: Catalog = catalog
        self.name: str = name
        self.journal: PersistentSellerStore | None = journal
        self.signer: ReceiptSigner | None = signer
        self.comparison: ComparisonFeed | None = comparison
        self.negotiations: dict[str, Negotiation] = {}
        self.closed: set[str] = set()
        self.price_history: SellerHistory = SellerHistory()

    async def negotiate(self, task: Task, text: str, queue: EventQueue) -> None:
        """Expose retrieval and guarded commercial actions for one model turn."""
        updater = TaskUpdater(queue, task.id, task.context_id)
        started = monotonic()
        current = self.negotiations.get(task.id)
        snapshot = self.comparison.snapshot if self.comparison is not None else None
        candidates: dict[str, Product] = {}
        explicit = opening_offer(text) if current is None else None
        basic_sku = explicit.sku if explicit else basic_request(text)
        offer = (
            explicit.price
            if explicit is not None
            else bargaining_offer(text)
            if current is not None
            else None
        )
        if current is not None and offer is None and accepts_quote(text):
            offer = current.ask
        if current is None and basic_sku is not None:
            candidates.update(
                (product.sku, product)
                for product in self.catalog.search(
                    basic_sku,
                    constraints=lambda product: product.sku == basic_sku,
                )
            )
        if current is not None and offer is not None:
            current = replace(current, incoming_offers=current.incoming_offers + 1)
            self.negotiations[task.id] = current
        state: dict[str, JsonValue] = {
            "phase": "bargaining" if current else "opening",
            "buyer_text": text[:2000] if current is None else None,
            "offer": offer,
            "explicit_offer_sku": explicit.sku if explicit else None,
            "price_final": current.ask == current.product.reserve if current else False,
            "offer_meets_reserve": (
                offer is not None
                and current is not None
                and offer >= current.product.reserve
            ),
            "offer_meets_ask": (
                offer is not None and current is not None and offer >= current.ask
            ),
            "catalog_count": len(self.catalog.products),
            "catalog_candidates": (
                search_result(
                    tuple(candidates.values()),
                    self.price_history,
                    snapshot,
                ).data
                if candidates
                else None
            ),
            "selected": json_value(current.product) if current else None,
            "ask": current.ask if current else None,
            "allowed_quote_min": current.product.reserve if current else None,
            "allowed_quote_max": current.ask if current else None,
            "coupon_used": current.coupon_used if current else False,
            "own_price_history": (
                json_value(self.price_history.snapshot(current.product.sku))
                if current
                else None
            ),
            "rounds": current.rounds if current else 0,
            "incoming_offers": current.incoming_offers if current else 0,
            "settlement_required": settlement_required(current, offer),
            "immediate_reserve_settlement": not margin_mode(),
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
                        if current is not None:
                            return ToolResult(
                                data={"error": "SKU already selected; use decide."}
                            )
                        query = Search.model_validate_json(arguments)
                        found = self.catalog.search(
                            basic_sku if basic_sku is not None else query.query,
                            constraints=(
                                lambda product: (
                                    (basic_sku is not None or compatible(product, text))
                                    and (basic_sku is None or product.sku == basic_sku)
                                    and (
                                        explicit is None or product.sku == explicit.sku
                                    )
                                )
                            )
                            if current is None
                            else None,
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
                                task.id,
                                updater,
                                current,
                                candidates,
                                offer,
                                snapshot,
                                explicit.sku if explicit else None,
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
            "OPENING: use catalog_candidates if present; otherwise search."
            if current is None
            else "BARGAINING: use the selected product and the actual numeric offer."
        )
        try:
            _ = await self.harness.run(
                phase + "\n" + PROMPT,
                state,
                TOOLS if current is None and not candidates else TOOLS[1:],
                dispatch,
            )
        except HarnessError:
            self.harness.emit(
                f"seller.failure task={task.id} code=model_loop_exhausted"
            )
            await self.mark_closed(task.id)
            await updater.failed(
                updater.new_agent_message(
                    [Part(text="Unable to choose a valid action.")]
                )
            )
        finally:
            elapsed = monotonic() - started
            self.harness.emit(
                f"seller.turn_elapsed task={task.id} seconds={elapsed:.3f}"
            )

    async def mark_closed(self, task_id: str) -> None:
        """Persist terminal authority before publishing any terminal wire events."""
        if self.journal is not None:
            await self.journal.close(task_id)
        self.closed.add(task_id)
        _ = self.negotiations.pop(task_id, None)

    async def quote(self, turn: Turn, product: Product, decision: Decision) -> None:
        """Publish a guarded quote with transparent coupon accounting."""
        current = turn.current
        coupon_applied = (
            current is not None
            and not current.coupon_used
            and decision.price < current.ask
        )
        updated = Negotiation(
            product,
            decision.price,
            current.rounds + 1 if current else 0,
            coupon_applied or (current.coupon_used if current else False),
            current.incoming_offers if current else 0,
        )
        if self.journal is not None:
            await self.journal.save_negotiation(turn.task_id, updated)
        self.negotiations[turn.task_id] = updated
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
        feedback = decision_feedback(
            current,
            turn.offer,
            decision,
            explicit_available=(
                current is None
                and any(p.sku == turn.explicit_offer_sku for p in self.catalog.products)
            ),
        )
        if feedback is not None:
            return feedback
        match decision.action:
            case "reject":
                await self.mark_closed(turn.task_id)
                await updater.reject(
                    updater.new_agent_message([Part(text=decision.reason)])
                )

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
        feedback = opening_settlement_feedback(turn, product, decision)
        if price_error is not None:
            guard = f"seller.guard task={turn.task_id} code={price_error.code}"
            self.harness.emit(
                f"{guard} price={decision.price} reserve={product.reserve}"
            )
            feedback = ToolResult(data=json_value(price_error))
        if feedback is not None:
            return feedback
        match decision.action:
            case "quote":
                await self.quote(turn, product, decision)
            case "accept":
                if (
                    (current is None and turn.explicit_offer_sku != product.sku)
                    or turn.offer is None
                    or decision.price != turn.offer
                ):
                    return ToolResult(
                        data={
                            "error": "No matching offer. Use quote to state a price."
                        },
                    )
                await self.mark_closed(turn.task_id)
                await complete_sale(
                    updater,
                    AgreedSale(
                        product,
                        decision.price,
                        turn.task_id,
                        self.name,
                        current.incoming_offers if current else 1,
                    ),
                    self.signer,
                )

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
