from pathlib import Path

import pytest
from typer.testing import CliRunner

from run_hybrid import app


def test_hybrid_dry_run_validates_twelve_episodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(app, ["--dry-run"])

    assert result.exit_code == 0
    assert "4 scenarios x 3 repeats = 12 hybrid episodes" in result.stdout


def test_hybrid_paid_run_requires_explicit_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")

    result = CliRunner().invoke(app, ["--output", str(tmp_path)])

    assert result.exit_code != 0
    assert "Paid model requires explicit --allow-paid" in result.output
