"""Stable policy interfaces; future A/B/C/D strategies implement these only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class SellerContext:
    now: int
    order: dict[str, Any]
    unit: dict[str, Any]
    history: dict[str, int]


@dataclass(frozen=True)
class BuyerContext:
    now: int
    order: dict[str, Any]
    quote: "Quote"
    history: dict[str, int]


@dataclass(frozen=True)
class Quote:
    order: str
    unit: str
    sku: str
    price: int
    grade: str
    expires_at: int | None
    cert: dict[str, Any] | None
    claim: str | None = None
    warranty_hours: float = 0


@dataclass(frozen=True)
class SellerTerms:
    claim: str | None = None
    include_cert: bool = True
    warranty_hours: float = 0


@dataclass(frozen=True)
class Decision:
    accept: bool
    reason: str


class SellerPricePolicy(Protocol):
    def price(self, context: SellerContext) -> int: ...


class SellerInformationPolicy(Protocol):
    def terms(self, context: SellerContext) -> SellerTerms: ...


class SellerRestockPolicy(Protocol):
    def plan(self, inventory: dict[str, Any], catalog: dict[str, Any], history: dict[str, Any]) -> list[dict[str, Any]]: ...


class BuyerShopPolicy(Protocol):
    def choose(self, order: dict[str, Any], shops: Sequence[dict[str, Any]], history: dict[str, Any]) -> str | None: ...


class BuyerNegotiationPolicy(Protocol):
    def decide(self, context: BuyerContext) -> Decision: ...


class BuyerHistoryPolicy(Protocol):
    def observe(self, result: dict[str, Any]) -> None: ...


class ConservativeListPrice:
    """Baseline seller asks list price, but never below the unit's cost."""

    def price(self, context: SellerContext) -> int:
        return max(int(context.unit["cost"]), int(context.unit["list_price"]))


class TruthfulMinimalTerms:
    """Attach the Mock certificate; make no quality claim and offer no warranty."""

    def terms(self, context: SellerContext) -> SellerTerms:
        return SellerTerms()


class BudgetAndQualityBuyer:
    """Baseline buyer accepts only a budget-safe, verifiable, order-fitting quote."""

    def decide(self, context: BuyerContext) -> Decision:
        if context.quote.price > context.order["budget"]:
            return Decision(False, "over_budget")
        return Decision(True, "accepted")

