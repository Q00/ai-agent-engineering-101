"""Parse only explicit whole-message purchase offers for basic catalog items."""

from __future__ import annotations

import re

from pydantic import Field

from market_a2a.models import FrozenModel


class OpeningOffer(FrozenModel):
    """An explicit item and actual amount, never a budget or product dimension."""

    sku: str
    price: int = Field(ge=0, strict=True)


def opening_offer(text: str) -> OpeningOffer | None:
    """Accept only 'offer ITEM N' or 'buy ITEM for N', ignoring outer whitespace."""
    alternatives = (
        r"(?:offer (lamp|chair|bookshelf) ([0-9]{1,9})|",
        r"buy (lamp|chair|bookshelf) for ([0-9]{1,9}))",
    )
    matched = re.fullmatch(
        "".join(alternatives),
        text.strip(),
        flags=re.IGNORECASE,
    )
    if matched is None:
        return None
    return OpeningOffer(
        sku=(matched[1] or matched[3]).lower(),
        price=int(matched[2] or matched[4]),
    )
