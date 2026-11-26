"""Owner review keeps restock code proposals out of live economic policy."""

from __future__ import annotations

import json
from contextlib import closing
from typing import TYPE_CHECKING

import anyio
import pytest

from r3.buyer import SAVED_FEEDBACK_FILE
from r3.manager import BUYER_REVIEW_DEADLINE, EDGE_RUNS_TO_WIDEN, FOLD_RUNS, Manager
from r3.model_client import ModelClient, ModelReply, Request
from r3.policy import (
    Band,
    Policy,
    apply_changes,
    load_history,
    save_policy,
    save_tactical,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import JsonValue

NOW = BUYER_REVIEW_DEADLINE - 1000


@pytest.fixture
def manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Manager, None, None]:
    monkeypatch.setattr("r3.manager.time.time", lambda: NOW)
    result = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="me",
        shop_name="me",
    )
    with closing(result.http):
        _ = save_policy(
            result.policy_file,
            Policy(),
            "base",
            bands={"restock.depth_skus": Band(low=2, high=10)},
        )
        _ = save_tactical(
            result.policy_file,
            {"buyer.quick_accept_ratio": 0.74},
            "existing overlay",
            now=NOW,
            ttl=700,
        )
        yield result


def control(manager: Manager, until: float = BUYER_REVIEW_DEADLINE) -> None:
    _ = (manager.run_dir / SAVED_FEEDBACK_FILE).write_text(
        json.dumps({"owner_control_until": until})
    )


def snapshots(manager: Manager) -> tuple[bytes, bytes]:
    path = manager.policy_file
    return path.read_bytes(), path.with_suffix(path.suffix + ".expiries").read_bytes()


def historical_depth_moves(manager: Manager) -> None:
    for _ in range(FOLD_RUNS):
        manager.events.record_policy_change(
            actor="code",
            layer="tactical",
            field="restock.depth_skus",
            before=6,
            after=7,
            reason="trigger cash_idle: idle cash",
            expires_at=NOW + 200,
            version=None,
            accepted=True,
        )


def test_trusted_depth_trigger_is_a_recorded_proposal_under_owner_review(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a real tactical apply path with an eligible code trigger and active lease.
    control(manager)
    client = ModelClient("fake", events=manager.events, quota=None)

    async def call(_request: Request) -> ModelReply:
        return ModelReply(
            '{"changes":{},"reason":"review"}', "claude-haiku-5-5", 1, 1, 1, 0
        )

    def summary() -> dict[str, JsonValue]:
        return {
            "triggers": [
                {
                    "field": "restock.depth_skus",
                    "direction": 1,
                    "name": "cash_idle",
                    "why": "idle cash",
                }
            ]
        }

    monkeypatch.setattr(client, "call", call)
    monkeypatch.setattr(manager, "client", lambda: client)
    monkeypatch.setattr(manager, "tactical_summary", summary)
    before = snapshots(manager)
    # When: the model holds and the code fallback proposes a depth increase.
    published = anyio.run(manager.tactical)
    # Then: the candidate is recorded for the owner, without touching policy or TTL.
    assert published is None
    assert snapshots(manager) == before
    proposals = [
        row
        for row in manager.events.recent_policy_changes(1)
        if row["field"] == "restock.depth_skus"
    ]
    assert len(proposals) == 1
    assert proposals[0]["actor"] == "code"
    assert not proposals[0]["accepted"]
    assert json.loads(str(proposals[0]["after"])) > 6


def test_pinned_depth_band_is_proposed_without_widening_during_review(
    manager: Manager,
) -> None:
    # Given: depth is pinned long enough to widen its existing band.
    current = load_history(manager.policy_file)
    _ = save_policy(
        manager.policy_file,
        apply_changes(current.base(), {"restock.depth_skus": 10}),
        "owner depth",
        bands=current.bands,
    )
    manager.events.set_value(
        "edge_runs:restock.depth_skus", str(EDGE_RUNS_TO_WIDEN - 1)
    )
    control(manager)
    before = snapshots(manager)
    # When: the real automatic band maintenance pass runs.
    _ = manager.track_band_edges()
    # Then: its wider candidate is auditable but both policy files remain identical.
    assert snapshots(manager) == before
    proposals = [
        row
        for row in manager.events.recent_policy_changes(1)
        if row["field"] == "band:restock.depth_skus"
    ]
    assert len(proposals) == 1
    assert not proposals[0]["accepted"]
    assert json.loads(str(proposals[0]["after"]))["high"] > 10


def test_repeated_depth_moves_remain_proposals_instead_of_permanent_base(
    manager: Manager,
) -> None:
    # Given: historical trusted movements meet the old permanent-fold threshold.
    historical_depth_moves(manager)
    control(manager)
    before = snapshots(manager)
    # When: the real fold pass runs during the owner experiment.
    folded = manager.fold_recurring_moves()
    # Then: no base/overlay/TTL is changed; the proposed base value is retained.
    assert folded == {}
    assert snapshots(manager) == before
    proposals = [
        row
        for row in manager.events.recent_policy_changes(1)
        if row["layer"] == "strategy" and row["field"] == "restock.depth_skus"
    ]
    assert len(proposals) == 1
    assert not proposals[0]["accepted"]
    assert json.loads(str(proposals[0]["after"])) == 7


@pytest.mark.parametrize("until", [None, NOW - 1])
def test_missing_or_expired_review_keeps_legacy_depth_trigger(
    manager: Manager, until: float | None
) -> None:
    # Given: a deployment without active owner control retains its established behavior.
    if until is not None:
        control(manager, until)
    # When: the explicitly named code trigger crosses the tactical boundary.
    kept = manager.filter_tactical(
        {"restock.depth_skus": 7},
        "code",
        "trigger cash_idle: idle cash",
        code_depth_trigger=True,
    )
    # Then: the old allowance survives rather than silently imposing a global lock.
    assert kept == {"restock.depth_skus": 7}


@pytest.mark.parametrize("until", [None, NOW - 1])
def test_missing_or_expired_review_keeps_legacy_depth_fold(
    manager: Manager, until: float | None
) -> None:
    # Given: enough trusted historical moves and no current owner lease.
    historical_depth_moves(manager)
    if until is not None:
        control(manager, until)
    # When: the fold pass runs.
    folded = manager.fold_recurring_moves()
    # Then: the established legacy base update still occurs.
    assert folded == {"restock.depth_skus": 7}
    assert load_history(manager.policy_file).base().restock.depth_skus == 7


def test_natural_overlay_expiry_remains_authorized_during_review(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an expired depth overlay and a separate buyer TTL still in the future.
    _ = save_tactical(
        manager.policy_file,
        {"restock.depth_skus": 7},
        "prior owner overlay",
        now=NOW - 200,
        ttl=100,
    )
    original_expiry = load_history(manager.policy_file).field_expiries[
        "buyer.quick_accept_ratio"
    ]
    control(manager)
    monkeypatch.setattr(manager, "client", lambda: None)
    # When: tactical maintenance expires already-authorized overlays before model work.
    _ = anyio.run(manager.tactical)
    # Then: only the due field returns to base; the other original TTL remains.
    current = load_history(manager.policy_file)
    assert current.current.restock.depth_skus == current.base().restock.depth_skus == 6
    assert current.field_expiries["buyer.quick_accept_ratio"] == original_expiry
    assert current.current.buyer.quick_accept_ratio == 0.74


@pytest.mark.parametrize("until", [None, NOW - 1])
def test_missing_or_expired_review_keeps_legacy_band_widening(
    manager: Manager, until: float | None
) -> None:
    # Given: depth is pinned at a band edge and there is no active owner lease.
    history = load_history(manager.policy_file)
    _ = save_policy(
        manager.policy_file,
        apply_changes(history.base(), {"restock.depth_skus": 10}),
        "owner depth",
        bands=history.bands,
    )
    manager.events.set_value(
        "edge_runs:restock.depth_skus", str(EDGE_RUNS_TO_WIDEN - 1)
    )
    if until is not None:
        control(manager, until)
    # When: code maintains the real band.
    _ = manager.track_band_edges()
    # Then: the established wider band is still published.
    assert load_history(manager.policy_file).bands["restock.depth_skus"].high > 10
