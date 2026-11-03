"""v1.17b fixes after CR '수정분 증분 검토' (review_r3_v117.md 261b25fe): condition 1 codex extra_args (any
non-empty refused + strict model / effort), condition 3 M10-2 TOCTOU (pinned dir fd + re-check before write),
condition 2 + EL 18:05 re-measurement driver (labpc/realrun_driver.py: prepare / stdin batch / ingest, counter from
0, cap 20 incl. injected, remote `timeout`). Mock / fake CLI only: no network, no real codex, no labpc."""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from llm import codex_client as C
from llm import editor as E
from llm.client import MockClient, json_reply
from tests import helpers as H
from tests.test_editor import budget, cycle, good, live_bytes, sb   # noqa: F401

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "labpc"))
import realrun_driver as R                    # noqa: E402

# ---------------- condition 1: extra_args
BYPASS = [["-c", 'sandbox_mode="danger-full-access"'], ["--config", "sandbox_mode=danger-full-access"],
          ["--config=sandbox_mode=danger-full-access"], ["-c", 'approval_policy="never"'],
          ["--enable", "shell_tool"], ["-p", "loose"], ["--profile", "loose"], ["--search"],
          ["-c", "shell_environment_policy.inherit=all"], ["--sandbox", "danger-full-access"],
          ["--dangerously-bypass-approvals-and-sandbox"], ["-C", "/"], ["--add-dir", "/"], ["-s", "workspace-write"],
          ["--full-auto"], ["--model", "other"], [""], ["harmless-looking"]]


@pytest.mark.parametrize("bad", BYPASS, ids=[" ".join(b) or "empty-string" for b in BYPASS])
def test_any_nonempty_extra_args_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        C.build_cmd("/x/codex", dict(C.load_config(), extra_args=bad), tmp_path)


@pytest.mark.parametrize("bad", ["-c sandbox_mode=x", {"a": 1}, 0, True])
def test_extra_args_wrong_type_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        C.build_cmd("/x/codex", dict(C.load_config(), extra_args=bad), tmp_path)


def test_empty_or_missing_extra_args_ok(tmp_path):
    cfg = dict(C.load_config())
    a = C.build_cmd("/x/codex", dict(cfg, extra_args=[]), tmp_path)
    cfg.pop("extra_args")
    assert C.build_cmd("/x/codex", cfg, tmp_path) == a
    for flag in ("--enable", "--config", "-p", "--profile", "--search"):
        assert flag not in a
    assert not any("sandbox_mode" in x or "approval_policy" in x for x in a)


@pytest.mark.parametrize("model", ["--search", "-c", "gpt x", "gpt;rm", "", "a" * 80, "-p"])
def test_model_cannot_carry_a_flag(tmp_path, model):
    with pytest.raises(ValueError):
        C.build_cmd("/x/codex", dict(C.load_config(), model=model), tmp_path)


@pytest.mark.parametrize("eff", ["high,sandbox_mode=danger-full-access", "medium\nx=1", "ultra", 1])
def test_effort_cannot_carry_an_override(tmp_path, eff):
    with pytest.raises(ValueError):
        C.build_cmd("/x/codex", dict(C.load_config(), reasoning_effort=eff), tmp_path)


def test_bad_settings_refused_before_a_count_is_used(tmp_path):
    from tests.test_codex_client import cfg, fake_cli
    exe = fake_cli(tmp_path, "cat > /dev/null; printf ok")
    cl = C.CodexClient("parse", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe,
                                                  extra_args=["--search"]), base=tmp_path)
    with pytest.raises(ValueError):
        cl.complete("p", max_tokens=5, timeout_s=10)
    assert cl.counter.read() == {"editor": 0, "parse": 0, "reserve": 0}


# ---------------- condition 3: M10-2 TOCTOU
def _race_layout(tmp_path):
    """sb with the live file in tmp/sbx/, a synthetic forbidden root tmp/forbidden/ holding the same bytes."""
    s = H.sandbox(tmp_path)
    sbx, forb = tmp_path / "sbx", tmp_path / "forbidden"
    sbx.mkdir()
    forb.mkdir()
    live = sbx / "live_policy.json"
    shutil.copyfile(s["live"], live)
    shutil.copyfile(s["live"], forb / "live_policy.json")          # byte-identical: _check_live would pass
    return dict(s, live=live), sbx, forb


class SwapClient(MockClient):
    """During the LLM call: sandbox dir -> moved aside, a symlink to the forbidden root put in its place."""

    def __init__(self, reply, sbx, forb):
        super().__init__(reply)
        self.sbx, self.forb = sbx, forb

    def complete(self, prompt, *, max_tokens, timeout_s):
        os.rename(self.sbx, self.sbx.with_name("sbx_moved"))
        os.symlink(self.forb, self.sbx)
        return super().complete(prompt, max_tokens=max_tokens, timeout_s=timeout_s)


@H.needs_proc_fd
def test_toctou_dir_swap_is_refused_and_forbidden_root_untouched(tmp_path, monkeypatch):
    s, sbx, forb = _race_layout(tmp_path)
    monkeypatch.setattr(E, "FORBIDDEN_ROOTS", E.FORBIDDEN_ROOTS + (forb,))
    before = (forb / "live_policy.json").read_bytes()
    r = cycle(s, SwapClient(json_reply(good()), sbx, forb), tmp_path, allowed_live=(s["live"],))
    assert r["status"] == "refused_live_moved" and r["stage"] == "adopt", r
    assert (forb / "live_policy.json").read_bytes() == before
    assert (tmp_path / "sbx_moved" / "live_policy.json").read_bytes() == before
    assert s["runs"].latest() == 1                                    # nothing registered
    assert json.loads(Path(s["audit"]).read_text().splitlines()[-1])["status"] == "refused_live_moved"


@H.needs_proc_fd
def test_toctou_write_goes_to_pinned_dir_even_without_recheck(tmp_path, monkeypatch):
    """Second layer: with the re-check disabled, the write still lands in the directory that was checked."""
    s, sbx, forb = _race_layout(tmp_path)
    monkeypatch.setattr(E, "FORBIDDEN_ROOTS", E.FORBIDDEN_ROOTS + (forb,))
    monkeypatch.setattr(E.LivePin, "recheck", lambda self: None)
    before = (forb / "live_policy.json").read_bytes()
    r = cycle(s, SwapClient(json_reply(good()), sbx, forb), tmp_path, allowed_live=(s["live"],))
    assert r["status"] == "adopted", r
    assert (forb / "live_policy.json").read_bytes() == before            # forbidden root unchanged
    moved = json.loads((tmp_path / "sbx_moved" / "live_policy.json").read_bytes())
    assert moved["defaults"]["food"]["price_mult"] == 0.95               # written via the pinned dir fd


def test_old_code_path_would_have_written_forbidden(tmp_path, monkeypatch):
    """Reproduces the CR finding against the pre-fix behaviour (write by the original path string)."""
    from harness import rollback as RB
    s, sbx, forb = _race_layout(tmp_path)
    os.rename(sbx, tmp_path / "sbx_moved")
    os.symlink(forb, sbx)
    before = (forb / "live_policy.json").read_bytes()
    RB.apply_proposal(s["runs"], good(), s["live"], H.T0 + 1000)           # what v1.17 did after the call
    assert (forb / "live_policy.json").read_bytes() != before


@H.needs_proc_fd
def test_live_file_swapped_for_symlink_is_refused(tmp_path, monkeypatch):
    s, sbx, forb = _race_layout(tmp_path)
    monkeypatch.setattr(E, "FORBIDDEN_ROOTS", E.FORBIDDEN_ROOTS + (forb,))

    class FileSwap(MockClient):
        def complete(self, prompt, **kw):
            (sbx / "live_policy.json").unlink()
            (sbx / "live_policy.json").symlink_to(forb / "live_policy.json")
            return super().complete(prompt, **kw)
    before = (forb / "live_policy.json").read_bytes()
    r = cycle(s, FileSwap(json_reply(good())), tmp_path, allowed_live=(s["live"],))
    assert r["status"] == "refused_live_moved"
    assert (forb / "live_policy.json").read_bytes() == before


@H.needs_proc_fd
def test_pin_refuses_symlinked_dir_at_start(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "live_policy.json").write_text("{}")
    link = tmp_path / "linkdir"
    link.symlink_to(real)
    with E.LivePin(link / "live_policy.json", (real / "live_policy.json",)) as p:   # resolved once -> real dir
        assert p.dir == str(real) and p.write_path.startswith("/proc/self/fd/")
        p.recheck()
        link.unlink()
        link.symlink_to(tmp_path)                                       # now resolves elsewhere
        with pytest.raises(E.LiveMoved):
            p.recheck()


@H.needs_proc_fd
def test_normal_adopt_and_rollback_still_work(sb, tmp_path):
    r = cycle(sb, MockClient(json_reply(good())), tmp_path)
    assert r["status"] == "adopted" and json.loads(live_bytes(sb))["defaults"]["food"]["price_mult"] == 0.95
    assert not list(Path(sb["live"]).parent.glob(".live_policy.json.*.tmp"))


# realrun driver tests moved to tests/test_fixes_v117c.py (v1.17c: ledger + integrity loader)


def test_codex_driver_refuses_real(tmp_path, monkeypatch):
    import codex_driver as D
    monkeypatch.setenv("R3_V117_REAL_OK", "1")
    with pytest.raises(SystemExit):
        D.run("ssh labpc", tmp_path / "r", tmp_path / "c", "gpt-6.1-terra", "/", real=True)
    assert not (tmp_path / "c").exists()


def test_remote_cmd_refuses_flag_model():
    import codex_driver as D
    with pytest.raises(ValueError):
        D.remote_cmd("--search", "/")
