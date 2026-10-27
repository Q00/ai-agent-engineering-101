"""Shared schemas for bounded observations and their generated views."""

from pydantic import AwareDatetime, Field

from market_a2a.models import FrozenModel, Purchase


class Review(FrozenModel):
    """A model-authored evaluation of the observed transaction."""

    stars: int = Field(strict=True, ge=1, le=5)
    review: str = Field(min_length=1)


class Trade(FrozenModel):
    """An own observation survives even if review generation later fails."""

    item: str
    budget: int
    selection_reason: str
    purchase: Purchase
    evaluation: Review | None = None
    observed_at: AwareDatetime | None = None
