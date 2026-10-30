"""Exercise the documented example through real journal and profile files."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from examples.operator_feedback_demo import DemoResult, run_demo
from market_a2a.meta_profile import Focus
from market_a2a.models import HarnessError

if TYPE_CHECKING:
    from pathlib import Path


def test_public_demo_changes_the_buyer_prior_and_rejects_false_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "new-run"
    result = run_demo(output)
    saved = DemoResult.model_validate_json((output / "result.json").read_bytes())
    assert saved == result
    assert result.first_profile.current.version == 1
    assert result.first_profile.current.priorities[0] == Focus.PRICE
    assert result.second_profile.current.version == 2
    assert result.second_profile.current.priorities[0] == Focus.TURNS
    assert result.second_profile.previous == (result.first_profile.current,)
    assert "strategy_prior" in result.buyer_context
    assert result.altered_evidence_rejected
    assert result.previous_profile_preserved
    assert not result.behavior_or_profit_evaluated


def test_public_demo_preserves_existing_evidence(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    _ = sentinel.write_text("prior evidence")
    with pytest.raises(HarnessError, match="new output directory"):
        _ = run_demo(output)
    assert sentinel.read_text() == "prior evidence"
    assert list(output.iterdir()) == [sentinel]
