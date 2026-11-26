"""Ask for the cheapest A/B goods unit; use the public ledger for price matches."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

import anyio
import pytest

from market_a2a.remote import Task
from r3 import buyer as buyer_module
from r3.buyer import (
    Choice,
    UnfitUnitError,
    ledger_low_price,
    negotiate,
    quote_information_request,
)
from r3.market_files import Order, ShopEntry
from r3.policy import BuyerPolicy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def pre_patch_36_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the list-discount line; patch 3.6 is in test_r3_margin_line."""
    monkeypatch.setattr(buyer_module, "MARGIN_LINE", None)


MATCH_TEXT = (
    "asking {ask}. Reply with a number. | price match: reply 'match <price>' with "
    "another shop's ledger price for this SKU (last 24 h) and we go down to it"
)


def task(state: str, text: str, ask: int | None) -> Task:
    """Build a seller reply carrying a grade-A, 72 h warranty certificate."""
    message: dict[str, object] = {
        "parts": [
            {"text": text},
            {
                "data": {
                    "cert": {"cert": {"unit": "u", "sku": "sku", "grade": "A"}},
                    "warranty_hours": 72,
                }
            },
        ]
    }
    if ask is not None:
        message["metadata"] = {"ask": ask}
    return Task.model_validate(
        {"id": "t", "contextId": "c", "status": {"state": state, "message": message}}
    )


class Seller:
    """Scripted seller: drops to a matched price, completes when we offer it."""

    def __init__(self, first_ask: int) -> None:
        """Start at the seller's first ask."""
        self.ask = first_ask
        self.sent: list[str] = []

    async def send(self, _endpoint: str, message: str, _task: Task) -> Task:
        """Answer one buyer message."""
        self.sent.append(message)
        if message.startswith("match "):
            self.ask = int(message.split()[1])
            return task("TASK_STATE_INPUT_REQUIRED", f"asking {self.ask}.", self.ask)
        if message.strip().isdigit() and int(message) >= self.ask:
            return task("TASK_STATE_COMPLETED", "deal", None)
        return task("TASK_STATE_INPUT_REQUIRED", f"asking {self.ask}.", self.ask)


def run(seller: Seller, opened: Task, low: int | None) -> int | None:
    choice = Choice(
        ShopEntry(name="dragonash", url="http://dragon.test"),
        "sku",
        "item",
        100000,
        "I want to buy item (sku)",
        0.3,
    )
    policy = BuyerPolicy(
        max_price_ratio=0.68,
        first_offer_ratio=0.42,
        quick_accept_ratio=0.6,
        surplus_score=False,
    )
    order = Order.model_validate(
        {"order": "o", "kind": "goods", "acceptable": ["sku"], "budget": 100000}
    )

    async def scenario() -> int | None:
        _, price = await negotiate(
            seller,  # pyright: ignore[reportArgumentType]
            "http://dragon.test/rpc",
            choice,
            100000,
            policy=policy,
            emit=lambda _: None,
            order=order,
            opened=opened,
            match_price=lambda: low,
        )
        return price

    return anyio.run(scenario)


def test_a_price_match_offer_gets_the_ledger_low_then_the_deal() -> None:
    # Given: dragonash's real offer at 0.70 of list; another shop sold at 0.55.
    seller = Seller(70000)
    opened = task("TASK_STATE_INPUT_REQUIRED", MATCH_TEXT.format(ask=70000), 70000)
    # When: the buyer negotiates.
    price = run(seller, opened, 55000)
    # Then: it asks for the match first and buys at the matched price.
    assert seller.sent[0] == "match 55000"
    assert price == 55000


def test_no_match_request_without_the_offer_or_a_lower_ledger_price() -> None:
    # A shop that does not offer matching never sees "match" (it never drops, so
    # the buyer walks away at its line).
    plain = Seller(70000)
    with pytest.raises(UnfitUnitError):
        run(plain, task("TASK_STATE_INPUT_REQUIRED", "asking 70000.", 70000), 55000)
    assert not any(m.startswith("match") for m in plain.sent)
    # A ledger price not below the ask is not sent either.
    dear = Seller(70000)
    opened = task("TASK_STATE_INPUT_REQUIRED", MATCH_TEXT.format(ask=70000), 70000)
    with pytest.raises(UnfitUnitError):
        run(dear, opened, 72000)
    assert not any(m.startswith("match") for m in dear.sent)


def test_ledger_low_price_uses_other_shops_a_b_and_the_last_day(
    tmp_path: Path,
) -> None:
    now = time.time()
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as con:
        _ = con.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER,
            grade TEXT, claim TEXT, claim_honest INTEGER, data TEXT)"""
        )
        rows = [
            (now - 60, "http://other.test/", 56000, "B"),
            (now - 60, "http://other.test", 40000, "C"),  # grade C: not comparable
            (now - 60, "http://dragon.test/", 50000, "A"),  # the same shop
            (now - 2 * 86400, "http://old.test", 45000, "A"),  # too old
            (now - 60, "http://third.test", 61000, "A"),
        ]
        _ = con.executemany(
            "INSERT INTO rows (ts, kind, shop, sku, amount, grade, data) "
            "VALUES (?, 'settle', ?, 'sku', ?, ?, ?)",
            [
                (ts, shop, amount, grade, json.dumps({}))
                for ts, shop, amount, grade in rows
            ],
        )
    assert ledger_low_price(db, "sku", "http://dragon.test", now) == 56000
    assert ledger_low_price(db, "other", "http://dragon.test", now) is None
    assert ledger_low_price(tmp_path / "missing.sqlite", "sku", "x", now) is None


def test_goods_requests_ask_for_the_cheapest_a_or_b_unit_with_a_warranty() -> None:
    order = Order.model_validate({"order": "o", "kind": "goods", "acceptable": ["s"]})
    text = quote_information_request(order, 4.0)
    assert "cheapest grade A or B unit that carries a warranty" in text


def test_a_grade_a_quote_well_above_the_line_asks_once_for_grade_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The request is switched off since 11/11 16:25; this keeps the path tested.
    monkeypatch.setattr(buyer_module, "ASK_FOR_B_ENABLED", True)
    # Given: a shop quoting grade A at 0.75 of list that never moves.
    seller = Seller(75000)
    opened = task("TASK_STATE_INPUT_REQUIRED", "grade A. asking 75000.", 75000)
    # When: the buyer negotiates (and finally walks at its line).
    with pytest.raises(UnfitUnitError):
        run(seller, opened, None)
    # Then: the first counter carries the grade-B request with a number in it,
    # and the request is sent only once.
    asks = [m for m in seller.sent if "lowest-priced grade B" in m]
    assert len(asks) == 1
    assert asks[0] == seller.sent[0]
    assert asks[0].rstrip().split()[-1].isdigit()
