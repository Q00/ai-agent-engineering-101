"""Bounded numeric evidence from this seller's own completed actions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, assert_never

from market_a2a.models import FrozenModel


class PriceHistory(FrozenModel):
    """Counts describe actions in the retained window, not conversion rates."""

    quote_actions: int = 0
    accepted_sales: int = 0
    recent_accepted_prices: tuple[int, ...] = ()


class SellerHistory:
    """Load canonical seller events only; never expose raw log text to the model."""

    def __init__(self) -> None:
        self.entries: dict[str, PriceHistory] = {}
        path = Path(__file__).resolve().parents[1] / "logs" / "market-kit.log"
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                offset = max(0, size - 2_000_000)
                _ = stream.seek(offset)
                if offset:
                    _ = stream.readline()
                tail = stream.read(2_000_000).decode("utf-8", errors="replace")
        except FileNotFoundError:
            return
        pattern = re.compile(
            r"[0-9]{2}:[0-9]{2}:[0-9]{2} \[seller\] seller\.action "
            r"task=[A-Za-z0-9-]+ action=(quote|accept) "
            r"sku=([A-Za-z0-9_.-]{1,100}) price=([0-9]{1,9})"
        )
        for line in tail.splitlines()[-5000:]:
            found = pattern.fullmatch(line)
            if found is None:
                continue
            sku, price = found[2], int(found[3])
            match found[1]:
                case "quote":
                    self.record("quote", sku, price)
                case "accept":
                    self.record("accept", sku, price)

    def snapshot(self, sku: str) -> PriceHistory:
        """Return only typed numeric facts for the selected SKU."""
        return self.entries.get(sku, PriceHistory())

    def record(self, action: Literal["quote", "accept"], sku: str, price: int) -> None:
        """Count valid emitted actions and retain the last five agreed prices."""
        previous = self.snapshot(sku)
        match action:
            case "quote":
                updated = PriceHistory(
                    quote_actions=previous.quote_actions + 1,
                    accepted_sales=previous.accepted_sales,
                    recent_accepted_prices=previous.recent_accepted_prices,
                )
            case "accept":
                updated = PriceHistory(
                    quote_actions=previous.quote_actions,
                    accepted_sales=previous.accepted_sales + 1,
                    recent_accepted_prices=(*previous.recent_accepted_prices, price)[-5:],
                )
            case _:
                assert_never(action)
        self.entries[sku] = updated
