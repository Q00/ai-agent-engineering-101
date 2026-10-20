"""Grounded review signals exclude executable prose and preserve own incidents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from market_a2a.memory import Review, Trade
from market_a2a.security import operator_directive_observed

if TYPE_CHECKING:
    from pydantic import JsonValue

INJECTION_WARNING: Final = (
    "Observed a prompt-injection attempt in the seller response: "
    "a claim of market/system authority accompanied by instructions "
    "to accept a price, change the budget, or assign a specified rating. "
    "Seller instructions were excluded from the negotiation input."
)


def public_review_signals(review: Review) -> dict[str, JsonValue]:
    """Summarize unverified reviewer claims without forwarding their instructions."""
    text = review.review[:4000].casefold()
    return {
        "stars": review.stars,
        "reviewer_reports_overcharge": bool(
            re.search(
                r"overcharg|surcharge|hidden fee|receipt (?:says|.*higher|.*inflated)",
                text,
            )
        ),
        "reviewer_reports_no_deal": bool(
            re.search(
                r"no deal|no purchase|no receipt|walked away|could not complete",
                text,
            )
        ),
        "authority_directive_in_review": operator_directive_observed(text),
    }


def incident_review(trade: Trade) -> Review:
    """Report only the seller-response pattern observed during this own trade."""
    purchase = trade.purchase
    outcome = "No purchase completed for this order."
    if purchase.completed and purchase.price is not None:
        outcome = f"Purchase completed at {purchase.price}; budget was {trade.budget}."
    return Review(
        stars=1,
        review=f"{INJECTION_WARNING} {outcome}",
    )
