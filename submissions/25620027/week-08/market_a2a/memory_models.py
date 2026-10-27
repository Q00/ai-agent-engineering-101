"""Shared schemas for bounded observations and their generated views."""

from uuid import uuid4

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
    event_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=32, max_length=32
    )


class ShopKnowledge(FrozenModel):
    """A bounded per-shop notebook; counts are observations, not settled deals."""

    observations: int = Field(ge=0)
    negotiated_completions: int = Field(ge=0)
    last_seen: AwareDatetime | None
    last_item: str
    last_observed_price: int | None
