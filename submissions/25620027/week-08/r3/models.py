"""Parse checkout envelopes before passing them to the course implementation."""

from typing import Annotated, ClassVar

from pydantic import ConfigDict, Field, JsonValue

from market_a2a.models import FrozenModel
from market_a2a.receipts import SignedReceipt


class LineItem(FrozenModel):
    """The course market settles one unit of one ordered item."""

    id: str
    quantity: int = Field(strict=True, ge=1, le=1)


class CheckoutReceipt(SignedReceipt):
    """Allow unsigned A2A metadata while validating the four signed fields."""

    # Validate the signed core; gateway forwards the original body with extensions.
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class CreateCheckout(FrozenModel):
    """Exactly one line must describe the signed negotiated receipt.

    Kit v4 buyers also send the order's acceptable SKUs so the shop may ship any
    unit of the ordered category; the list is bounded and must name strings.
    """

    line_items: tuple[LineItem, ...] = Field(min_length=1, max_length=1)
    receipt: CheckoutReceipt
    acceptable: (
        tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] | None
    ) = Field(default=None, max_length=512)


class SignedMandate(FrozenModel):
    """Preserve signed JSON without rewriting claims before verification."""

    mandate: dict[str, JsonValue]
    sig: str


class PaymentData(FrozenModel):
    """One order and the buyer's two closed mandate signatures."""

    order: str
    checkout_mandate: SignedMandate
    payment_mandate: SignedMandate


class CompleteCheckout(FrozenModel):
    """Completion carries a signed payment envelope rather than chat text."""

    payment_data: PaymentData
