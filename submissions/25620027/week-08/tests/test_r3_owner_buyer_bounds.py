"""Model proposals cannot widen the owner's immediate-accept limits."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest

from r3.manager import EDGE_RUNS_TO_WIDEN
from r3.model_client import ModelClient, ModelReply, Request
from r3.policy import Band, load_history, save_bands, save_tactical
from tests.test_r3_manager_buyer_lock import managed, reply

if TYPE_CHECKING:
    from pathlib import Path

KEY = "buyer.quick_accept_ratio"


@pytest.mark.parametrize(("low", "high"), [(0.70, 0.84), (0.69, 0.78)])
def test_strategy_refuses_owner_band_expansion_but_keeps_other_buyer_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, low: float, high: float
) -> None:
    with managed(tmp_path) as manager:
        _ = save_bands(manager.policy_file, {KEY: Band(low=0.70, high=0.78)})
        before = load_history(manager.policy_file)
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(_request: Request) -> ModelReply:
            return reply({"buyer.max_rounds": 3}, {KEY: {"low": low, "high": high}})

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        published = anyio.run(manager.ask_model, dict[str, int]())
        after = load_history(manager.policy_file)
        assert published is not None
        assert published.buyer.max_rounds == 3
        assert after.bands == before.bands
        assert after.current.seller == before.current.seller
        assert after.current.restock == before.current.restock
        assert any(
            row["outcome"] == "refused" and "owner" in str(row["detail"])
            for row in manager.events.loop_runs(0)
        )


@pytest.mark.parametrize("value", [0.69, 0.80, 0.84])
def test_tactical_rejects_outside_owner_limits_even_with_an_old_wide_band(
    tmp_path: Path, value: float
) -> None:
    with managed(tmp_path) as manager:
        _ = save_bands(manager.policy_file, {KEY: Band(low=0.60, high=0.90)})
        before = load_history(manager.policy_file)
        assert manager.filter_tactical({KEY: value}, "model", "try") == {}
        assert load_history(manager.policy_file) == before
        assert any(
            row["outcome"] == "refused" and "owner" in str(row["detail"])
            for row in manager.events.loop_runs(0)
        )


@pytest.mark.parametrize("value", [0.55, 0.58, 0.62])
def test_owner_prices_are_locked_even_inside_valid_band(
    tmp_path: Path, value: float
) -> None:
    with managed(tmp_path) as manager:
        _ = save_bands(manager.policy_file, {KEY: Band(low=0.70, high=0.78)})
        assert manager.filter_tactical({KEY: value}, "model", "bounded") == {}
        assert (
            manager.filter_model_bands(
                {KEY: Band(low=0.70, high=0.78)}, "model", "bounded"
            )
            == {}
        )


def test_code_edge_tracking_cannot_reopen_the_owner_band(tmp_path: Path) -> None:
    with managed(tmp_path) as manager:
        _ = save_bands(manager.policy_file, {KEY: Band(low=0.70, high=0.78)})
        _ = save_tactical(manager.policy_file, {KEY: 0.78}, "at cap")
        for _ in range(EDGE_RUNS_TO_WIDEN + 1):
            _ = manager.track_band_edges()
        assert load_history(manager.policy_file).bands[KEY] == Band(low=0.70, high=0.78)
