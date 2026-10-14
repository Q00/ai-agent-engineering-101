"""Regression for the premature rejection found in live trade attempt one."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_current_seller import ModelWire, opening, send, shop

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def anyio_backend() -> str:
    """Use the installed async backend for the real SDK adapter."""
    return "asyncio"


@pytest.mark.anyio
async def test_low_first_offer_when_model_rejects_then_corrects(tmp_path: Path) -> None:
    # Given the same open lamp negotiation that failed in the live test.
    model = ModelWire()
    async with shop(tmp_path / "catalog.json", model) as client:
        task = await opening(client, model)
        model.decide("reject", 60)
        model.decide("quote", 100)
        # When the model tries to end the negotiation on the first low offer.
        counter = await send(client, "60", task)
        # Then the guard obtains a counteroffer while preserving the same task.
        assert counter.result is not None
        continued = counter.result.task
        assert continued.id == task.id
        assert continued.status.state == "TASK_STATE_INPUT_REQUIRED"
        assert "100" in continued.observation()
        model.decide("accept", 100)
        completed = await send(client, "100", continued)
        assert completed.result is not None
        assert completed.result.task.status.state == "TASK_STATE_COMPLETED"
        assert completed.result.task.artifacts[0].parts[0].data is not None
        assert completed.result.task.artifacts[0].parts[0].data.price == 100
