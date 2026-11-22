"""New food observations preserve the real buyer's decisions and unknowns."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from functools import partial
from typing import TYPE_CHECKING, override

import anyio
import httpx2
import pytest
from pydantic import TypeAdapter

from market_a2a.remote import Part, Task
from r3 import buyer
from r3.events import EventLedger, FoodEvidence
from r3.market_files import KitFile, Order, ShopEntry
from r3.policy import BuyerPolicy, Policy, save_policy
from tests.r3_stock import KIMCHI, write_market
from tests.test_r3_fast_buyer import SellerPeer

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

NOW = 200000.0


class PeerTask(KitFile):
    """Parse the local fixture response at its HTTP boundary."""

    task: Task


class PeerEnvelope(KitFile):
    """The fake peer still uses the real Remote wire protocol."""

    result: PeerTask


class FoodPeer(SellerPeer):
    """Attach explicit certificate claims to an existing negotiation fixture."""

    @override
    def handle(self, request: httpx2.Request) -> httpx2.Response:
        response = super().handle(request)
        if request.method == "GET":
            return response
        wire = PeerEnvelope.model_validate_json(response.content)
        task = wire.result.task
        part = Part(
            data={
                "expires_at": NOW + 90 * 3600,
                "cert": {
                    "cert": {
                        "unit": "u-1",
                        "sku": KIMCHI,
                        "expires_at": NOW + 100 * 3600,
                        "grade": "A",
                        "warranty_hours": 0,
                    }
                },
            }
        )
        message = task.status.message.model_copy(
            update={"parts": (*task.status.message.parts, part)}
        )
        changed = task.model_copy(
            update={"status": task.status.model_copy(update={"message": message})}
        )
        return httpx2.Response(
            200,
            json=wire.model_copy(update={"result": PeerTask(task=changed)}).model_dump(
                mode="json", by_alias=True
            ),
        )


def evidence_rows(path: Path) -> list[FoodEvidence]:
    """Read the actual append-only records rather than mock the writer."""
    with closing(sqlite3.connect(path)) as db:
        rows = TypeAdapter(list[tuple[str]]).validate_python(
            db.execute(
                "SELECT payload FROM buyer_food_evidence ORDER BY observed_at,stage"
            ).fetchall()
        )
        return [FoodEvidence.model_validate_json(row[0]) for row in rows]


@pytest.mark.parametrize("unavailable", [False, True])
def test_real_food_buy_keeps_receipt_and_offer_when_observation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, unavailable: bool
) -> None:
    # Given: the same real buyer and local HTTP peer, with optional evidence IO loss.
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "food-observed",
                "item": "kimchi",
                "kind": "food",
                "acceptable": [KIMCHI],
                "budget": 60000,
                "use_within_hours": 2.5,
            }
        )
    )
    _ = (folder / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "Peer",
                    "url": "http://peer.test",
                    "offers": [{"sku": KIMCHI, "qty": 1}],
                }
            ]
        )
    )
    _ = save_policy(
        tmp_path / "policy.json",
        Policy(buyer=BuyerPolicy(food_margin_hours=48)),
        "food evidence fixture",
    )
    peer = FoodPeer(ask=20000, accept_at=19000, sku=KIMCHI)
    monkeypatch.setattr(buyer, "WALL_CLOCK", lambda: NOW)
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    original_write = EventLedger.record_food_evidence_batch

    def write_after_offer(self: EventLedger, evidence: Iterable[FoodEvidence]) -> None:
        assert peer.offers == [20000]
        if unavailable:
            message = "test observation unavailable"
            raise sqlite3.OperationalError(message)
        original_write(self, evidence)

    monkeypatch.setattr(EventLedger, "record_food_evidence_batch", write_after_offer)
    # When: drive choose, quote, binding acceptance and receipt through the real buy.
    result = anyio.run(
        partial(
            buyer.buy,
            folder / "shops.json",
            "kimchi",
            60000,
            folder / "order.json",
            lambda _line: None,
            folder=folder,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )
    # Then: price, signature-bearing receipt and existing min-life policy are identical.
    assert result.agreed_price == 20000
    assert result.receipt is not None
    assert result.receipt["unit"] == "u-1"
    assert peer.offers == [20000]
    assert json.loads(buyer.result_line(result))["accept"] == {
        "cert": True,
        "min_hours_left": 50.5,
    }
    rows = evidence_rows(tmp_path / "events.sqlite")
    if unavailable:
        assert rows == []
    else:
        assert {row.stage for row in rows} == {
            "order",
            "quote",
            "acceptance",
            "receipt",
        }
        quote = next(row for row in rows if row.stage == "quote")
        assert quote.price == 20000
        acceptance = next(row for row in rows if row.stage == "acceptance")
        assert acceptance.use_within_hours == 2.5
        assert acceptance.food_margin_hours == 48
        assert acceptance.certificate_expires_at == NOW + 100 * 3600
        assert acceptance.seller_expires_at == NOW + 90 * 3600
        assert acceptance.certificate_attached is True
        assert acceptance.unit == "u-1"
        assert acceptance.price == 20000
        assert acceptance.warranty_hours == 0


@pytest.mark.parametrize("kind", ["food", "goods"])
def test_unknown_food_facts_remain_null_and_goods_are_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # Given: a seller gives no structured certificate and the order lacks use time.
    ledger = EventLedger(tmp_path / "events.sqlite")
    order = Order.model_validate({"order": "o-1", "kind": kind})
    task = Task.model_validate(
        {"id": "t-1", "contextId": "c-1", "status": {"state": "TASK_STATE_COMPLETED"}}
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
    # When: append an observed receipt stage, without inventing checkout verification.
    observation = buyer.food_task_evidence(
        order, Policy(), choice, task, stage="receipt", price=0
    )
    if observation is not None:
        ledger.record_food_evidence_batch((observation,))
    # Then: only food has an observation and unknown facts stay distinct from zero.
    rows = evidence_rows(ledger.path)
    if kind == "goods":
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0].price == 0
        assert rows[0].use_within_hours is None
        assert rows[0].certificate_expires_at is None
        assert rows[0].certificate_attached is None
        assert rows[0].unit is None


def test_no_candidate_food_order_keeps_requirement_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a food order with no registered shops is still an issued opportunity.
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "food-no-candidate",
                "item": "kimchi",
                "kind": "food",
                "acceptable": [KIMCHI],
                "budget": 60000,
                "use_within_hours": 2.5,
            }
        )
    )
    _ = (folder / "shops.json").write_text("[]")
    _ = save_policy(
        tmp_path / "policy.json",
        Policy(buyer=BuyerPolicy(food_margin_hours=48)),
        "fixture",
    )
    monkeypatch.setattr(buyer, "WALL_CLOCK", lambda: NOW)
    # When: the real buyer returns before entering any negotiation.
    result = anyio.run(
        partial(
            buyer.buy,
            folder / "shops.json",
            "kimchi",
            60000,
            folder / "order.json",
            lambda _line: None,
            folder=folder,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )
    # Then: the failed opportunity keeps facts, without a guessed seller/expiry/credit.
    assert result.agreed_price is None
    rows = evidence_rows(tmp_path / "events.sqlite")
    assert len(rows) == 1
    assert rows[0].stage == "order"
    assert rows[0].use_within_hours == 2.5
    assert rows[0].food_margin_hours == 48
    assert rows[0].certificate_expires_at is None
    assert rows[0].shop is None


@pytest.mark.parametrize(
    ("field", "bad"),
    [("expires_at", True), ("expires_at", "300000"), ("warranty_hours", True)],
)
def test_malformed_certificate_times_do_not_turn_into_known_hours(
    monkeypatch: pytest.MonkeyPatch, field: str, *, bad: bool | str
) -> None:
    # Given: a structurally attached certificate contains a nonnumeric time claim.
    order = Order(order="food-malformed", kind="food", use_within_hours=2.5)
    task = Task.model_validate(
        {
            "id": "t-1",
            "contextId": "c-1",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"data": {"cert": {"cert": {field: bad}}}}]},
            },
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
    # When: convert only actual observations for later audit, without checkout claims.
    observed = buyer.food_task_evidence(
        order, Policy(), choice, task, stage="quote", price=None
    )
    # Then: attachment is observed while malformed times stay unknown rather than1h.
    assert observed is not None
    assert observed.certificate_attached is True
    assert observed.certificate_expires_at is None
    assert observed.warranty_hours is None


@pytest.mark.parametrize("bad_ask", [True, "20000", 10**1000, float("inf")])
def test_bad_structured_quote_is_unknown_without_changing_the_trade(
    monkeypatch: pytest.MonkeyPatch, *, bad_ask: bool | str | float
) -> None:
    # Given: external metadata is not necessarily a finite numeric quote.
    task = Task.model_validate(
        {
            "id": "t-1",
            "contextId": "c-1",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"metadata": {"ask": bad_ask}},
            },
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
    # When: the observation boundary parses metadata without using it to make offers.
    observed = buyer.food_task_evidence(
        Order(order="o-1", kind="food"),
        Policy(),
        choice,
        task,
        stage="quote",
        price=None,
    )
    # Then: a bad claim remains unknown instead of raising or becoming1credit.
    assert observed is not None
    assert observed.price is None
