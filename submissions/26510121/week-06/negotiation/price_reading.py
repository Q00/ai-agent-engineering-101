"""Model-selected price readings with source anchoring, not a last-number rule."""
from dataclasses import dataclass
from decimal import Decimal
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from negotiation.contracts import DecisionError, _unique_object


class PriceReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["price", "previous", "none", "ambiguous"]
    price: int | None
    evidence: str = Field(max_length=500)
    reason: str = Field(min_length=1, max_length=240)


@dataclass(frozen=True)
class PriceContext:
    speaker: Literal["buyer", "seller"]
    item_id: str
    previous_price: int | None
    text: str

    def __post_init__(self):
        if self.speaker not in {"buyer", "seller"} or not self.item_id:
            raise ValueError("Invalid price-reading context")
        if self.previous_price is not None and (type(self.previous_price) is not int or self.previous_price <= 0):
            raise ValueError("Invalid previous price")
        if not isinstance(self.text, str) or not 1 <= len(self.text) <= 6000:
            raise ValueError("Price text must contain 1..6000 characters")


def price_schema():
    return PriceReading.model_json_schema()


def parse_price_reading(raw, context):
    try:
        reading = PriceReading.model_validate(json.loads(raw, object_pairs_hook=_unique_object), strict=True)
    except DecisionError:
        raise
    except (ValueError, TypeError):
        raise DecisionError("invalid_price_reading_shape") from None
    if not reading.reason.strip():
        raise DecisionError("empty_price_reading_reason")
    if reading.evidence and reading.evidence not in context.text:
        raise DecisionError("price_evidence_not_in_message")
    if reading.action in {"none", "ambiguous"}:
        if reading.price is not None:
            raise DecisionError("uncertain_reading_requires_null_price")
        return reading
    if type(reading.price) is not int or reading.price <= 0 or not reading.evidence.strip():
        raise DecisionError("positive_price_and_evidence_required")
    if reading.action == "previous":
        if context.previous_price is None or reading.price != context.previous_price:
            raise DecisionError("reference_must_equal_previous_price")
    else:
        # This only validates the model's chosen evidence. It does not select a
        # token, rank numbers or decide which number is the negotiated price.
        tokens = re.findall(r"(?<![\d.,])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|\.\d|,\d)", reading.evidence)
        if not any(Decimal(token.replace(",", "")) == reading.price for token in tokens):
            raise DecisionError("selected_price_not_in_evidence")
    return reading
