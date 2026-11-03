"""labpc codex driver, DRY RUN only: the ssh prefix is labpc/mock_ssh.py (local, no network, no real codex)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "labpc"))
import codex_driver as D                      # noqa: E402

from llm import codex_client as C             # noqa: E402

MOCK = f"{sys.executable} {HERE / 'labpc' / 'mock_ssh.py'}"


@pytest.fixture
def slow(monkeypatch):
    monkeypatch.setenv("R3_MOCK_SLOW", "1")


def test_plan_is_6_10_4():
    p = D.plan()
    assert len(p) == 20
    assert sum(1 for r in p if r[1] == "editor") == 6 and sum(1 for r in p if r[1] == "parse") == 10
    assert sum(1 for r in p if r[1] in ("short_timeout", "injected")) == 4


def test_remote_cmd_flags_and_no_mktemp():
    rc = D.remote_cmd("gpt-6.1-terra", "/")
    assert rc.startswith("timeout -k 5 90 codex exec --ephemeral --skip-git-repo-check --sandbox read-only "
                         "--ignore-user-config -C / ")                       # v1.17b: remote timeout wrap
    assert "mktemp" not in rc and rc.endswith(" -m gpt-6.1-terra -")
    assert "--disable shell_tool" in rc


def test_dry_run_20_calls_then_stops(tmp_path, slow):
    out, cnt = tmp_path / "r.jsonl", tmp_path / "c.json"
    rows, counter = D.run(MOCK, out, cnt, "gpt-6.1-terra", "/", real=False)
    assert len(rows) == 20 and counter == {"editor": 6, "parse": 10, "reserve": 4}
    by = {}
    for r in rows:
        by.setdefault(r["case"], []).append(r)
    assert [r["format_ok"] for r in by["parse"]].count(False) == 1          # "about six grand" prose
    assert [r["format_ok"] for r in by["editor"]].count(False) == 1         # run=3 fenced
    assert all(r["timed_out"] and r["rule_fallback"] for r in by["short_timeout"])
    assert all(r["rule_fallback"] and not r["format_ok"] for r in by["injected"])
    assert all(r["exit"] in (0, 124) for r in rows)                        # 124 = remote timeout (v1.17b)
    assert all(r["exit"] == 124 for r in by["short_timeout"])
    text = out.read_text()
    assert "9000" not in text and "edits" not in text and "MSG" not in text     # no prompt / answer text logged
    s = D.summary(rows, counter)
    assert s["calls"] == 20 and s["counter_total"] == 20 and s["format_ok_rate_normal"] == round(14 / 16, 3)
    # 21st: refused before ssh, nothing runs
    rows2, counter2 = D.run(MOCK, out, cnt, "gpt-6.1-terra", "/", real=False)
    assert [r["status"] for r in rows2] == ["cap_reached"] and counter2 == counter


def test_missing_cwd_is_exit_and_fallback(tmp_path):
    rows, _ = D.run(MOCK, tmp_path / "r.jsonl", tmp_path / "c.json", "m", "/nonexistent-dir-r3", real=False, limit=2)
    assert all(r["exit"] == 98 and r["rule_fallback"] for r in rows)


def test_real_refused_without_env_or_with_mock(tmp_path, monkeypatch):
    monkeypatch.delenv("R3_V117_REAL_OK", raising=False)
    with pytest.raises(SystemExit):
        D.run("tailscale ssh LABPC", tmp_path / "r", tmp_path / "c", "m", "/", real=True)
    monkeypatch.setenv("R3_V117_REAL_OK", "1")
    with pytest.raises(SystemExit):
        D.run(MOCK, tmp_path / "r", tmp_path / "c", "m", "/", real=True)
    assert not (tmp_path / "c").exists()


def test_cli_dry_run(tmp_path):
    p = subprocess.run([sys.executable, str(HERE / "labpc" / "codex_driver.py"), "--ssh", MOCK, "--out",
                        str(tmp_path / "r.jsonl"), "--counter", str(tmp_path / "c.json"), "--limit", "3"],
                       capture_output=True, text=True, timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert p.returncode == 0, p.stderr
    s = json.loads(p.stdout)
    assert s["calls"] == 3 and s["counter_total"] == 3


# ---------------- Gate G / Gate P scripts: verbatim from DEPLOY_STEPS_r2.md, fed on stdin (box, fake dir)
DEPLOY = Path("/workspace/course/r3_deploy/v1.16.1/DEPLOY_STEPS_r2.md")


def _lines(a, b):
    return "".join(DEPLOY.read_text(encoding="utf-8").splitlines(keepends=True)[a - 1:b])


@pytest.mark.skipif(not DEPLOY.exists(), reason="DEPLOY_STEPS_r2.md stayed in the Grok staging env")
def test_gate_scripts_are_verbatim():
    assert (HERE / "labpc" / "gate_g_r2.py").read_text(encoding="utf-8") == _lines(120, 151) == _lines(309, 340)
    assert (HERE / "labpc" / "gate_p_r2.py").read_text(encoding="utf-8") == _lines(226, 266) == _lines(345, 385)


def test_gate_p_via_stdin_on_fake_dir(tmp_path):
    import time
    (tmp_path / ".r3").mkdir()
    (tmp_path / "logs").mkdir()
    old = time.time() - 4000
    (tmp_path / ".r3" / "metrics.jsonl").write_text(json.dumps({"hook": "seller", "decision": "open", "ts": old})
                                                     .replace(" ", "") + "\n")
    (tmp_path / "logs" / "market-kit.log").write_text("00:00:01 [kit] start\n")
    size = (tmp_path / "logs" / "market-kit.log").stat().st_size
    src = (HERE / "labpc" / "gate_p_r2.py").read_text()
    p = subprocess.run([sys.executable, "-", str(size)], input=src, cwd=tmp_path, capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0 and "PRECHECK GO" in p.stdout, (p.stdout, p.stderr)
    p = subprocess.run([sys.executable, "-"], input=src, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert "판정 불가(B_LOGSIZE 인자 없음)" in p.stdout


def test_gate_g_via_stdin_stops_without_kit(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "market-kit.log").write_text("")
    src = (HERE / "labpc" / "gate_g_r2.py").read_text()
    p = subprocess.run([sys.executable, "-", "8123"], input=src, cwd=tmp_path, capture_output=True, text=True,
                       timeout=60)
    assert "GATE STOP" in p.stdout and "Traceback" not in p.stderr, (p.stdout, p.stderr)


def test_parse_slower_than_parse_timeout_counts_as_rule_path(tmp_path, slow, monkeypatch):
    assert D.PA.PARSE_TIMEOUT_S == 8
    rows, _ = D.run(MOCK, tmp_path / "r.jsonl", tmp_path / "c.json", "m", "/", real=False, limit=7)
    p = rows[6]
    assert p["case"] == "parse" and p["latency_s"] >= 3 and p["format_ok"] and not p["over_parse_timeout"]
    assert p["rule_fallback"] is False                                       # 3 s < 8 s
    monkeypatch.setattr(D.PA, "PARSE_TIMEOUT_S", 1)
    rows, _ = D.run(MOCK, tmp_path / "r2.jsonl", tmp_path / "c2.json", "m", "/", real=False, limit=7)
    p = rows[6]
    assert p["format_ok"] and p["over_parse_timeout"] and p["rule_fallback"] is True
    assert not any(r["over_parse_timeout"] for r in rows if r["role"] == "editor")
