"""Receipt validation must never reconstruct or normalize the returned data Part."""

from __future__ import annotations

import pytest
from pydantic import JsonValue, ValidationError

from market_a2a.models import HarnessError
from market_a2a.remote import Checkout, Task, purchase


@pytest.mark.parametrize("price", [120, 120.0])
def test_receipt_when_valid_preserves_signature_extensions_and_numeric_type(
    price: float,
) -> None:
    # Given: the seller issued a payload containing signed fields and extensions.
    raw: dict[str, JsonValue] = {
        "shop": "Seller Identity",
        "item": "lamp",
        "sku": "lamp-1",
        "price": price,
        "task": "task-7",
        "sig_seller": "verbatim",
        "extra": {"float": 1.0},
    }
    task = Task.model_validate(
        {
            "id": "task-7",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"data": raw}]}],
        }
    )
    # When: the receipt is checked against the authorized checkout.
    result = purchase(
        task, Checkout(item="lamp", sku="lamp-1", price=120, budget=120, shop="rpc")
    )
    # Then: every payload field and number type survives.
    assert result.receipt == raw
    assert result.receipt is not None
    assert type(result.receipt["price"]) is type(price)


@pytest.mark.parametrize("price", [121, 120.5, "120", True])
def test_receipt_when_price_invalid_rejects_checkout(price: JsonValue) -> None:
    # Given: the receipt differs from the authorized price or monetary type.
    task = Task.model_validate(
        {
            "id": "task-7",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"data": {"item": "lamp", "price": price}}]}],
        }
    )
    # When/Then: validation rejects the checkout instead of returning a receipt.
    with pytest.raises((HarnessError, ValidationError)):
        _ = purchase(task, Checkout(item="lamp", price=120, budget=120, shop="rpc"))


def test_receipt_when_task_identity_differs_rejects_checkout() -> None:
    # Given: a seller attaches another negotiation's receipt.
    task = Task.model_validate(
        {
            "id": "current-task",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {
                    "parts": [
                        {"data": {"item": "lamp", "price": 120, "task": "other-task"}}
                    ]
                }
            ],
        }
    )
    # When/Then: the buyer rejects the mismatched task identity.
    with pytest.raises(HarnessError):
        _ = purchase(task, Checkout(item="lamp", price=120, budget=120, shop="rpc"))
