from __future__ import annotations

import pytest
from pydantic import ValidationError

from cnp.domain import Bid, Candidate, Task, TaskId, parse_bid, runtime_task, select_winner


def test_true_bid_parses_when_valid_json() -> None:
    # Given a valid contractor response.
    raw = '{"bid":true,"confidence":91,"reason":"계산"}'
    # When parsed at the boundary.
    parsed = parse_bid(raw)
    # Then typed values survive unchanged.
    assert parsed == Bid(bid=True, confidence=91, reason="계산")


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"bid":true,"confidence":91,"reason":"x"}\n```',
        '{"bid":"true","confidence":91,"reason":"x"}',
        '{"bid":true,"confidence":true,"reason":"x"}',
        '{"bid":true,"confidence":101,"reason":"x"}',
        '{"bid":true,"confidence":NaN,"reason":"x"}',
        '{"bid":true,"confidence":91,"reason":" "}',
        '{"bid":true,"confidence":91,"reason":"x","agent_id":"A"}',
    ],
)
def test_invalid_bid_rejected_without_repair(raw: str) -> None:
    # Given malformed JSON or a field outside the contract.
    # When parsed, then reject instead of inventing a valid bid.
    with pytest.raises(ValidationError):
        parse_bid(raw)


def test_tie_uses_input_order_when_highest_confidence_equal() -> None:
    # Given fixed A/B/C call order with B declining.
    bids = (
        Candidate("A", Bid(bid=True, confidence=98, reason="a")),
        Candidate("B", Bid(bid=False, confidence=100, reason="b")),
        Candidate("C", Bid(bid=True, confidence=98, reason="c")),
    )
    # When selecting, then retain the first true top score.
    assert select_winner(bids) == "A"


def test_overconfident_wrong_role_can_win() -> None:
    # Given C bids higher even though this could be a calculation task.
    bids = (
        Candidate("A", Bid(bid=True, confidence=91, reason="a")),
        Candidate("C", Bid(bid=True, confidence=98, reason="c")),
    )
    # When selecting, then do not apply a gold-based correction.
    assert select_winner(bids) == "C"


def test_unassigned_when_every_contractor_declines() -> None:
    # Given a response that declines with a high confidence value.
    bids = (Candidate("B", Bid(bid=False, confidence=100, reason="b")),)
    # When selecting, then there is no award.
    assert select_winner(bids) is None


def test_runtime_projection_omits_gold() -> None:
    # Given an evaluator-labelled input.
    task = Task(id=TaskId("T01"), desc="calculate", gold="A")
    # When projecting for runtime use.
    payload = runtime_task(task).model_dump()
    # Then only the public task is exposed.
    assert payload == {"id": "T01", "desc": "calculate"}
