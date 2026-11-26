"""Buyer-only model changes preserve seller values, bands and other writers."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import anyio

from r3.manager import FOLD_RUNS, Manager
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

    import pytest
    from pydantic import JsonValue


@contextmanager
def managed(path: Path) -> Generator[Manager, None, None]:
    manager = Manager(
        path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="me",
        shop_name="me",
    )
    try:
        yield manager
    finally:
        manager.http.close()


def reply(
    changes: dict[str, float | int], bands: dict[str, dict[str, float]]
) -> ModelReply:
    return ModelReply(
        json.dumps({"changes": changes, "bands": bands, "reason": "buyer trial"}),
        "claude-sonnet-5-5",
        1,
        1,
        1,
        0,
    )


def test_strategy_refuses_seller_restock_fields_and_bands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an active seller overlay and a model proposing both sides.
    with managed(tmp_path) as manager:
        _ = save_policy(
            manager.policy_file,
            Policy(),
            "base",
            bands={"seller.concession": Band(low=0.2, high=0.4)},
        )
        _ = save_tactical(manager.policy_file, {"seller.concession": 0.31}, "seller")
        before = load_history(manager.policy_file)
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(_request: Request) -> ModelReply:
            return reply(
                {
                    "seller.markup_a": 0.9,
                    "restock.stock_share": 0.8,
                    "buyer.max_rounds": 3,
                },
                {"seller.concession": {"low": 0.1, "high": 0.9}},
            )

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        # When: the strategy proposal passes through its real apply path.
        published = anyio.run(manager.ask_model, dict[str, int]())
        # Then: only the buyer field changes; seller layers and TTL survive.
        after = load_history(manager.policy_file)
        assert published is not None
        assert published.buyer.max_rounds == 3
        assert after.current.seller == before.current.seller
        assert after.current.restock == before.current.restock
        assert after.bands == before.bands
        assert after.field_expiries == before.field_expiries
        assert any(
            row["outcome"] == "refused"
            and row["detail"] == "refused: seller/restock locked"
            for row in manager.events.loop_runs(0)
        )


def test_strategy_does_not_overwrite_a_concurrent_policy_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: another writer updates policy while the model is replying.
    with managed(tmp_path) as manager:
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(_request: Request) -> ModelReply:
            _ = save_tactical(
                manager.policy_file, {"buyer.quick_accept_ratio": 0.73}, "other writer"
            )
            return reply({"buyer.max_rounds": 3}, {})

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        # When: the delayed proposal attempts to save its original snapshot.
        published = anyio.run(manager.ask_model, dict[str, int]())
        # Then: the other writer's value remains and the stale proposal is refused.
        after = load_history(manager.policy_file)
        assert published is None
        assert after.current.buyer.quick_accept_ratio == 0.73
        assert after.current.buyer.max_rounds == 4


def test_only_named_code_depth_triggers_bypass_restock_lock(tmp_path: Path) -> None:
    # Given: the same depth change is proposed by a model and a trusted code trigger.
    with managed(tmp_path) as manager:
        change = {"restock.depth_skus": 7}
        # When: origin is checked at the shared tactical boundary.
        model = manager.filter_tactical(change, "claude-haiku-5-5", "cash_idle")
        code = manager.filter_tactical(
            change, "code", "trigger cash_idle: idle cash", code_depth_trigger=True
        )
        # Then: the model is refused while the explicitly allowed code move survives.
        assert model == {}
        assert code == change


def test_historical_buyer_and_seller_moves_never_fold_into_base(tmp_path: Path) -> None:
    # Given: enough accepted historical moves to trigger the old permanent fold.
    with managed(tmp_path) as manager:
        for field in ("buyer.quick_accept_ratio", "seller.concession"):
            for _ in range(FOLD_RUNS):
                manager.events.record_policy_change(
                    actor="model",
                    layer="tactical",
                    field=field,
                    before=0.3,
                    after=0.31,
                    reason="repeated",
                    expires_at=None,
                    version=None,
                    accepted=True,
                )
        before = load_history(manager.policy_file)
        # When: the folding pass runs.
        folded = manager.fold_recurring_moves()
        # Then: repeated proposals are not treated as performance evidence.
        assert folded == {}
        assert load_history(manager.policy_file) == before


def test_strategy_cannot_shorten_owner_24h_warranties_or_change_their_bands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the owner has set all grades to 24h with existing seller bands.
    with managed(tmp_path) as manager:
        warranties: dict[str, JsonValue] = {
            "seller.warranty_a": 24,
            "seller.warranty_b": 24,
            "seller.warranty_c": 24,
        }
        _ = save_policy(
            manager.policy_file,
            apply_changes(Policy(), warranties),
            "owner 24h warranty decision",
            bands={"seller.concession": Band(low=0.2, high=0.4)},
        )
        before = load_history(manager.policy_file)
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(request: Request) -> ModelReply:
            assert request.role == "strategy"
            return reply(
                {
                    "seller.warranty_a": 0,
                    "seller.warranty_b": 6,
                    "seller.warranty_c": 4,
                    "buyer.max_rounds": 3,
                },
                {key: {"low": 0, "high": 6} for key in warranties},
            )

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        # When: Sonnet's proposal goes through the real strategy apply path.
        published = anyio.run(manager.ask_model, dict[str, int]())
        # Then: valid buyer work applies while all warranty values/bands survive.
        after = load_history(manager.policy_file)
        assert published is not None
        assert published.buyer.max_rounds == 3
        assert (
            after.current.seller.warranty_a,
            after.current.seller.warranty_b,
            after.current.seller.warranty_c,
        ) == (24, 24, 24)
        assert after.current.seller == before.current.seller
        assert after.bands == before.bands
        rejected = {
            row["field"]
            for row in manager.events.recent_policy_changes(1)
            if not row["accepted"] and row["note"] == "seller/restock locked"
        }
        assert rejected == set(warranties) | {f"band:{key}" for key in warranties}
        assert any(
            row["role"] == "strategy"
            and row["outcome"] == "refused"
            and row["detail"] == "refused: seller/restock locked"
            for row in manager.events.loop_runs(0)
        )


def test_tactical_cannot_shorten_owner_24h_warranties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: all grade warranties are 24h and a buyer adjustment is in-band.
    with managed(tmp_path) as manager:
        warranties: dict[str, JsonValue] = {
            "seller.warranty_a": 24,
            "seller.warranty_b": 24,
            "seller.warranty_c": 24,
        }
        _ = save_policy(
            manager.policy_file,
            apply_changes(Policy(), warranties),
            "owner 24h warranty decision",
            bands={"buyer.quick_accept_ratio": Band(low=0.6, high=0.9)},
        )
        before = load_history(manager.policy_file)
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(request: Request) -> ModelReply:
            assert request.role == "tactical"
            return ModelReply(
                json.dumps(
                    {
                        "changes": {
                            "seller.warranty_a": 0,
                            "seller.warranty_b": 6,
                            "seller.warranty_c": 4,
                            "buyer.quick_accept_ratio": 0.75,
                        },
                        "reason": "reduce warranty costs and improve buying",
                    }
                ),
                "claude-haiku-5-5",
                1,
                1,
                1,
                0,
            )

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        monkeypatch.setattr(manager, "tactical_summary", dict[str, object])
        # When: Haiku's proposal goes through the real tactical apply path.
        published = anyio.run(manager.tactical)
        # Then: only the valid buyer overlay is published.
        after = load_history(manager.policy_file)
        assert published is None
        assert after.current.buyer == before.current.buyer
        assert (
            after.current.seller.warranty_a,
            after.current.seller.warranty_b,
            after.current.seller.warranty_c,
        ) == (24, 24, 24)
        assert after.current.seller == before.current.seller
        assert after.bands == before.bands
        assert after.tactical is None
        rejected = {
            row["field"]
            for row in manager.events.recent_policy_changes(1)
            if not row["accepted"] and row["note"] == "seller/restock locked"
        }
        assert rejected == set(warranties)
        assert any(
            row["role"] == "tactical"
            and row["outcome"] == "refused"
            and row["detail"] == "refused: seller/restock locked"
            for row in manager.events.loop_runs(0)
        )
