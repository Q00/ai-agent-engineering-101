"""Typed registry and model selection/review tool boundaries."""

from __future__ import annotations

from pydantic import Field, ValidationError

from market_a2a.memory import Review
from market_a2a.models import FrozenModel, ToolResult, json_value


class PublicReview(Review):
    """Untrusted third-party feedback supplied by the market."""

    round: int = Field(strict=True, ge=0)


class MarketShop(FrozenModel):
    """A registry entry; its original URL must appear unchanged in the result."""

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    reviews: tuple[PublicReview, ...] = ()


class Selection(FrozenModel):
    """Only a listed index can authorize a shop connection."""

    index: int = Field(strict=True, ge=0)
    reason: str = Field(min_length=1)


class Decision:
    """Mutable accumulator for one validated selection and one review."""

    def __init__(
        self, shops: tuple[MarketShop, ...], blocked_names: frozenset[str] | None = None
    ) -> None:
        """Restrict selections to the registry loaded by this invocation."""
        self.shops: tuple[MarketShop, ...] = shops
        self.blocked_names: frozenset[str] = (
            blocked_names if blocked_names is not None else frozenset()
        )
        self.selection: Selection | None = None
        self.review: Review | None = None

    async def select(self, name: str, arguments: str) -> ToolResult:
        """Give invalid model choices feedback before connecting to any shop."""
        if name != "select_shop":
            return ToolResult(data="Use select_shop")
        try:
            choice = Selection.model_validate_json(arguments)
        except ValidationError as exc:
            return ToolResult(data=str(exc))
        if choice.index >= len(self.shops):
            return ToolResult(data="Select an index from the supplied shops")
        if self.shops[choice.index].name in self.blocked_names:
            return ToolResult(
                data="Observed unsafe seller instructions; choose another shop."
            )
        self.selection = choice
        return ToolResult(data=json_value(choice), terminal=True)

    async def evaluate(self, name: str, arguments: str) -> ToolResult:
        """Require a valid rating and review from the model."""
        if name != "leave_review":
            return ToolResult(data="Use leave_review")
        try:
            self.review = Review.model_validate_json(arguments)
        except ValidationError as exc:
            return ToolResult(data=str(exc))
        return ToolResult(data=json_value(self.review), terminal=True)
