from pathlib import Path

import pytest

from run import read_api_key


def test_api_key_reader_accepts_shell_export_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / "openai.env"
    env_file.write_text('export OPENAI_API_KEY="test-key"\n', encoding="utf-8")

    assert read_api_key(env_file) == "test-key"
