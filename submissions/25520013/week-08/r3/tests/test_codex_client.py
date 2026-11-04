"""Real-call adapter, exercised WITHOUT a model: disabled flag, missing CLI, a fake local CLI script (prints a fixed
reply / exits 1 like a not-logged-in CLI / sleeps past the timeout), the 20-call hard counter, and the call log
(hashes only). No test starts the real /home/box/.local/bin/codex."""
import json
import os
import stat
from pathlib import Path

import pytest

from llm import codex_client as C
from llm import editor as E
from llm.client import call_with_timeout
from tests import helpers as H
from tests.test_editor import budget, cycle, good, live_bytes, sb   # noqa: F401


def cfg(tmp_path, **kw):
    c = json.loads(C.CONFIG.read_text())
    c.update(counter_path="counter.json", call_log_path="calls.jsonl", **kw)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(c))
    return p


def fake_cli(tmp_path, body):
    p = tmp_path / "fakecodex"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


def test_shipped_config_defaults():
    c = C.load_config()
    # 2026-11-04 성수님: the LLM is a required design element, so the shipped config is ON (v121c, labpc codex)
    assert c["model"] == "gpt-6.1-terra" and c["real_calls_enabled"] is True and c["live_ops"] is True
    assert c["cli"] == "/home/sungsoo/.local/bin/codex" and c["extra_args"] == []    # sandbox flags fixed in code
    assert "--sandbox" in C.FIXED_ARGS and "--ephemeral" in C.FIXED_ARGS
    assert "@" not in C.CONFIG.read_text()                      # no account address in the config


def test_disabled_config_falls_back(tmp_path):
    # the shipped config is ON since 2026-11-04; a config with real calls off must still make no call
    cl = C.CodexClient("editor", config_path=cfg(tmp_path, real_calls_enabled=False), base=tmp_path)
    out, st = call_with_timeout(cl, "p", 10, 5)
    assert out is None and st == "error:RealCallsDisabled"
    assert not (tmp_path / "counter.json").exists()


@H.needs_proc_fd
def test_missing_cli_falls_back_and_editor_keeps_policy(sb, tmp_path):
    cl = C.CodexClient("editor", config_path=cfg(tmp_path, real_calls_enabled=True, cli=str(tmp_path / "nope")),
                       base=tmp_path)
    before = live_bytes(sb)
    r = cycle(sb, cl, tmp_path)
    assert r["status"] == "fallback_rules" and r["reasons"] == ["error:CodexUnavailable"]
    assert live_bytes(sb) == before and not (tmp_path / "counter.json").exists()


@H.needs_proc_fd
def test_fake_cli_reply_runs_full_editor_path(sb, tmp_path):
    reply = json.dumps(good())
    exe = fake_cli(tmp_path, f"cat > /dev/null; pwd > {tmp_path}/cwd.txt; printf '%s' '{reply}'")
    cl = C.CodexClient("editor", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe), base=tmp_path)
    r = cycle(sb, cl, tmp_path)
    assert r["status"] == "adopted", r
    assert C.HardCounter(tmp_path / "counter.json").read() == {"editor": 1, "parse": 0, "reserve": 0}
    log = (tmp_path / "calls.jsonl").read_text()
    row = json.loads(log)
    assert row["status"] == "ok" and row["model"] == "gpt-6.1-terra" and len(row["prompt_sha256"]) == 64
    assert "DATA" not in log and "price rejects" not in log       # neither prompt nor reply text is logged
    cwd = Path((tmp_path / "cwd.txt").read_text().strip())
    assert cwd.name.startswith("r3v117-codex-") and not cwd.exists()     # empty temp dir, removed after


@H.needs_proc_fd
def test_fake_cli_not_logged_in_exit1_falls_back(sb, tmp_path):
    exe = fake_cli(tmp_path, "echo 'Not logged in' >&2; exit 1")
    cl = C.CodexClient("editor", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe), base=tmp_path)
    r = cycle(sb, cl, tmp_path)
    assert r["status"] == "fallback_rules" and r["reasons"] == ["error:RuntimeError"]
    assert json.loads((tmp_path / "calls.jsonl").read_text())["status"] == "exit 1"


def test_fake_cli_timeout_falls_back(tmp_path):
    exe = fake_cli(tmp_path, "sleep 5")
    cl = C.CodexClient("editor", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe), base=tmp_path)
    out, st = call_with_timeout(cl, "p", 10, 0.5)          # outer join and inner subprocess share the 0.5 s deadline
    assert out is None and st in ("timeout", "error:TimeoutExpired")


def test_subprocess_timeout_inside_adapter(tmp_path):
    exe = fake_cli(tmp_path, "sleep 5")
    cl = C.CodexClient("parse", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe), base=tmp_path)
    out, st = call_with_timeout(cl, "p", 10, 3)          # outer 3 s, inner subprocess timeout_s=3 too
    assert out is None
    assert st in ("timeout", "error:TimeoutExpired")


def test_hard_counter_20_total_with_shares(tmp_path):
    hc = C.HardCounter(tmp_path / "c.json")
    for _ in range(C.EDITOR_CAP):
        hc.take("editor")
    for _ in range(C.RESERVE_CAP):
        hc.take("editor")                                  # over its share -> reserve
    with pytest.raises(C.CallCapReached):
        hc.take("editor")
    for _ in range(C.PARSE_CAP):
        hc.take("parse")
    assert hc.total() == C.TOTAL_CAP == 20
    with pytest.raises(C.CallCapReached):
        hc.take("parse")


def test_hard_counter_unreadable_is_full(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("garbage")
    with pytest.raises(C.CallCapReached):
        C.HardCounter(p).take("parse")


@H.needs_proc_fd
def test_counter_reached_falls_back_without_subprocess(sb, tmp_path):
    exe = fake_cli(tmp_path, f"touch {tmp_path}/ran; echo '{{}}'")
    p = cfg(tmp_path, real_calls_enabled=True, cli=exe)
    (tmp_path / "counter.json").write_text(json.dumps({"editor": 6, "parse": 10, "reserve": 4}))
    cl = C.CodexClient("editor", config_path=p, base=tmp_path)
    r = cycle(sb, cl, tmp_path)
    assert r["status"] == "fallback_rules" and r["reasons"] == ["error:CallCapReached"]
    assert not (tmp_path / "ran").exists()
