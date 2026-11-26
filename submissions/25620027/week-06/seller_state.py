"""Validated seller configuration and deterministic negotiation state."""

from dataclasses import dataclass, replace
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

PositivePrice = Annotated[int, Field(strict=True, gt=0)]


class Reserves(BaseModel):
    """The three inventory prices, parsed once from CLI JSON."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    lamp: PositivePrice
    chair: PositivePrice
    bookshelf: PositivePrice

    def lookup(self, item: str) -> int | None:
        """Find an inventory item without accepting arbitrary attributes."""
        return {
            "lamp": self.lamp,
            "chair": self.chair,
            "bookshelf": self.bookshelf,
        }.get(item)


class SellerConfig(BaseModel):
    """A seller's fixed settings; card inflation never changes pricing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str = Field(min_length=1)
    port: int = Field(gt=0, le=65535)
    reserves: Reserves
    low_offer_limit: PositivePrice = 5
    inflated: bool = False


class Receipt(BaseModel):
    """Machine-readable sale artifact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    seller: str
    item: str
    price: PositivePrice


@dataclass(frozen=True, slots=True)
class Negotiation:
    """Immutable snapshot of one item's negotiation."""

    item: str
    reserve: int
    low_offers: int = 0

    @property
    def opening_price(self) -> int:
        """Round 130 percent up using integer arithmetic."""
        return (self.reserve * 13 + 9) // 10

    def with_low_offer(self) -> "Negotiation":
        """Advance exactly once for each valid below-reserve offer."""
        return replace(self, low_offers=self.low_offers + 1)
