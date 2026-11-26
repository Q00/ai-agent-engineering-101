"""User-owned review prevents hidden buyer policy changes during maturation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

from r3.buyer import SAVED_FEEDBACK_FILE
from r3.buyer_report_models import BuyerReport
from r3.manager import BUYER_REVIEW_DEADLINE, Manager, owner_buyer_review_until
from r3.policy import Band, load_history, save_policy, save_tactical

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pytest


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


def control(path: Path, until: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps({"owner_control_until": until, "shops": {}}))


def test_review_rejects_all_model_buyer_layers_and_preserves_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an owner trial and a pre-existing independently expiring overlay.
    now = BUYER_REVIEW_DEADLINE - 1000
    monkeypatch.setattr("r3.manager.time.time", lambda: now)
    with managed(tmp_path) as manager:
        _ = save_policy(
            manager.policy_file, load_history(manager.policy_file).base(), "base"
        )
        _ = save_tactical(
            manager.policy_file,
            {"buyer.quick_accept_ratio": 0.74},
            "prior",
            now=now,
            ttl=700,
        )
        control(manager.run_dir / SAVED_FEEDBACK_FILE, BUYER_REVIEW_DEADLINE)
        before = load_history(manager.policy_file)
        # When: models propose tactical, strategy and band changes.
        tactical = manager.filter_tactical(
            {"buyer.quick_accept_ratio": 0.73}, "model", "trial"
        )
        strategy = manager.filter_strategy(
            {"buyer.food_margin_hours": 36, "buyer.surplus_score": False},
            "model",
            "trial",
        )
        bands = manager.filter_model_bands(
            {"buyer.quick_accept_ratio": Band(low=0.70, high=0.78)}, "model", "trial"
        )
        _ = manager.track_band_edges()
        # Then: all buyer writes are refused without altering another layer/TTL.
        assert tactical == strategy == bands == {}
        after = load_history(manager.policy_file)
        assert after == before
        assert all(
            not row["accepted"]
            for row in manager.events.recent_policy_changes(1)
            if row["actor"] == "model"
        )
        assert (
            len(
                [
                    row
                    for row in manager.events.recent_policy_changes(1)
                    if row["actor"] == "model"
                ]
            )
            == 4
        )


def test_missing_or_expired_control_retains_existing_model_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: legacy installations and expired owner trials have no new write lock.
    now = BUYER_REVIEW_DEADLINE - 1000
    monkeypatch.setattr("r3.manager.time.time", lambda: now)
    with managed(tmp_path) as manager:
        assert manager.filter_strategy({"buyer.max_rounds": 3}, "model", "legacy") == {
            "buyer.max_rounds": 3
        }
        control(manager.run_dir / SAVED_FEEDBACK_FILE, now - 1)
        # When/then: expiration does not extend the delegation or revive an overlay.
        assert manager.filter_strategy({"buyer.max_rounds": 3}, "model", "expired") == {
            "buyer.max_rounds": 3
        }
        control(manager.run_dir / SAVED_FEEDBACK_FILE, BUYER_REVIEW_DEADLINE)
        assert (
            owner_buyer_review_until(
                manager.run_dir / SAVED_FEEDBACK_FILE, BUYER_REVIEW_DEADLINE
            )
            == 0
        )


def test_corrupt_control_cannot_reopen_model_writes_or_extend_deadline(
    tmp_path: Path,
) -> None:
    # Given: a malformed ownership marker is distinct from stale neutral scores.
    path = tmp_path / SAVED_FEEDBACK_FILE
    _ = path.write_text("{bad")
    # When/then: corrupt data fails closed only within the existing delegation.
    assert (
        owner_buyer_review_until(path, BUYER_REVIEW_DEADLINE - 1)
        == BUYER_REVIEW_DEADLINE
    )
    assert owner_buyer_review_until(path, BUYER_REVIEW_DEADLINE + 1) == 0
    control(path, BUYER_REVIEW_DEADLINE + 500)
    assert (
        owner_buyer_review_until(path, BUYER_REVIEW_DEADLINE - 1)
        == BUYER_REVIEW_DEADLINE
    )


def test_manager_reuses_report_facts_and_preserves_control_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the existing report refresh has already captured its fixed sources.
    now = BUYER_REVIEW_DEADLINE - 1000
    monkeypatch.setattr("r3.manager.time.time", lambda: now)
    report = BuyerReport(
        generated_at=now,
        cohort_start=now - 3600,
        cohort_end=now,
        ledger_latest_at=now - 10,
        ledger_fresh=True,
        authoritative_order_times=0,
        local_observation_times=0,
        mature_bands={},
        mature_cohort_start=now - 21780 - 3600,
        mature_cohort_end=now - 21780,
        summary="fixed report",
        notes=(),
    )

    def fixed_report(
        _events_path: Path,
        _ledger_path: Path,
        _market_dir: Path,
        _buyer_name: str,
        _output_path: Path,
    ) -> BuyerReport:
        return report

    monkeypatch.setattr("r3.manager.write_buyer_report", fixed_report)
    calls: list[
        tuple[Path, Path, Path, str, Path, float, float | None, bool, float]
    ] = []

    def publisher(  # noqa: PLR0913 - match the explicit source boundary
        ledger_path: Path,
        facts_path: Path,
        catalog_path: Path,
        buyer_name: str,
        output_path: Path,
        *,
        observed_at: float,
        ledger_latest_at: float | None,
        ledger_fresh: bool,
        owner_control_until: float,
    ) -> None:
        calls.append(
            (
                ledger_path,
                facts_path,
                catalog_path,
                buyer_name,
                output_path,
                observed_at,
                ledger_latest_at,
                ledger_fresh,
                owner_control_until,
            )
        )

    monkeypatch.setattr("r3.manager.publish_saved_feedback", publisher)
    with managed(tmp_path) as manager:
        target = manager.run_dir / SAVED_FEEDBACK_FILE
        control(target, BUYER_REVIEW_DEADLINE)
        before = target.read_bytes()
        # When: the existing scheduled report refresh publishes the derived cache.
        manager.refresh_buyer_report()
        # Then: the exact cached facts/time and unchanged lease cross the boundary.
        assert calls == [
            (
                manager.ledger.path,
                (manager.run_dir / "r3-buyer-report.json").with_suffix(".sqlite"),
                manager.market_dir / "catalog.json",
                "me",
                target,
                now,
                now - 10,
                True,
                BUYER_REVIEW_DEADLINE,
            )
        ]
        assert target.read_bytes() == before
