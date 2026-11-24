from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from cnp.domain import ContractId, RuntimeTask, TaskId
from cnp.protocol import Protocol, ProtocolError
from cnp.records import ModelReply, Request
from cnp.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def database() -> Iterator[sqlite3.Connection]:
    # Given a real isolated SQLite database.
    with closing(sqlite3.connect(":memory:")) as db:
        yield db


def test_announcement_requires_queued_contract(database: sqlite3.Connection) -> None:
    # Given an unregistered contract.
    protocol = Protocol(Store(database), lambda: 10.0)
    request = Request(contract_id=ContractId("1:T01"), actor="M", request_id="announce")
    # When announcing, then refuse an unknown contract.
    with pytest.raises(ProtocolError, match="contract_error"):
        protocol.announce(request)


def test_bid_before_announcement_rejected(database: sqlite3.Connection) -> None:
    # Given a controller without a bidding contract.
    protocol = Protocol(Store(database), lambda: 10.0)
    request = Request(contract_id=ContractId("1:T01"), actor="A", request_id="bid")
    # When submitting, then no acceptance is invented.
    with pytest.raises(ProtocolError, match="contract_error"):
        protocol.submit_bid(request, ModelReply(text='{"bid":false,"confidence":0,"reason":"x"}'))


@pytest.fixture
def bidding(database: sqlite3.Connection) -> Protocol:
    protocol = Protocol(Store(database), lambda: 10.0)
    manager = Request(contract_id=ContractId("1:T01"), actor="M", request_id="start")
    protocol.get_task(manager, RuntimeTask(id=TaskId("T01"), desc="calculate"))
    protocol.announce(manager, window_s=20)
    return protocol


def submit_three(protocol: Protocol) -> Request:
    for agent, text in (
        ("A", '{"bid":true,"confidence":91,"reason":"calculate"}'),
        ("B", '{"bid":false,"confidence":0,"reason":"decline"}'),
        ("C", '{"bid":true,"confidence":84,"reason":"code"}'),
    ):
        request = Request.model_validate(
            {"contract_id": "1:T01", "actor": agent, "request_id": agent}
        )
        protocol.submit_bid(request, ModelReply(text=text))
    manager = Request(contract_id=ContractId("1:T01"), actor="M", request_id="award")
    protocol.seal(manager)
    return manager


def test_t01_has_six_protocol_messages(bidding: Protocol) -> None:
    # Given two true bids and one normal decline.
    manager = submit_three(bidding)
    # When the contract is awarded and closed.
    bidding.award(manager)
    result = bidding.close(manager)
    # Then state and count match the hand-worked design example.
    assert result.winner == "A"
    assert result.phase == "CLOSED"
    assert len(bidding.store.events("protocol_messages")) == 6


def test_duplicate_bid_never_adds_candidate(bidding: Protocol) -> None:
    # Given A already responded.
    req = Request(contract_id=ContractId("1:T01"), actor="A", request_id="a")
    reply = ModelReply(text='{"bid":true,"confidence":10,"reason":"x"}')
    bidding.submit_bid(req, reply)
    # When A tries to raise the score in a second response.
    with pytest.raises(ProtocolError, match="duplicate_bid"):
        bidding.submit_bid(req.model_copy(update={"request_id": "b"}), reply)
    # Then one response and one true-bid message remain.
    assert len(bidding.load(req).responses) == 1
    assert len(bidding.store.events("protocol_messages")) == 4


def test_deadline_rejects_late_bid_and_keeps_raw(bidding: Protocol) -> None:
    # Given the deadline has passed.
    bidding.clock = lambda: 30.0
    req = Request(contract_id=ContractId("1:T01"), actor="A", request_id="late")
    # When a response arrives at the deadline, then preserve a late event only.
    with pytest.raises(ProtocolError, match="late_bid"):
        bidding.submit_bid(req, ModelReply(text="late-original"))
    assert len(bidding.store.events("protocol_messages")) == 3
    assert "late-original" in bidding.store.events("system_events")[-2].detail


def test_role_guard_blocks_contractor_award(bidding: Protocol) -> None:
    # Given an agent instead of the Manager.
    req = Request(contract_id=ContractId("1:T01"), actor="A", request_id="bad")
    # When it awards, then reject regardless of phase.
    with pytest.raises(ProtocolError, match="role_error"):
        bidding.award(req)


def test_seal_refuses_to_discard_pending_responses(bidding: Protocol) -> None:
    # Given an open contract with no replies before deadline.
    req = Request(contract_id=ContractId("1:T01"), actor="M", request_id="seal")
    # When sealing early, then refuse.
    with pytest.raises(ProtocolError, match="responses_pending"):
        bidding.seal(req)


def test_exact_award_retry_does_not_duplicate_message(bidding: Protocol) -> None:
    # Given an awarded contract.
    req = submit_three(bidding)
    bidding.award(req)
    # When the exact command is retried.
    bidding.award(req)
    # Then the award remains single.
    assert sum(e.kind == "AWARD" for e in bidding.store.events("protocol_messages")) == 1


def test_award_rollback_when_event_insert_fails(bidding: Protocol) -> None:
    # Given a real SQLite trigger simulating a failing audit write.
    req = submit_three(bidding)
    bidding.store.db.execute(
        "CREATE TRIGGER fail_award BEFORE INSERT ON protocol_messages "
        "WHEN json_extract(NEW.payload,'$.kind')='AWARD' "
        "BEGIN SELECT RAISE(ABORT,'simulated disk error'); END;"
    )
    # When awarding, both the state and messages must roll back.
    with pytest.raises(sqlite3.IntegrityError):
        bidding.award(req)
    # Then no half-committed award exists.
    assert bidding.load(req).phase == "SEALED"
    assert len(bidding.store.events("protocol_messages")) == 5


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (ModelReply(text="not-json"), "parse_fail"),
        (ModelReply(error="api_error", error_detail="HTTP 503"), "api_error"),
        (ModelReply(error="timeout"), "timeout"),
        (ModelReply(text='{"bid":false,"confidence":0,"reason":"x"}'), "decline"),
    ],
)
def test_failures_are_distinct_from_abstention(
    bidding: Protocol,
    reply: ModelReply,
    status: str,
) -> None:
    # Given distinct terminal response types.
    req = Request(contract_id=ContractId("1:T01"), actor="A", request_id="response")
    # When recording one response.
    result = bidding.submit_bid(req, reply)
    # Then retain the outcome and never add a true-bid message.
    assert result.responses[0].status == status
    assert len(bidding.store.events("protocol_messages")) == 3
