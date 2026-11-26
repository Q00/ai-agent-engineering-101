"""Receipt refusal memory keeps unrelated SKU opportunities available."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from r3 import events as events_module
from r3.events import EventLedger
from r3.market_files import PaymentLine

if TYPE_CHECKING:
    from pathlib import Path

SHOP: Final = "https://shop.test"
QUALITY: Final = (
    "receipt expected credit below the line or quality (claim C, warranty 72 h)"
)
PRICE: Final = (
    "receipt expected credit below the line or quality "
    "(paid 80 above 0.70 of the shipped list 100)"
)
IDENTITY: Final = (
    "receipt expected credit below the line or quality (shipped SKU unknown: goods)"
)


def refusal(ledger: EventLedger, order: str, sku: str = "bad") -> None:
    """Record one observed R3 receipt rejection with a selected SKU."""
    ledger.record_attempt(
        order=order,
        shop=SHOP,
        shop_name="shop",
        sku=sku,
        list_price=100,
        price=None,
        outcome="no_deal",
        reason=QUALITY,
        market_round=3,
    )


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EventLedger:
    """Use a fixed clock and an isolated real SQLite ledger."""
    monkeypatch.setattr(events_module.time, "time", lambda: 20_000.0)
    return EventLedger(tmp_path / "events.sqlite")


def test_three_refusals_block_only_the_rejected_sku(ledger: EventLedger) -> None:
    # Given: one SKU repeatedly fails, another only once at the same shop.
    for n in range(3):
        refusal(ledger, f"bad-{n}")
    refusal(ledger, "good-once", "good")
    # When: derive the current repeated-refusal candidates.
    blocked = ledger.receipt_refused_skus(3)
    # Then: the unrelated SKU remains available.
    assert blocked == frozenset({(SHOP, "bad")})


def test_unknown_sku_cannot_blacklist_any_offer(ledger: EventLedger) -> None:
    # Given: historical attempts do not identify a selected SKU.
    for n in range(4):
        refusal(ledger, f"unknown-{n}", "")
    # When: derive the scoped memory.
    blocked = ledger.receipt_refused_skus(3)
    # Then: missing identity never expands to a shop-wide exclusion.
    assert blocked == frozenset()


def test_duplicate_attempts_in_one_order_count_once(ledger: EventLedger) -> None:
    # Given: one rejected negotiation was recorded more than once.
    for _ in range(3):
        refusal(ledger, "same-order")
    # When: count independently issued orders, not duplicated attempts.
    blocked = ledger.receipt_refused_skus(3)
    # Then: one order cannot trigger the three-order threshold.
    assert blocked == frozenset()


def test_security_errors_keep_their_existing_failure_streak(
    ledger: EventLedger,
) -> None:
    # Given: three suspicious receipts are separate from economic walk-aways.
    for n in range(3):
        ledger.record_attempt(
            order=f"suspicious-{n}",
            shop=SHOP,
            shop_name="shop",
            sku="bad",
            list_price=100,
            price=None,
            outcome="suspicious",
            reason="receipt signature mismatch",
            market_round=3,
        )
    # When: derive both the narrow economic memory and existing safety streak.
    blocked = ledger.receipt_refused_skus(3)
    streaks = ledger.failure_streaks(3)
    # Then: unsafe receipts retain shop-level handling through the original API.
    assert blocked == frozenset()
    assert streaks[SHOP][0] == 3


@pytest.mark.parametrize(
    ("shop", "item", "paid", "ledger_id", "expected"),
    [
        (SHOP + "/", "bad", True, 10, frozenset()),
        (SHOP, "good", True, 10, frozenset({(SHOP, "bad")})),
        ("https://other.test", "bad", True, 10, frozenset({(SHOP, "bad")})),
        (SHOP, "bad", False, 10, frozenset({(SHOP, "bad")})),
        (SHOP, "bad", True, None, frozenset({(SHOP, "bad")})),
    ],
)
def test_only_a_paid_matching_sku_resets_memory(  # noqa: PLR0913
    ledger: EventLedger,
    shop: str,
    item: str,
    *,
    paid: bool,
    ledger_id: int | None,
    expected: frozenset[tuple[str, str]],
) -> None:
    # Given: an agreement follows three rejections; payment evidence may differ.
    for n in range(3):
        refusal(ledger, f"failed-{n}")
    ledger.record_attempt(
        order="later",
        shop=SHOP,
        shop_name="shop",
        sku="bad",
        list_price=100,
        price=60,
        outcome="agreed",
        market_round=3,
    )
    ledger.sync_payments(
        [
            PaymentLine(
                order="later",
                shop=shop,
                item=item,
                price=60,
                paid=paid,
                ledger=ledger_id,
            )
        ]
    )
    # When: derive recovery from actual payment identity.
    blocked = ledger.receipt_refused_skus(3)
    # Then: only confirmed matching payment permits this SKU again.
    assert blocked == expected


def test_pending_agreement_does_not_reset_memory(ledger: EventLedger) -> None:
    # Given: the shop agreed after three rejections but checkout is still unknown.
    for n in range(3):
        refusal(ledger, f"failed-{n}")
    ledger.record_attempt(
        order="pending",
        shop=SHOP,
        shop_name="shop",
        sku="bad",
        list_price=100,
        price=60,
        outcome="agreed",
        market_round=3,
    )
    # When: the memory is read before payment.
    blocked = ledger.receipt_refused_skus(3)
    # Then: negotiated success alone is not proven recovery.
    assert blocked == frozenset({(SHOP, "bad")})


def test_old_refusals_expire(
    ledger: EventLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the three refusals are beyond the requested window.
    for n in range(3):
        refusal(ledger, f"old-{n}")
    monkeypatch.setattr(events_module.time, "time", lambda: 30_801.0)
    refusal(ledger, "new")
    # When: derive a three-hour memory.
    blocked = ledger.receipt_refused_skus(3)
    # Then: one fresh refusal does not retain an expired three-order block.
    assert blocked == frozenset()


def test_different_failure_causes_do_not_amplify_each_other(
    ledger: EventLedger,
) -> None:
    # Given: no single price, identity, or quality failure repeated three times.
    for kind, reason in enumerate((QUALITY, PRICE, IDENTITY)):
        for n in range(2):
            ledger.record_attempt(
                order=f"cause-{kind}-{n}",
                shop=SHOP,
                shop_name="shop",
                sku="bad",
                list_price=100,
                price=None,
                outcome="no_deal",
                reason=reason,
                market_round=3,
            )
    # When: group repeated failures by their cause.
    blocked = ledger.receipt_refused_skus(3)
    # Then: unrelated evidence does not accumulate into a blanket rejection.
    assert blocked == frozenset()


def test_late_payment_import_does_not_clear_newer_failures(ledger: EventLedger) -> None:
    # Given: the paid negotiation predates three subsequent receipt failures.
    ledger.record_attempt(
        order="old-paid",
        shop=SHOP,
        shop_name="shop",
        sku="bad",
        list_price=100,
        price=60,
        outcome="agreed",
        market_round=3,
    )
    for n in range(3):
        refusal(ledger, f"new-failure-{n}")
    ledger.sync_payments(
        [
            PaymentLine(
                order="old-paid", shop=SHOP, item="bad", price=60, paid=True, ledger=1
            )
        ]
    )
    # When: recovery uses negotiation chronology instead of import time.
    blocked = ledger.receipt_refused_skus(3)
    # Then: old success cannot erase later failed units.
    assert blocked == frozenset({(SHOP, "bad")})
