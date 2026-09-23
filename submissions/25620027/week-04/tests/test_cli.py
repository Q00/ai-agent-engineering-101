from pathlib import Path

import pytest
from typer.testing import CliRunner

from run import app


def test_dry_run_validates_full_matrix_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # When
    result = CliRunner().invoke(app, ["--dry-run"])

    # Then
    assert result.exit_code == 0
    assert "4 scenarios x 3 conditions x 3 repeats = 36 episodes" in result.stdout


def test_live_paid_run_requires_explicit_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    # When
    result = CliRunner().invoke(app, ["--output", str(tmp_path)])

    # Then
    assert result.exit_code != 0
    assert "Paid model requires explicit --allow-paid" in result.output
