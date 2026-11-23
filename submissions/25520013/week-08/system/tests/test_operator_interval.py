"""A2_OPERATOR_INTERVAL_S overrides the harness settings.json interval, still inside BOUNDS (2026-11-10, usage cut)."""

import json

from kernel import operator as op


def _settings(tmp_path, monkeypatch, env):
    (tmp_path / "operator").mkdir()
    (tmp_path / "operator" / "settings.json").write_text(json.dumps({"interval_s": 300}))
    o = op.Operator.__new__(op.Operator)
    o.harness_root = str(tmp_path)
    if env is None:
        monkeypatch.delenv("A2_OPERATOR_INTERVAL_S", raising=False)
    else:
        monkeypatch.setenv("A2_OPERATOR_INTERVAL_S", env)
    return o.settings()["interval_s"]


def test_harness_value_without_env(tmp_path, monkeypatch):
    assert _settings(tmp_path, monkeypatch, None) == 300


def test_env_overrides_harness(tmp_path, monkeypatch):
    assert _settings(tmp_path, monkeypatch, "1200") == 1200


def test_env_is_bounded_and_bad_value_ignored(tmp_path, monkeypatch):
    assert _settings(tmp_path, monkeypatch, "99999") == 7200


def test_bad_env_ignored(tmp_path, monkeypatch):
    assert _settings(tmp_path, monkeypatch, "abc") == 300
