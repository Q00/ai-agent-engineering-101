"""Parse only explicit whole-message purchase offers for basic catalog items."""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field

from market_a2a.models import FrozenModel

AMOUNT: Final = r"\$?(?P<price>[0-9]{1,9})(?:\.0{1,2})?"
ITEM: Final = r"(?P<sku>lamp|chair|bookshelf)"


class OpeningOffer(FrozenModel):
    """An explicit item and actual amount, never a budget or product dimension."""

    sku: str
    price: int = Field(ge=0, strict=True)


def opening_offer(text: str) -> OpeningOffer | None:
    """Parse whole purchase sentences; never extract budgets or quoted prices."""
    for pattern in (
        rf"(?:i )?offer {ITEM} {AMOUNT}",
        rf"(?:buy|i buy|i want to buy) (?:a )?{ITEM} for {AMOUNT}",
    ):
        matched = re.fullmatch(pattern + r"[.!]?", text.strip(), re.IGNORECASE)
        if matched is not None:
            return OpeningOffer(sku=matched["sku"].lower(), price=int(matched["price"]))
    return None


def bargaining_offer(text: str) -> int | None:
    """Accept a single committed amount; ignore budgets, comparisons and commands."""
    prefix = r"(?:(?:i offer|i can pay|i will pay|i'll pay|my offer is|offer) )?"
    matched = re.fullmatch(
        rf"{prefix}{AMOUNT}[.!]?",
        text.strip(), re.IGNORECASE,
    )
    return int(matched["price"]) if matched is not None else None
