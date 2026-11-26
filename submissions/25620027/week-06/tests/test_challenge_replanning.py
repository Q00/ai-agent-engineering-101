from __future__ import annotations

import json
from pathlib import Path

import pytest

from market_a2a.challenges import ShoppingFixture, shopping_summary
from market_a2a.models import HarnessError


@pytest.fixture
def replay_log() -> tuple[str, ShoppingFixture]:
    fixture = ShoppingFixture.model_validate(
        {
            "budget": 500,
            "items": [
                {"request_id": "lamp", "description": "lamp", "expected_sku": "lamp"},
                {
                    "request_id": "chair",
                    "description": "chair",
                    "expected_sku": "chair",
                },
            ],
        },
    )
    lines = [
        """[SHOPPING PLAN] {"reservations":[
{"request_id":"lamp","shop_url":"http://local/shops/00/","cap":150},
{"request_id":"chair","shop_url":"http://local/shops/01/","cap":200}],
"reason":"initial"}""",
        """[BUDGET LEDGER] {"request_id":"lamp","spent":120,"released":30,
"reserved":200,"total_budget":500}""",
        """[BUDGET LEDGER] {"request_id":"chair","spent":120,"released":200,
"reserved":0,"total_budget":500}""",
        """[SHOPPING PLAN] {"reservations":[
{"request_id":"chair","shop_url":"http://local/shops/01/","cap":250}],
"reason":"retry after release"}""",
        """[BUDGET LEDGER] {"request_id":"chair","spent":320,"released":50,
"reserved":0,"total_budget":500}""",
    ]
    for item in ("lamp", "chair"):
        lines.extend(
            (
                '[A2A REQUEST] {"params":{"message":'
                + json.dumps({"taskId": item, "contextId": item})
                + "}}",
                '[A2A HTTP] {"method":"POST","url":"http://local","version":"1.0"}',
                "[NEGOTIATION SUMMARY] {}",
            ),
        )
    lines.append(
        """SHOPPING_RESULT [{"completed":true,"item":"lamp","sku":"lamp",
"price":120,"task_id":"lamp","shop":"http://local/shops/00/"},
{"completed":true,"item":"chair","sku":"chair","price":200,
"task_id":"chair","shop":"http://local/shops/01/"}]""",
    )
    return "\n".join(line.replace("\n", "") for line in lines), fixture


def test_summary_accepts_released_failed_purchase_then_success(
    replay_log: tuple[str, ShoppingFixture],
) -> None:
    # Given a failed chair checkout released its full cap before a new plan.
    log, fixture = replay_log
    # When the complete transcript is independently evaluated.
    summary = shopping_summary(log, fixture, Path(__file__).resolve().parents[1])
    # Then both receipts are paid once despite the additional failed attempt.
    assert summary.passed
    assert summary.total == 320
    assert len(summary.ledger) == 3


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ('"spent":120,"released":200', '"spent":119,"released":200'),
        ('"spent":120,"released":200', '"spent":120,"released":199'),
        ('"reserved":200', '"reserved":199'),
        ('"spent":320,"released":50', '"spent":319,"released":51'),
        ('"total_budget":500', '"total_budget":501'),
        ('"cap":250', '"cap":400'),
        (
            '"request_id":"chair","spent":320',
            '"request_id":"lamp","spent":320',
        ),
    ],
)
def test_summary_rejects_invalid_replanning_accounting(
    replay_log: tuple[str, ShoppingFixture], original: str, replacement: str
) -> None:
    # Given a valid retry transcript with one corrupted transition or duplicate plan.
    log, fixture = replay_log
    corrupted = log.replace(original, replacement)
    # When the evaluator examines the evidence, then it must refuse a pass.
    with pytest.raises(HarnessError):
        _ = shopping_summary(corrupted, fixture, Path(__file__).resolve().parents[1])
