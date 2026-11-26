import json
from pathlib import Path

import pytest

from acl_lab.settings import InputError, load_inputs


def _write_inputs(root: Path, scenarios: list[dict[str, int | str]]) -> None:
    config = {
        "provider": "test",
        "base_url": "https://example.invalid/v1",
        "model": "fake",
        "temperature": 0,
        "max_tokens": 64,
        "request_timeout_s": 10,
        "max_turns": 8,
        "repetitions": 3,
        "retry_delays_s": [1],
        "prompt_version": "test-v1",
    }
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")


def test_load_inputs_accepts_balanced_preregistered_scenarios(tmp_path: Path) -> None:
    # Given
    scenarios: list[dict[str, int | str]] = [
        {"id": 1, "item": "a", "reserve": 10, "budget": 20},
        {"id": 2, "item": "b", "reserve": 20, "budget": 20},
        {"id": 3, "item": "c", "reserve": 30, "budget": 20},
        {"id": 4, "item": "d", "reserve": 40, "budget": 10},
    ]
    _write_inputs(tmp_path, scenarios)

    # When
    settings, loaded = load_inputs(tmp_path)

    # Then
    assert settings.max_turns == 8
    assert len(loaded) == 4


def test_load_inputs_rejects_scenarios_with_only_possible_deals(tmp_path: Path) -> None:
    # Given
    scenarios: list[dict[str, int | str]] = [
        {"id": number, "item": str(number), "reserve": 10, "budget": 20} for number in range(1, 5)
    ]
    _write_inputs(tmp_path, scenarios)

    # When / Then
    with pytest.raises(InputError, match="scenario_classes_missing"):
        load_inputs(tmp_path)
