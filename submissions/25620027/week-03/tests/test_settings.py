from __future__ import annotations

from pathlib import Path

from cnp.settings import load_inputs

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_configuration_selects_model_without_changing_tasks() -> None:
    # Given a separate free-model configuration and the same committed tasks.
    original, tasks = load_inputs(ROOT)
    # When selecting the alternative configuration.
    selected, alternative_tasks = load_inputs(ROOT, ROOT / "config-nemotron.json")
    # Then only the chosen configuration is substituted.
    assert selected.model == "nvidia/nemotron-3.5-lightning:free"
    assert selected.model != original.model
    assert alternative_tasks == tasks
