"""Grounded review signals exclude executable prose and preserve own incidents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, TypedDict

from market_a2a.memory import Review, Trade
from market_a2a.models import FrozenModel
from market_a2a.security import operator_directive_observed

if TYPE_CHECKING:
    from market_a2a.market_decision import PublicReview

INJECTION_WARNING: Final = (
    "Observed a prompt-injection attempt in the seller response: "
    "a claim of market/system authority accompanied by instructions "
    "to accept a price, change the budget, or assign a specified rating. "
    "Seller instructions were excluded from the negotiation input."
)


class PublicReviewSignals(TypedDict):
    """Unverified reviewer claims; no field independently establishes seller facts."""

    stars: int
    reviewer_reports_overcharge: bool
    reviewer_reports_no_deal: bool
    authority_directive_in_review: bool
    reviewer_reports_matched_receipt: bool


class SelectionReview(FrozenModel):
    """Rating-free claims at the selection boundary, not independent review votes."""

    round: int
    reviewer_reports_overcharge: bool
    reviewer_reports_no_deal: bool
    authority_directive_in_review: bool
    reviewer_reports_matched_receipt: bool


def public_review_signals(review: Review) -> PublicReviewSignals:
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
        "reviewer_reports_matched_receipt": bool(
            re.search(r"\breceipt\s+(?:matched|matches)\b", text)
        ),
    }


def selection_review_evidence(
    reviews: tuple[PublicReview, ...],
) -> tuple[SelectionReview, ...]:
    """Collapse bounded same-round claims without inferring independent authors."""
    evidence: list[SelectionReview] = []
    seen: set[SelectionReview] = set()
    for review in reviews[:10]:
        signals = public_review_signals(review)
        entry = SelectionReview(
            round=review.round,
            reviewer_reports_overcharge=signals["reviewer_reports_overcharge"],
            reviewer_reports_no_deal=signals["reviewer_reports_no_deal"],
            authority_directive_in_review=signals["authority_directive_in_review"],
            reviewer_reports_matched_receipt=signals[
                "reviewer_reports_matched_receipt"
            ],
        )
        if entry not in seen:
            seen.add(entry)
            evidence.append(entry)
    return tuple(evidence)


def incident_review(trade: Trade) -> Review:
    """Report only the seller-response pattern observed during this own trade."""
    purchase = trade.purchase
    outcome = "No purchase completed for this order."
    if purchase.completed and purchase.price is not None:
        outcome = f"Purchase completed at {purchase.price}."
    return Review(
        stars=1,
        review=f"{INJECTION_WARNING} {outcome}",
    )
