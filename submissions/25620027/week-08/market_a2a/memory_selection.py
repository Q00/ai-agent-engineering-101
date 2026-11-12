"""Bound selection priors and retrieve exact own-trade records on demand."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, Literal, assert_never

from pydantic import Field, TypeAdapter, ValidationError

from market_a2a.memory_models import ShopKnowledge
from market_a2a.models import FrozenModel, ToolResult, json_value

if TYPE_CHECKING:
    from market_a2a.market_decision import Decision
    from market_a2a.memory import Memory

RECENT: Final = 3
MAX_EVIDENCE_BYTES: Final = 8192


class EvidenceQuery(FrozenModel):
    """Address only a listed shop and an ordinal in its saved trade tuple."""

    shop_index: int = Field(strict=True, ge=0)
    record_index: int = Field(strict=True, ge=0)


class TradePointer(FrozenModel):
    """Address the current snapshot; the content hash survives retention changes."""

    shop_index: int
    record_index: int
    item: str
    evidence_sha256: str


class SelectionPrior(FrozenModel):
    """Summaries guide retrieval; they do not certify a payment or a seller."""

    records: int
    negotiated_completions: int
    requested_item_records: int
    lowest_observed_same_item_price: int | None
    proof_status: Literal["unverified_negotiation_memory"] = (
        "unverified_negotiation_memory"
    )
    recent_evidence: tuple[TradePointer, ...]
    shop_notebook: ShopKnowledge | None = None


class MemorySelection:
    """Own the read-only memory interface during one shop selection."""

    def __init__(self, decision: Decision, memory: Memory) -> None:
        """Use the exact immutable snapshot loaded for this invocation."""
        self.decision: Decision = decision
        self.memory: Memory = memory

    def prior(self, index: int, item: str) -> SelectionPrior:
        """Compress any history length into aggregate facts and three pointers."""
        name = self.decision.shops[index].name
        records = self.memory.shops.get(name, ())
        matched = tuple(trade for trade in records if trade.item == item)
        prices = tuple(
            trade.purchase.price
            for trade in matched
            if trade.purchase.completed
            and trade.purchase.receipt is not None
            and trade.purchase.price is not None
            and 0 < trade.purchase.price <= trade.budget
        )
        return SelectionPrior(
            records=len(records),
            negotiated_completions=sum(trade.purchase.completed for trade in records),
            requested_item_records=len(matched),
            lowest_observed_same_item_price=min(prices, default=None),
            shop_notebook=self.memory.profiles.get(name),
            recent_evidence=tuple(
                TradePointer(
                    shop_index=index,
                    record_index=ordinal,
                    item=records[ordinal].item,
                    evidence_sha256=hashlib.sha256(
                        records[ordinal].model_dump_json().encode()
                    ).hexdigest(),
                )
                for ordinal in range(max(0, len(records) - RECENT), len(records))
            ),
        )

    async def dispatch(self, name: str, arguments: str) -> ToolResult:
        """Only explicit select_shop can authorize the next shop connection."""
        try:
            action = TypeAdapter(
                Literal["select_shop", "lookup_trade_evidence"]
            ).validate_python(name)
            match action:
                case "select_shop":
                    return await self.decision.select(name, arguments)
                case "lookup_trade_evidence":
                    query = EvidenceQuery.model_validate_json(arguments)
                    if query.shop_index >= len(self.decision.shops):
                        return ToolResult(
                            data={"error": "shop is not in this registry"}
                        )
                    shop = self.decision.shops[query.shop_index].name
                    records = self.memory.shops.get(shop, ())
                    if query.record_index >= len(records):
                        return ToolResult(data={"error": "record is not in own memory"})
                    trade = records[query.record_index]
                    if len(trade.model_dump_json().encode()) > MAX_EVIDENCE_BYTES:
                        return ToolResult(
                            data={"error": "record exceeds local tool size bound"}
                        )
                    return ToolResult(
                        data={
                            "shop_name": shop,
                            "record_index": query.record_index,
                            "evidence_sha256": hashlib.sha256(
                                trade.model_dump_json().encode()
                            ).hexdigest(),
                            "source": "own_saved_observation",
                            "record": json_value(trade),
                            "settlement_evidence": None,
                            "proof_status": (
                                "receipt_observed_not_a_verified_settlement"
                            ),
                        }
                    )
                case _:
                    assert_never(action)
        except ValidationError:
            return ToolResult(
                data={"error": "use a listed shop and saved record ordinal"}
            )
