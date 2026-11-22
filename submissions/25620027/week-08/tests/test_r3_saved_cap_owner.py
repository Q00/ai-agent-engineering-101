"""Owner-controlled Saved price-cap experiments survive model proposals."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import anyio

from r3.manager import (
    PROMPT,
    TACTICAL_PROMPT,
    Manager,
    policy_schema,
    strategy_schema,
    tactical_schema,
)
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
    from pathlib import Path

    import pytest


def test_model_cannot_replace_owner_cap_but_other_buyer_moves_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="us",
        shop_name="us",
    )
    try:
        _ = save_policy(
            manager.policy_file,
            apply_changes(Policy(), {"buyer.max_price_ratio": 0.84}),
            "owner",
        )
        _ = save_tactical(
            manager.policy_file, {"buyer.quick_accept_ratio": 0.73}, "other"
        )
        before = load_history(manager.policy_file)
        client = ModelClient("fake", events=manager.events, quota=None)

        async def call(_request: Request) -> ModelReply:
            return ModelReply(
                json.dumps(
                    {
                        "changes": {
                            "buyer.max_price_ratio": 0.92,
                            "buyer.max_rounds": 3,
                        },
                        "bands": {},
                        "reason": "model tries to buy more",
                    }
                ),
                "claude-sonnet-5-5",
                1,
                1,
                1,
                0,
            )

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        summary: dict[str, int] = {}
        published = anyio.run(manager.ask_model, summary)
        after = load_history(manager.policy_file)
        assert published is not None
        assert after.current.buyer.max_price_ratio == 0.84
        assert after.current.buyer.max_rounds == 3
        assert after.current.buyer.quick_accept_ratio == 0.73
        assert after.field_expiries == before.field_expiries
        assert after.current.seller == before.current.seller
        assert after.current.restock == before.current.restock
        assert any(
            row["detail"] == "refused: owner Saved price cap locked"
            for row in manager.events.loop_runs(0)
        )
    finally:
        manager.http.close()


def test_only_cap_proposal_is_refused_without_a_policy_save(tmp_path: Path) -> None:
    manager = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="us",
        shop_name="us",
    )
    try:
        _ = save_policy(manager.policy_file, Policy(), "owner")
        before = manager.policy_file.read_bytes()
        assert (
            manager.filter_strategy(
                {"buyer.max_price_ratio": 0.97}, "model", "increase"
            )
            == {}
        )
        assert manager.policy_file.read_bytes() == before
    finally:
        manager.http.close()


def test_model_cap_band_refused_and_unrelated_band_retained(tmp_path: Path) -> None:
    manager = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="us",
        shop_name="us",
    )
    try:
        other = Band(low=0.45, high=0.60)
        result = manager.filter_model_bands(
            {
                "buyer.max_price_ratio": Band(low=0.8, high=0.99),
                "buyer.first_offer_ratio": other,
            },
            "model",
            "bands",
        )
        assert result == {}
    finally:
        manager.http.close()


def test_model_schema_hides_cap_while_owner_policy_schema_keeps_it() -> None:
    model = cast("dict[str, object]", strategy_schema()["properties"])
    changes = cast("dict[str, object]", model["changes"])
    assert "buyer.max_price_ratio" not in cast(
        "dict[str, object]", changes["properties"]
    )
    owner = cast("dict[str, object]", policy_schema()["properties"])
    changes = cast("dict[str, object]", owner["changes"])
    assert "buyer.max_price_ratio" in cast("dict[str, object]", changes["properties"])


def test_owner_cap_change_and_scoped_restore_preserve_unrelated_overlay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy.json"
    _ = save_policy(
        path, apply_changes(Policy(), {"buyer.max_price_ratio": 0.88}), "initial"
    )
    _ = save_tactical(path, {"buyer.quick_accept_ratio": 0.73}, "other", ttl=600)
    before = load_history(path)
    _ = save_policy(
        path,
        apply_changes(before.base(), {"buyer.max_price_ratio": 0.84}),
        "owner trial",
        expected_version=before.current.version,
    )
    trial = load_history(path)
    assert trial.current.buyer.max_price_ratio == 0.84
    assert trial.field_expiries == before.field_expiries
    _ = save_policy(
        path,
        apply_changes(trial.base(), {"buyer.max_rounds": 3}),
        "concurrent allowed",
        expected_version=trial.current.version,
    )
    latest = load_history(path)
    assert latest.current.buyer.max_price_ratio == 0.84
    _ = save_policy(
        path,
        apply_changes(latest.base(), {"buyer.max_price_ratio": 0.88}),
        "owner scoped restore",
        expected_version=latest.current.version,
    )
    restored = load_history(path)
    assert restored.current.buyer.max_price_ratio == 0.88
    assert restored.current.buyer.max_rounds == 3
    assert restored.current.buyer.quick_accept_ratio == 0.73
    assert restored.field_expiries == before.field_expiries
    assert restored.current.seller == before.current.seller
    assert restored.current.restock == before.current.restock


def test_model_timing_value_and_band_cannot_override_owner_trial(
    tmp_path: Path,
) -> None:
    manager = Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://us",
        buyer_name="us",
        shop_name="us",
    )
    try:
        _ = save_policy(
            manager.policy_file,
            apply_changes(Policy(), {"buyer.late_accept_share": 0.46}),
            "owner",
        )
        before = manager.policy_file.read_bytes()
        assert (
            manager.filter_tactical(
                {"buyer.late_accept_share": 0.26, "buyer.quick_accept_ratio": 0.74},
                "model",
                "try cheaper",
            )
            == {}
        )
        assert (
            manager.filter_strategy(
                {"buyer.late_accept_share": 0.26}, "model", "try cheaper"
            )
            == {}
        )
        assert (
            manager.filter_model_bands(
                {
                    "buyer.late_accept_share": Band(low=0.1, high=0.5),
                    "buyer.first_offer_ratio": Band(low=0.45, high=0.6),
                },
                "model",
                "band",
            )
            == {}
        )
        assert manager.policy_file.read_bytes() == before
    finally:
        manager.http.close()


def test_model_schemas_hide_timing_but_owner_schema_keeps_it() -> None:
    for schema in (strategy_schema(), tactical_schema()):
        props = schema["properties"]
        assert isinstance(props, dict)
        assert "buyer.late_accept_share" not in props["changes"]["properties"]
        if "bands" in props:
            assert "buyer.late_accept_share" not in props["bands"]["properties"]
    props = policy_schema()["properties"]
    assert isinstance(props, dict)
    assert "buyer.late_accept_share" in props["changes"]["properties"]


def test_owner_timing_change_drops_only_its_overlay_and_restores_base(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy.json"
    _ = save_policy(
        path,
        apply_changes(Policy(), {"buyer.late_accept_share": 0.44}),
        "base",
        bands={"buyer.late_accept_share": Band(low=0.2275, high=0.484)},
    )
    _ = save_tactical(
        path,
        {"buyer.late_accept_share": 0.26, "buyer.quick_accept_ratio": 0.74},
        "other",
    )
    before = load_history(path)
    _ = save_policy(
        path,
        apply_changes(before.base(), {"buyer.late_accept_share": 0.46}),
        "owner timing trial",
        expected_version=before.current.version,
    )
    after = load_history(path)
    assert after.current.buyer.late_accept_share == 0.46
    assert after.current.buyer.quick_accept_ratio == 0.74
    assert after.field_expiries == {
        k: v for k, v in before.field_expiries.items() if k != "buyer.late_accept_share"
    }
    assert after.current.seller == before.current.seller
    assert after.current.restock == before.current.restock
    _ = save_policy(
        path,
        apply_changes(after.base(), {"buyer.food_margin_hours": 36}),
        "concurrent model",
        expected_version=after.current.version,
    )
    current = load_history(path)
    _ = save_policy(
        path,
        apply_changes(current.base(), {"buyer.late_accept_share": 0.44}),
        "restore original base after original TTL",
        expected_version=current.current.version,
    )
    restored = load_history(path)
    assert restored.current.buyer.late_accept_share == 0.44
    assert restored.current.buyer.food_margin_hours == 36
    assert restored.current.buyer.quick_accept_ratio == 0.74
    assert restored.field_expiries == after.field_expiries


def test_models_distinguish_unpaid_orders_from_useless_settled_deals() -> None:
    for prompt in (PROMPT, TACTICAL_PROMPT):
        assert "Failed or unpaid orders are not settled deals" in prompt
        assert "must not be assigned a -1 Saved credit" in prompt
