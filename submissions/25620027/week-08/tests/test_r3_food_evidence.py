"""Food observations stay distinct from payment and mature ledger outcomes."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter, ValidationError

from r3.events import EventLedger, FoodEvidence
from r3.ledger_db import LedgerDB
from r3.market_files import Order, PaymentLine

if TYPE_CHECKING:
    from pathlib import Path


def observations(path: Path) -> tuple[FoodEvidence, ...]:
    """Read the persisted boundary exactly as a later read-only audit would."""
    with closing(sqlite3.connect(path)) as db:
        rows = TypeAdapter(list[tuple[str]]).validate_python(
            db.execute(
                "SELECT payload FROM buyer_food_evidence ORDER BY observed_at,stage"
            ).fetchall()
        )
    return tuple(FoodEvidence.model_validate_json(row[0]) for row in rows)


def test_observed_food_requirements_and_certificate_roundtrip(tmp_path: Path) -> None:
    # Given: a concrete quote with independent certificate and seller statements.
    ledger = EventLedger(tmp_path / "events.sqlite")
    evidence = FoodEvidence(
        order_id="food-order",
        stage="quote",
        observed_at=1000.0,
        task_id="shop-task",
        shop="https://shop.test",
        sku="food-sku",
        unit="unit-7",
        use_within_hours=2.5,
        food_margin_hours=48.0,
        certificate_expires_at=28000.0,
        seller_expires_at=29000.0,
        stated_hours_left=7.5,
        grade="B",
        warranty_hours=0.0,
        price=6500,
        list_price=10000,
        certificate_attached=True,
        policy_version=183,
    )
    # When: the local observation is recorded.
    ledger.record_food_evidence(evidence)
    # Then: requirements and distinct observed facts survive without reinterpretation.
    assert observations(ledger.path) == (evidence,)


@pytest.mark.parametrize("known", [False, True])
def test_unknown_fields_are_not_filled_with_zero_or_false(
    tmp_path: Path, *, known: bool
) -> None:
    # Given: an absent fact differs from an explicitly observed zero or false.
    ledger = EventLedger(tmp_path / "events.sqlite")
    evidence = FoodEvidence(
        order_id="food-order",
        stage="order",
        observed_at=0.0,
        use_within_hours=0.0 if known else None,
        food_margin_hours=0.0 if known else None,
        certificate_expires_at=0.0 if known else None,
        warranty_hours=0.0 if known else None,
        price=0 if known else None,
        certificate_attached=False if known else None,
    )
    # When: the order crosses SQLite and JSON boundaries.
    ledger.record_food_evidence(evidence)
    # Then: no zero, false or unknown value changes meaning.
    assert observations(ledger.path) == (evidence,)


def test_repeated_stage_keeps_first_observation_but_acceptance_is_separate(
    tmp_path: Path,
) -> None:
    # Given: one quote and a later changed snapshot of the same task.
    ledger = EventLedger(tmp_path / "events.sqlite")
    first = FoodEvidence(
        order_id="o",
        stage="quote",
        observed_at=1000.0,
        task_id="t",
        shop="https://s.test",
        certificate_expires_at=9000.0,
    )
    ledger.record_food_evidence(first)
    changed = first.model_copy(update={"observed_at": 1100.0, "price": 600})
    acceptance = FoodEvidence(
        order_id="o",
        stage="acceptance",
        observed_at=1200.0,
        task_id="t",
        shop="https://s.test",
        certificate_expires_at=9000.0,
        price=600,
    )
    # When: a repeated quote and an actual acceptance are appended.
    ledger.record_food_evidence(changed)
    ledger.record_food_evidence(acceptance)
    # Then: first-quote truth is retained and acceptance has its own instant.
    assert observations(ledger.path) == (first, acceptance)


def test_task_ids_from_different_shops_do_not_collide(tmp_path: Path) -> None:
    # Given: task identifiers are scoped by counterpart shop.
    ledger = EventLedger(tmp_path / "events.sqlite")
    facts = tuple(
        FoodEvidence(
            order_id="o",
            stage="quote",
            observed_at=1000.0 + i,
            task_id="same-task",
            shop=f"https://s{i}.test",
        )
        for i in range(2)
    )
    # When: both counterpart observations are stored.
    for fact in facts:
        ledger.record_food_evidence(fact)
    # Then: both source identities survive.
    assert observations(ledger.path) == facts


def test_legacy_order_schema_and_rows_remain_usable(tmp_path: Path) -> None:
    # Given: the exact six-column order table from an old release.
    path = tmp_path / "events.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        _ = db.execute("""CREATE TABLE buyer_orders(order_id TEXT PRIMARY KEY,
observed_at REAL NOT NULL,budget INTEGER,kind TEXT,expires REAL,
policy_version INTEGER)""")
        _ = db.execute("INSERT INTO buyer_orders VALUES ('old',1,100,'food',900,1)")
    ledger = EventLedger(path)
    # When: new code writes an old-style order and an optional new observation.
    ledger.record_order_metadata(Order(order="new", kind="food"), observed_at=2.0)
    ledger.record_food_evidence(
        FoodEvidence(order_id="new", stage="order", observed_at=2.0)
    )
    # Then: old order layout/content is intact and no historical facts are invented.
    with closing(sqlite3.connect(path)) as db:
        assert len(db.execute("PRAGMA table_info(buyer_orders)").fetchall()) == 6
        assert db.execute(
            "SELECT * FROM buyer_orders WHERE order_id='old'"
        ).fetchone() == (
            "old",
            1.0,
            100,
            "food",
            900.0,
            1,
        )
        assert db.execute("SELECT count(*) FROM buyer_orders").fetchone() == (2,)
    assert tuple(x.order_id for x in observations(path)) == ("new",)


def test_concurrent_initialization_and_optional_writes_preserve_each_order(
    tmp_path: Path,
) -> None:
    # Given: multiple local roles may start against the same existing WAL database.
    path = tmp_path / "events.sqlite"
    _ = EventLedger(path)
    with closing(sqlite3.connect(path)) as db, db:
        _ = db.execute("DROP TABLE buyer_food_evidence")
    barrier = Barrier(4)

    def write(number: int) -> None:
        _ = barrier.wait()
        ledger = EventLedger(path)
        ledger.record_food_evidence(
            FoodEvidence(
                order_id=f"o{number}",
                stage="order",
                observed_at=float(number),
            )
        )

    # When: independent writers initialize and append together.
    with ThreadPoolExecutor(max_workers=4) as pool:
        _ = tuple(pool.map(write, range(4)))
    # Then: each actual order is represented once without schema migration races.
    assert tuple(x.order_id for x in observations(path)) == ("o0", "o1", "o2", "o3")


def test_receipt_observation_joins_payment_without_implying_settlement(
    tmp_path: Path,
) -> None:
    # Given: a receipt was observed but checkout explicitly refused payment.
    ledger = EventLedger(tmp_path / "events.sqlite")
    fact = FoodEvidence(
        order_id="o",
        stage="receipt",
        observed_at=1000.0,
        unit="u",
        price=0,
        certificate_attached=False,
    )
    _ = ledger.sync_payments([PaymentLine(order="o", price=0, paid=False)])
    # When: the signed-deal observation is stored independently.
    ledger.record_food_evidence(fact)
    # Then: the existing payment remains refused, not a manufactured settled credit.
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("""SELECT p.paid,p.price,p.ledger FROM buyer_food_evidence e
JOIN payments p ON p.order_id=e.order_id""").fetchall() == [(0, 0, None)]
    assert observations(ledger.path) == (fact,)


def test_unit_and_order_link_to_authoritative_settle_and_break(tmp_path: Path) -> None:
    # Given: public ledger facts are separate from the local certificate observation.
    ledger = EventLedger(tmp_path / "events.sqlite")
    public = LedgerDB(tmp_path / "market.sqlite")
    assert (
        public.ingest(
            [
                {
                    "seq": 1,
                    "ts": 1000.0,
                    "kind": "settle",
                    "round": 3,
                    "data": {"buyer": "me", "order": "o", "unit": "u", "amount": 600},
                },
                {
                    "seq": 2,
                    "ts": 1200.0,
                    "kind": "broken",
                    "round": 3,
                    "data": {"buyer": "me", "unit": "u", "refunded": False},
                },
            ]
        )
        == 2
    )
    fact = FoodEvidence(
        order_id="o",
        stage="receipt",
        observed_at=990.0,
        unit="u",
        certificate_expires_at=900.0,
    )
    # When: the earlier, already-expired certificate value is recorded as observed.
    ledger.record_food_evidence(fact)
    # Then: exact keys link later settle/break/refund facts; expiry is not corrected.
    with closing(sqlite3.connect(ledger.path)) as db:
        _ = db.execute("ATTACH DATABASE ? AS market", (str(public.path),))
        assert db.execute("""SELECT s.amount,json_extract(b.data,'$.refunded')
FROM buyer_food_evidence e
JOIN market.rows s ON s.kind='settle' AND json_extract(s.data,'$.order')=e.order_id
  AND json_extract(s.data,'$.unit')=e.unit
JOIN market.rows b ON b.kind='broken'
  AND json_extract(b.data,'$.unit')=e.unit""").fetchall() == [(600, 0)]
    assert observations(ledger.path) == (fact,)


@pytest.mark.parametrize(
    "raw",
    [
        '{"order_id":"","stage":"order","observed_at":0}',
        '{"order_id":"o","stage":"paid","observed_at":0}',
        '{"order_id":"o","stage":"order","observed_at":NaN}',
        '{"order_id":"o","stage":"order","observed_at":true}',
        '{"order_id":"o","stage":"order","observed_at":0,"price":true}',
        '{"order_id":"o","stage":"order","observed_at":0,"use_within_hours":-1}',
        '{"order_id":"o","stage":"order","observed_at":0,"grade":"D"}',
    ],
)
def test_invalid_facts_do_not_cross_the_model_boundary(raw: str) -> None:
    # Given/When: malformed, unobserved or impossible states cross the JSON boundary.
    with pytest.raises(ValidationError):
        _ = FoodEvidence.model_validate_json(raw)
    # Then: no FoodEvidence object exists to append.


def test_negative_stated_hours_remain_expired_evidence(tmp_path: Path) -> None:
    # Given: the seller explicitly stated that the unit has already expired.
    ledger = EventLedger(tmp_path / "events.sqlite")
    fact = FoodEvidence(
        order_id="o", stage="quote", observed_at=1000.0, stated_hours_left=-2.0
    )
    # When: persist the observation without a policy decision.
    ledger.record_food_evidence(fact)
    # Then: an expired value is not silently clipped into a fresh or unknown value.
    assert observations(ledger.path) == (fact,)
