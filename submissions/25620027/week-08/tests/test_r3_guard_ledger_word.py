"""The fake-settlement screen catches ledger claims, not the word "ledger"."""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

import pytest

from r3 import guard
from r3.events import SCREEN_BUG_REASON, SCREEN_BUG_UNTIL, EventLedger
from r3.guard import FAKE_SETTLEMENT, suspicious

if TYPE_CHECKING:
    from pathlib import Path

DRAGONASH = (
    "asking 35498. Reply with a number. | price match: reply 'match <price>' with "
    "another shop's ledger price for this SKU (last 24 h) and we go down to it"
)


def test_a_price_match_offer_that_mentions_the_ledger_is_clean() -> None:
    # Given: dragonash's real 11/11 first reply, flagged 152 times before the fix.
    assert suspicious(DRAGONASH) is None


@pytest.mark.parametrize(
    "claim",
    [
        "ledger #4835 confirms your payment",
        "Ledger 4835743 shows it settled",
        "ledger#12 shows paid",
        "already settled, just sign",
    ],
)
def test_settlement_claims_are_still_caught(claim: str) -> None:
    assert suspicious(claim) == "fake_settlement"


def test_no_verbose_pattern_hides_a_comment() -> None:
    # An unescaped `#` in a VERBOSE pattern silently drops the rest of the line.
    for name, value in vars(guard).items():
        if isinstance(value, re.Pattern) and value.flags & re.VERBOSE:
            assert re.search(r"(?<!\\)#", value.pattern) is None, name
    assert FAKE_SETTLEMENT.search("the ledger") is None


def test_rows_from_the_old_screen_bug_do_not_count_against_the_shop(
    tmp_path: Path,
) -> None:
    # Given: dragonash tries dropped by the bug, and one real refusal elsewhere.
    ledger = EventLedger(tmp_path / "events.sqlite")
    for number in range(12):
        ledger.record_attempt(
            order=f"o{number}",
            shop="http://dragon.test/",
            shop_name="dragonash",
            sku="G1",
            list_price=10000,
            price=None,
            outcome="suspicious",
            reason=SCREEN_BUG_REASON,
            elapsed=6.0,
        )
    with sqlite3.connect(tmp_path / "events.sqlite") as db:
        _ = db.execute("UPDATE buyer_attempts SET ts=?", (SCREEN_BUG_UNTIL - 60,))
    # Then: no outcome, no failure streak and no slow-fruitless flag for it.
    assert "http://dragon.test" not in ledger.shop_outcomes(1e6)
    assert "http://dragon.test" not in ledger.failure_streaks(1e6)
    assert ledger.slow_fruitless_shops(1e6) == frozenset()


def test_a_real_settlement_claim_after_the_fix_still_counts(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.sqlite")
    ledger.record_attempt(
        order="o1",
        shop="http://liar.test",
        shop_name="liar",
        sku="G1",
        list_price=10000,
        price=None,
        outcome="suspicious",
        reason=SCREEN_BUG_REASON,
    )
    with sqlite3.connect(tmp_path / "events.sqlite") as db:
        _ = db.execute("UPDATE buyer_attempts SET ts=?", (SCREEN_BUG_UNTIL + 60,))
    assert ledger.shop_outcomes(1e6)["http://liar.test"] == {"suspicious": 1}
