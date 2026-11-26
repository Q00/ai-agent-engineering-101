"""Food observations bind one unit and one certificate without changing checkout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import pytest

from market_a2a.remote import Part, Task
from r3 import buyer
from r3.market_files import Order, ShopEntry
from r3.policy import Policy
from tests.r3_stock import KIMCHI

NOW: Final = 200000.0
EXPIRY: Final = NOW + 100 * 3600
OTHER_EXPIRY: Final = NOW + 200 * 3600
OTHER_SKU: Final = "F-other"


def certificate(unit: str, expiry: float | None, sku: str | None = KIMCHI) -> Part:
    """Build a seller claim at the same external boundary as a real task."""
    return Part(
        data={"cert": {"cert": {"unit": unit, "sku": sku, "expires_at": expiry}}}
    )


def seller_claim(cert: Part, unit: str, sku: str, expiry: float) -> Part:
    """Attach a seller identity without repeating the certificate fixture."""
    return Part(
        data={**(cert.data or {}), "unit": unit, "sku": sku, "expires_at": expiry}
    )


@dataclass(frozen=True, slots=True)
class BindingCase:
    """Describe a conflicting observation and the fields that must stay unknown."""

    parts: tuple[Part, ...]
    receipt_unit: str | None = "A"
    receipt_sku: str | None = KIMCHI
    expected_unit: str | None = "A"
    expected_sku: str | None = KIMCHI
    expected_expiry: float | None = None
    expected_list_price: int | None = 52000
    stage: Literal["quote", "acceptance", "receipt"] = "receipt"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            BindingCase(
                (certificate("A", EXPIRY),), receipt_unit="B", expected_unit="B"
            ),
            id="receipt-unit-wins-over-quote",
        ),
        pytest.param(
            BindingCase(
                (
                    certificate("A", None),
                    Part(
                        data={
                            "cert": {
                                "cert": {
                                    "unit": "B",
                                    "sku": KIMCHI,
                                    "expires_at": EXPIRY,
                                    "grade": "A",
                                    "warranty_hours": 72,
                                }
                            }
                        }
                    ),
                )
            ),
            id="different-certificate-does-not-fill-missing-expiry",
        ),
        pytest.param(
            BindingCase((certificate("A", EXPIRY), certificate("A", OTHER_EXPIRY))),
            id="same-unit-conflicting-certificates-unknown",
        ),
        pytest.param(
            BindingCase(
                (certificate("A", EXPIRY), certificate("A", EXPIRY)),
                expected_expiry=EXPIRY,
            ),
            id="identical-certificate-duplicates-are-unambiguous",
        ),
        pytest.param(
            BindingCase(
                (
                    certificate("A", None),
                    Part(data={"expires_at": EXPIRY, "hours_left": 100}),
                )
            ),
            id="unbound-seller-expiry-not-borrowed",
        ),
        pytest.param(
            BindingCase(
                (seller_claim(certificate("A", EXPIRY, KIMCHI), "B", KIMCHI, EXPIRY),)
            ),
            id="part-with-conflicting-unit-identities-excluded",
        ),
        pytest.param(
            BindingCase((certificate("A", EXPIRY, OTHER_SKU),)),
            id="wrong-certificate-sku-excluded",
        ),
        pytest.param(
            BindingCase(
                (certificate("A", EXPIRY),),
                receipt_sku=OTHER_SKU,
                expected_sku=None,
                expected_list_price=None,
            ),
            id="substitute-receipt-cannot-borrow-choice-sku-or-list-price",
        ),
        pytest.param(
            BindingCase(
                (certificate("A", EXPIRY),), receipt_unit=None, expected_unit=None
            ),
            id="missing-receipt-unit-remains-unknown",
        ),
        pytest.param(
            BindingCase((certificate("A", EXPIRY, None),)),
            id="missing-certificate-sku-not-inferred",
        ),
        pytest.param(
            BindingCase((certificate("A", 0.0),), expected_expiry=0.0),
            id="explicit-zero-expiry-remains-zero",
        ),
        pytest.param(
            BindingCase(
                (certificate("A", EXPIRY), certificate("B", EXPIRY)),
                expected_unit=None,
                stage="quote",
            ),
            id="ambiguous-quote-identity-is-unknown",
        ),
        pytest.param(
            BindingCase(
                (
                    seller_claim(
                        certificate("A", EXPIRY, KIMCHI), "A", OTHER_SKU, EXPIRY
                    ),
                )
            ),
            id="part-with-conflicting-sku-identities-excluded",
        ),
        pytest.param(
            BindingCase(
                (
                    seller_claim(
                        certificate("A", EXPIRY, KIMCHI), "A", KIMCHI, EXPIRY - 3600
                    ),
                    seller_claim(
                        certificate("A", EXPIRY, KIMCHI), "A", KIMCHI, EXPIRY - 7200
                    ),
                ),
                expected_expiry=EXPIRY,
            ),
            id="conflicting-seller-claims-not-first-match",
        ),
    ],
)
def test_receipt_evidence_keeps_identity_and_certificate_together(
    monkeypatch: pytest.MonkeyPatch, case: BindingCase
) -> None:
    # Given: the actual receipt may differ from the earlier quote's identity.
    task = Task.model_validate(
        {
            "id": "t-binding",
            "contextId": "c-binding",
            "status": {
                "state": "TASK_STATE_COMPLETED",
                "message": {
                    "parts": [part.model_dump(mode="json") for part in case.parts]
                },
            },
            "artifacts": [
                {
                    "parts": [
                        {
                            "data": {
                                "unit": case.receipt_unit,
                                "item": case.receipt_sku,
                                "price": 20000,
                                "sig_seller": "fixture-signature",
                            }
                        }
                    ]
                }
            ],
        }
    )
    choice = buyer.Choice(
        ShopEntry(name="Peer", url="http://peer.test"),
        KIMCHI,
        "kimchi",
        52000,
        "ask",
        1,
    )
    monkeypatch.setattr(buyer, "WALL_CLOCK", lambda: NOW)
    # When: capture claims only; this function never makes a checkout decision.
    result = buyer.food_task_evidence(
        Order(order="food-binding", kind="food", acceptable=(KIMCHI, OTHER_SKU)),
        Policy(),
        choice,
        task,
        stage=case.stage,
        price=20000,
    )
    # Then: unknown identity cannot borrow another unit's expiry or valuation.
    assert result is not None
    assert result.unit == case.expected_unit
    assert result.sku == case.expected_sku
    assert result.certificate_expires_at == case.expected_expiry
    assert result.seller_expires_at is None
    assert result.stated_hours_left == (
        (case.expected_expiry - NOW) / 3600
        if case.expected_expiry is not None
        else None
    )
    assert result.list_price == case.expected_list_price
    assert result.price == 20000
    assert result.certificate_attached is True
    assert result.grade is None
    assert result.warranty_hours is None
