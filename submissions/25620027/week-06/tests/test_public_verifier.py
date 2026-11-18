"""The public verifier must distinguish authorized prices from budget-only bounds."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest

from market_a2a.models import HarnessError
from market_a2a.remote import Remote
from verify_deployment import Recorder, Scenario, verify_case

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(("receipt_price", "authorized"), [(100, True), (120, False)])
def test_receipt_matches_actual_offer(
    tmp_path: Path, receipt_price: int, *, authorized: bool
) -> None:
    # Given: the seller quotes 100 and the buyer may spend up to 120.
    replies = iter(
        (
            {
                "result": {
                    "task": {
                        "id": "task-1",
                        "contextId": "context-1",
                        "status": {
                            "state": "TASK_STATE_INPUT_REQUIRED",
                            "message": {"parts": [{"text": "lamp asking 100"}]},
                        },
                    }
                }
            },
            {
                "result": {
                    "task": {
                        "id": "task-1",
                        "contextId": "context-1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [
                            {
                                "parts": [
                                    {
                                        "data": {
                                            "item": "lamp",
                                            "sku": "lamp",
                                            "price": receipt_price,
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                }
            },
            {"error": {"code": -32004, "message": "Task already completed"}},
        )
    )
    recorder = Recorder(tmp_path)

    def respond(_request: httpx2.Request) -> httpx2.Response:
        """Keep the actual HTTP parsing and offered-price logic in the test."""
        return httpx2.Response(200, json=next(replies))

    async def run() -> None:
        async with httpx2.AsyncClient(
            base_url="https://shop.invalid/", transport=httpx2.MockTransport(respond)
        ) as client:
            client.event_hooks["response"].append(recorder.capture)
            remote = Remote(client, recorder.emit)
            scenario = Scenario("lamp", 120, 100, (60, 80, 100, 110, 120))
            # When: the public verifier observes the receipt after offering 100.
            if authorized:
                result = await verify_case(remote, recorder, scenario)
                # Then: the exact authorized receipt is accepted.
                assert isinstance(result, dict)
                assert result["pass"] is True
            else:
                # Then: a receipt of 120 is rejected despite fitting the budget.
                with pytest.raises(HarnessError):
                    _ = await verify_case(remote, recorder, scenario)

    anyio.run(run)
