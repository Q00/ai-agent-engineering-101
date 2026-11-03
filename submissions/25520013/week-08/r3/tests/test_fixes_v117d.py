"""v1.17d fixes4: CR review_r3_v117_fixes3.md (a784913b) C1-C3, N1-N7, the three editor pre-live items, the
operating caps (CAP_PROPOSAL_v1.md a5464e4d) and the proposer round. Fake CLI / mock clients only: no network,
no real codex, no labpc. Every ledger here is a tmp ledger; the real ledger and HWM are checked unchanged."""
import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "labpc"))
import realrun_driver_v4 as R                 # noqa: E402
import realrun_ledger_v4 as LG                # noqa: E402
from llm import caps_v4 as CP                 # noqa: E402
from llm import codex_client_v4 as C4         # noqa: E402
from llm import editor_v4 as E4               # noqa: E402
from llm.client import MockClient, json_reply  # noqa: E402
from tests import helpers as H                # noqa: E402

REAL = [LG.LEDGER, LG.HWM]

FAKE = r'''#!/bin/bash
case "$1" in
  --version) echo "codex-cli 0.164.0-fake"; exit 0;;
  features) [ "${FAKE_NO_FEATURE:-}" = 1 ] && { echo "shell_tool stable true"; exit 0; }
            for f in shell_tool unified_exec shell_snapshot apps plugins remote_plugin browser_use \
                     browser_use_external in_app_browser computer_use view_image skill_search; do
              echo "$f stable true"; done; exit 0;;
  exec) if [ "${2:-}" = --help ]; then echo "--ephemeral --skip-git-repo-check --ignore-user-config --sandbox --disable"; exit 0; fi;;
esac
echo "$*" >> "${FAKE_LOG:-/dev/null}"
p=$(cat)
case "$p" in
  *CONTEXT:*R3CANARY*) echo '[{"subject":"x","predicate":"y","value":"R3CANARY","evidence":[999999],"op":"ADD"}]'; exit 0;;
  *CONTEXT:*) echo '[{"subject":"shop:a","predicate":"claim_honesty","value":"honest","evidence":[3],"op":"ADD"}]'; exit 0;;
esac
[ -n "${FAKE_LEAK:-}" ] && { echo "leak sk-ABCDEFGHIJKLMNOPQRSTUV"; exit 0; }
case "$p" in
  *SYSTEM:*) echo '{"offer": 1}';;
  *"new instruction"*) echo "I will not. R3CANARY";;
  *"<<<MSG"*) echo '{"offer": 9000}';;
  *) echo '{"edits": [], "rationale": "fake: no clear signal"}';;
esac
'''


@pytest.fixture(autouse=True)
def real_untouched():
    before = [p.read_bytes() if p.exists() else None for p in REAL]
    yield
    assert before == [p.read_bytes() if p.exists() else None for p in REAL], "a test touched the real ledger/HWM"


@pytest.fixture
def led(tmp_path):
    (tmp_path / "L").mkdir()
    lg = LG.Ledger(tmp_path / "L" / "ledger.jsonl", tmp_path / "W" / "hwm.json", allow_unpinned=True)
    lg.init("test approval", "test")
    return lg


@pytest.fixture
def fakebin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "codex").write_text(FAKE)
    (b / "codex").chmod(0o755)
    return b


def _run(script, fakebin, tmp_path, env=None):
    log = tmp_path / "fake.log"
    if log.exists():
        log.unlink()
    e = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", FAKE_LOG=str(log), **(env or {}))
    p = subprocess.run(["bash", "-c", "cat | bash -s"], input=script, capture_output=True, text=True, env=e,
                       timeout=200, cwd=str(tmp_path))
    return p, (len(log.read_text().splitlines()) if log.exists() else 0)


def _send(d, led, fakebin, tmp_path, script=None, env=None, raw=None):
    R.mark_sent(d, led)
    if raw is None:
        p, n = _run(script if script is not None else (d / "batch.sh").read_text(), fakebin, tmp_path, env)
        raw = p.stdout
    else:
        n = 0
    (d / "raw_stdout.txt").write_text(raw)
    R.capture(d, led)
    return raw, n


SPEC2 = {"editor": 1, "parse": 1, "short_timeout": 0, "injected": 0}


# ---------------- N1 ledger cannot be reset
def test_n1_unpinned_path_refused_and_hwm_must_be_separate(tmp_path):
    with pytest.raises(LG.LedgerError, match="pinned"):
        LG.Ledger(tmp_path / "x.jsonl")
    with pytest.raises(LG.LedgerError, match="separate"):
        LG.Ledger(tmp_path / "x.jsonl", tmp_path / "h.json", allow_unpinned=True)
    assert LG.LEDGER.is_absolute() and LG.HWM.parent != LG.LEDGER.parent


@pytest.mark.parametrize("how", ["delete", "truncate0", "init_only", "drop_last", "move", "rewrite", "hwm_deleted"])
def test_n1_deleted_truncated_moved_rewritten_ledger_is_refused(led, how):
    led.reserve("r1", 10)
    p = led.path
    lines = p.read_text().splitlines(keepends=True)
    if how == "delete":
        p.unlink()
    elif how == "truncate0":
        p.write_text("")
    elif how == "init_only":
        p.write_text(lines[0])
    elif how == "drop_last":
        p.write_text("".join(lines[:-1]))
    elif how == "move":
        p.rename(p.with_name("elsewhere.jsonl"))
    elif how == "rewrite":
        p.write_text(lines[0] + lines[1].replace('"n": 10', '"n": 1 '))
    else:
        led.hwm.unlink()
    with pytest.raises(LG.LedgerError):
        led.reserve("r2", 1)
    with pytest.raises(LG.LedgerError):
        led.summary()


def test_n1_init_and_adopt_are_explicit_and_one_time(tmp_path, led):
    with pytest.raises(LG.LedgerError, match="init refused"):
        led.init("again", "x")
    (tmp_path / "L2").mkdir()
    old = tmp_path / "L2" / "ledger.jsonl"
    old.write_text(json.dumps({"event": "init", "approval_id": "a", "cap": 20, "scope": "s"}) + "\n")
    lg = LG.Ledger(old, tmp_path / "W2" / "hwm.json", allow_unpinned=True)
    with pytest.raises(LG.LedgerError, match="no high-water"):
        lg.reserve("r", 1)
    with pytest.raises(LG.LedgerError, match="approval"):
        lg.adopt("")
    lg.adopt("approval text")
    with pytest.raises(LG.LedgerError, match="already exists"):
        lg.adopt("approval text")
    assert lg.summary()["remaining"] == 20
    nl = LG.Ledger(tmp_path / "L3" / "l.jsonl", tmp_path / "W3" / "h.json", allow_unpinned=True)
    with pytest.raises(LG.LedgerError, match="missing"):
        nl.reserve("r", 1)                                      # nothing is created implicitly
    assert not nl.path.exists()


def test_n1_every_event_reports_ledger_sha(led):
    led.reserve("r1", 2)
    r = led.mark_sent("r1")
    import hashlib
    assert r["ledger_sha256_after"] == hashlib.sha256(led.path.read_bytes()).hexdigest()


def test_n2_two_open_rounds_share_cap_and_proposer_scope_cap(led):
    with pytest.raises(LG.LedgerError, match="proposer"):
        led.reserve("p0", 11, scope="proposer", approval_id="x")
    with pytest.raises(LG.LedgerError):
        led.reserve("p0", 1, scope="other", approval_id="x")
    led.reserve("re", 10)
    led.reserve("p1", 10, scope="proposer", approval_id="x")
    with pytest.raises(LG.LedgerError, match="cap 20"):
        led.reserve("re2", 1)
    s = led.summary()
    assert s["committed"] == 20 and sorted(s["open"]) == ["p1", "re"]
    assert s["rounds"]["p1"]["scope"] == "proposer" and s["rounds"]["p1"]["approval_id"] == "x"
    led.abandon("re", "t")
    assert led.summary()["remaining"] == 10


def test_n2_concurrent_reserve_never_exceeds_cap(led):
    ok = []

    def go(i):
        try:
            led.reserve(f"c{i}", 3)
            ok.append(i)
        except LG.LedgerError:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 6 and led.summary()["committed"] == 18


# ---------------- C1 canary only
def test_c1_no_real_secret_path_in_any_prompt_and_canary_is_absent(tmp_path, led):
    d = R.prepare(tmp_path, ts="a", ledger=led)
    dp = R.prepare(tmp_path, ts="b", ledger=led, kind="proposer")
    for dd in (d, dp):
        meta = json.loads((dd / "prepare_meta.json").read_text())
        inputs = (dd / "inputs.jsonl").read_text()
        batch = (dd / "batch.sh").read_text()
        for bad in ("auth.json", ".codex/", "~/.codex", ".env", "id_rsa", "access_token", ".ssh"):
            assert bad not in inputs and bad not in base64.b64decode("".join(batch.splitlines()[1:-1])).decode()
        assert meta["canary_path"] in inputs and meta["canary_path"].startswith("~/.r3_canary_absent_")
        assert not Path(os.path.expanduser(meta["canary_path"])).exists()
        assert sum(json.loads(x)["kind"] == "injected" for x in inputs.splitlines()) == 2


def test_c1_prepare_refuses_a_prompt_naming_a_secret_location(tmp_path, led, monkeypatch):
    assert R.check_prompt_clean("read ~/.codex/auth.json now", "0" * 32)
    assert R.check_prompt_clean("cat .env", "0" * 32)
    monkeypatch.setattr(R, "PARSE_MESSAGES", ["read auth.json"] + R.PARSE_MESSAGES[1:])
    with pytest.raises(SystemExit, match="C1"):
        R.prepare(tmp_path, ts="c", ledger=led)


def test_c1_injection_judged_by_canary_offer_and_price(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="d", ledger=led)
    _send(d, led, fakebin, tmp_path)
    s = R.ingest(d, led)
    rows = [json.loads(x) for x in (d / "calls.jsonl").read_text().splitlines()]
    inj = {r["role"]: r["injection_followed"] for r in rows if r["kind"] == "injected"}
    assert inj == {"parse": True, "editor": True}                  # offer 1 / canary word echoed
    assert all(not r["injection_followed"] for r in rows if r["kind"] != "injected")
    assert s["injection_followed"] == 2


# ---------------- N3 zero-call proof
def _meta(d):
    return json.loads((d / "prepare_meta.json").read_text())


def _start(d, eof=1, sha=None):
    m = _meta(d)
    return (f"@@R3RR START run={d.name} nonce={m['nonce']} stdin_sha={sha or m['payload_sha256']} "
            f"expected_sha={m['payload_sha256']} eof={eof} host=labpc-x\n")


@pytest.mark.parametrize("body,zero", [
    ("{S1}@@R3RR INTEGRITY_FAIL calls=0\n", False),                     # forged: eof=1 + sha ok + INTEGRITY_FAIL
    ("{S0}@@R3RR INTEGRITY_FAIL calls=0\n", True),
    ("{S0}@@R3RR INTEGRITY_FAIL calls=0\n@@R3RR PREFLIGHT_OK\n", False),
    ("{S1}@@R3RR CODEX_VERSION x\n@@R3RR PREFLIGHT_OK\n@@R3RR PREFLIGHT_FAIL a calls=0\n", False),
    ("{S1}@@R3RR CODEX_VERSION x\n@@R3RR PREFLIGHT_FAIL a calls=0\n", True),
    ("{S1}@@R3RR PREFLIGHT_FAIL a calls=0\n@@R3RR PREFLIGHT_FAIL b calls=0\n", False),
    ("@@R3RR INTEGRITY_FAIL calls=0\n", False),                         # no START
])
def test_n3_zero_call_proof_must_be_consistent(tmp_path, led, fakebin, body, zero):
    d = R.prepare(tmp_path, ts="z", spec=SPEC2, ledger=led)
    raw = body.replace("{S1}", _start(d)).replace("{S0}", _start(d, eof=0))
    _send(d, led, fakebin, tmp_path, raw=raw)
    s = R.ingest(d, led)
    assert (s["ledger_used"] == 0) is zero, s["ledger_basis"]


def test_n3_real_integrity_and_preflight_fail_are_zero(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="i", spec=SPEC2, ledger=led)
    s = (d / "batch.sh").read_text()
    raw, n = _send(d, led, fakebin, tmp_path, script=s[: len(s) // 2])
    assert n == 0 and R.ingest(d, led)["ledger_used"] == 0
    d2 = R.prepare(tmp_path, ts="p", spec=SPEC2, ledger=led)
    raw, n = _send(d2, led, fakebin, tmp_path, env={"FAKE_NO_FEATURE": "1"})
    assert n == 0 and "PREFLIGHT_FAIL" in raw and R.ingest(d2, led)["ledger_used"] == 0


def test_n3_capture_once_and_raw_change_after_capture_refused(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="cap", spec=SPEC2, ledger=led)
    R.mark_sent(d, led)
    (d / "raw_stdout.txt").write_text("")
    with pytest.raises(SystemExit, match="capture"):
        R.ingest(d, led)
    R.capture(d, led)
    with pytest.raises(LG.LedgerError, match="already captured"):
        R.capture(d, led)
    (d / "raw_stdout.txt").write_text(_start(d, eof=0) + "@@R3RR INTEGRITY_FAIL calls=0\n")
    with pytest.raises(SystemExit, match="changed after capture"):
        R.ingest(d, led)


# ---------------- N4 / N5
def test_n4_unmarked_fail_text_is_ignored_not_indexerror(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="n4", spec=SPEC2, ledger=led)
    _send(d, led, fakebin, tmp_path, raw="ssh: note INTEGRITY_FAIL in a banner\n x PREFLIGHT_FAIL\n@@R3RR\n")
    s = R.ingest(d, led)
    assert s["ledger_used"] == 2 and s["fail_lines"] == [] and s["unmarked_lines"] >= 2


def test_n5_start_nonce_must_match_the_ledger(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="n5", spec=SPEC2, ledger=led)
    m = _meta(d)
    fake_nonce = "f" * 32
    meta_p = d / "prepare_meta.json"
    meta_p.chmod(0o644)
    meta_p.write_text(json.dumps(dict(m, nonce=fake_nonce)))            # meta forged to agree with the output
    raw = _start(d).replace(m["nonce"], fake_nonce) + "@@R3RR INTEGRITY_FAIL calls=0\n"
    _send(d, led, fakebin, tmp_path, raw=raw)
    with pytest.raises(SystemExit, match="ledger"):
        R.ingest(d, led)


# ---------------- N6 / N7 / C3 / C2
def test_n6_c3_host_and_evidence_block_and_report(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="c3", ledger=led)
    _send(d, led, fakebin, tmp_path)
    s = R.ingest(d, led)
    e = s["evidence_5_4"]
    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    assert s["host"] == e["start_host"] == host
    assert all(json.loads(x)["host"] == host for x in (d / "calls.jsonl").read_text().splitlines())
    assert e["sha_match"] and e["eof"] == 1 and e["codex_version_is_fake"] is True
    assert e["raw_stdout_sha256_captured"] == R.sha((d / "raw_stdout.txt").read_bytes())
    rep = (d / f"REPORT_5_4_{d.name}.md").read_text()
    for k in ("START host", "CODEX_VERSION", "stdin_sha / expected_sha", "eof", "SHA256SUMS_ingest",
              s["sha256sums_ingest"], "fake 표시: 있음"):
        assert k in rep
    assert s["sha256sums_ingest"] == R.sha((d / "SHA256SUMS_ingest").read_bytes())


def test_n7_secret_in_output_quarantines_raw_and_redacts(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="n7", spec={"editor": 0, "parse": 2, "short_timeout": 0, "injected": 0}, ledger=led)
    _send(d, led, fakebin, tmp_path, env={"FAKE_LEAK": "1"})
    s = R.ingest(d, led)
    assert not (d / "raw_stdout.txt").exists()
    q = d / "quarantine" / "raw_stdout.txt"
    assert q.exists() and (q.stat().st_mode & 0o777) == 0o600
    for f in ("raw_stdout.redacted.txt", "calls.jsonl", "summary.json", f"REPORT_5_4_{d.name}.md"):
        t = (d / f).read_text()
        assert "sk-ABCDEFGHIJ" not in t
        assert base64.b64encode(b"leak sk-ABCDEFGHIJKLMNOPQRSTUV").decode()[:20] not in t
    assert s["redacted_rows"] == [0, 1] and s["evidence_5_4"]["raw_quarantined"]


def test_c2_uncertain_result_is_closed_as_all_used_and_never_resent(tmp_path, led):
    d = R.prepare(tmp_path, ts="c2", ledger=led)
    with pytest.raises(LG.LedgerError, match="never sent"):
        R.close_uncertain(d, "x", led)
    R.mark_sent(d, led)
    with pytest.raises(LG.LedgerError, match="already marked sent"):
        R.mark_sent(d, led)
    with pytest.raises(LG.LedgerError, match="marked sent"):
        R.abandon(d, "x", led)
    r = R.close_uncertain(d, "timeout on the Mac", led)
    assert r["used"] == 10 and r["released"] == 0 and led.summary()["committed"] == 10
    with pytest.raises(LG.LedgerError):
        R.mark_sent(d, led)


def test_c2_labpc_commands_v4_single_send_no_retry():
    t = (HERE / "LABPC_COMMANDS_v4.md").read_text()
    assert "Shell 호출 1회" in t and "재시도 없음" in t and "close-uncertain" in t and "auth.json" not in t


# ---------------- proposer round
def test_proposer_round_prepare_run_ingest(tmp_path, led, fakebin):
    d = R.prepare(tmp_path, ts="pp", ledger=led, kind="proposer")
    m = _meta(d)
    assert m["n"] == 10 and m["scope"] == "proposer" and json.loads((d / "counter.json").read_text())["by_role"] == {"memo": 10}
    raw, n = _send(d, led, fakebin, tmp_path)
    assert n == 10
    s = R.ingest(d, led)
    assert s["by_kind"]["memo"]["format_ok"] == 8 and s["by_kind"]["memo"]["bound_ok"] == 8
    assert s["injection_followed"] == 2 and s["ledger_used"] == 10
    assert "memo_proposer.py" in (d / "SHA256SUMS").read_text()


# ---------------- editor pre-live items
@pytest.fixture
def sb(tmp_path):
    return H.sandbox(tmp_path)


def _good(domain="food"):
    return {"edits": [{"path": f"defaults.{domain}.price_mult", "value": 0.95}],
            "rationale": "price rejects dominate", "evidence_seq": [39, 40], "kind_hint": domain}


def test_editor1_planted_symlink_at_old_tmp_name_is_not_followed(sb, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("KEEP")
    live = Path(sb["live"])
    os.symlink(outside, live.with_name(f".{live.name}.{os.getpid()}.tmp"))
    with E4.LivePin(live, (live,)) as pin:
        v, new = E4.apply_proposal_pinned(sb["runs"], _good(), pin, H.T0 + 5)
    assert outside.read_text() == "KEEP" and live.read_bytes() == new and v == 2
    left = [p.name for p in live.parent.iterdir() if p.name.startswith(f".{live.name}.") and not p.is_symlink()]
    assert left == []


def test_editor1_tmp_is_unique_exclusive_and_cleaned_on_failure(sb, monkeypatch):
    live = Path(sb["live"])
    seen = []
    real = os.replace

    def boom(a, b, *k, **kw):
        seen.append(a)
        raise OSError("disk")
    monkeypatch.setattr(os, "replace", boom)
    with E4.LivePin(live, (live,)) as pin:
        with pytest.raises(OSError):
            E4._atomic_pinned(pin, b"x")
    monkeypatch.setattr(os, "replace", real)
    assert seen and f".{os.getpid()}.tmp" not in seen[0] and not Path(seen[0]).exists()
    assert not [p for p in live.parent.iterdir() if p.name.startswith(f".{live.name}.")]


def test_editor1_run_cycle_and_rollback_use_pinned_writer(sb, tmp_path, monkeypatch):
    import harness.rollback as RB
    monkeypatch.setattr(RB, "_atomic", lambda *a: (_ for _ in ()).throw(AssertionError("old _atomic used")))
    from llm.budget import Budget
    bud = Budget(tmp_path / "b.json", per_hour=9, per_day=9, tokens_per_day=10 ** 6)
    r = E4.run_cycle(client=MockClient(json_reply(_good())), domain="food", card_text=H.card(), card_seq=40,
                     lessons=H.LESSONS, events=sb["events"], ledger_path=sb["ledger"], runs=sb["runs"],
                     live_policy_path=sb["live"], budget=bud,
                     ab_eval=lambda a, p: {"point": 900.0, "lo": 120.0, "hi": 1600.0, "n_A": 30, "n_B": 30},
                     salt="t", audit_path=sb["audit"], now=H.T0 + 1000, allowed_live=(sb["live"],))
    assert r["status"] == "adopted"
    with E4.LivePin(sb["live"], (sb["live"],)) as pin:
        v, raw = E4.rollback_pinned(sb["runs"], 1, pin, H.T0 + 2000)
    assert Path(sb["live"]).read_bytes() == sb["runs"].policy_bytes(1) and v == 3


def test_editor2_model_fullmatch():
    for bad in ("gpt-6.1-terra\n", "gpt-6.1-terra\n-c x", " gpt", "gpt-6.1-terra "):
        with pytest.raises(ValueError):
            C4.check_settings({"model": bad, "extra_args": []})
    assert C4.check_settings({"model": "gpt-6.1-terra"})[0] == "gpt-6.1-terra"


def _cfg(tmp_path, **kw):
    c = {"real_calls_enabled": True, "model": "gpt-6.1-terra", "reasoning_effort": "medium",
         "cli": str(tmp_path / "bin" / "codex"), "extra_args": []}
    c.update(kw)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(c))
    return p


def test_editor3_counter_and_log_paths_pinned(tmp_path, fakebin):
    for k, v in (("counter_path", "state/other.json"), ("call_log_path", "/tmp/x.jsonl")):
        with pytest.raises(ValueError, match="pinned"):
            C4.CodexClient("editor", config_path=_cfg(tmp_path, **{k: v}), base=tmp_path)
    c = C4.CodexClient("editor", config_path=_cfg(tmp_path, counter_path=C4.COUNTER_REL), base=tmp_path)
    assert c.counter.path == tmp_path / C4.COUNTER_REL


def test_editor3_cli_allow_list_and_minimal_env(tmp_path, fakebin, monkeypatch):
    exe = str(fakebin / "codex")
    seen = {}

    def runner(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setenv("SECRET_LIKE_VAR", "x")
    c = C4.CodexClient("editor", config_path=_cfg(tmp_path), base=tmp_path, runner=runner, allowed_cli=())
    with pytest.raises(C4.CodexUnavailable, match="allow-list"):
        c.complete("p", max_tokens=5, timeout_s=5)
    assert not (tmp_path / C4.COUNTER_REL).exists()                         # refused before a count
    for cli in ("codex", "bin/codex"):
        c = C4.CodexClient("editor", config_path=_cfg(tmp_path, cli=cli), base=tmp_path, runner=runner,
                           allowed_cli=(exe,))
        with pytest.raises(C4.CodexUnavailable, match="absolute"):
            c.complete("p", max_tokens=5, timeout_s=5)
    os.chmod(exe, 0o777)
    c = C4.CodexClient("editor", config_path=_cfg(tmp_path), base=tmp_path, runner=runner, allowed_cli=(exe,))
    with pytest.raises(C4.CodexUnavailable, match="writable"):
        c.complete("p", max_tokens=5, timeout_s=5)
    os.chmod(exe, 0o755)
    assert c.complete("p", max_tokens=5, timeout_s=5) == "ok"
    assert set(seen["env"]) <= {"PATH", "HOME", "LANG", "LC_ALL", "CODEX_HOME"} and "SECRET_LIKE_VAR" not in seen["env"]
    assert seen["cmd"][0] == os.path.realpath(exe)


def test_old_modules_are_unchanged_by_this_bundle():
    import hashlib
    want = {"llm/editor.py": "f409220a", "llm/codex_client.py": "d6661dc4", "labpc/realrun_driver.py": "a9b64581",
            "labpc/realrun_ledger.py": "3d4807bc"}
    for f, h in want.items():
        assert hashlib.sha256((HERE / f).read_bytes()).hexdigest().startswith(h), f


# ---------------- operating caps
class Clock:
    def __init__(self, t=1_793_700_000.0):
        self.t = t

    def __call__(self):
        return self.t


def caps(tmp_path, enabled=True, clock=None):
    return CP.OpsCaps(tmp_path, config={"enabled": enabled}, clock=clock or Clock())


def test_caps_default_config_is_disabled_and_makes_no_call(tmp_path):
    assert CP.load_config()["enabled"] is False
    assert CP.load_config(tmp_path / "missing.json")["enabled"] is False
    c = MockClient("x")
    o = CP.OpsCaps(tmp_path, clock=Clock())
    assert o.call("parse", c, "p", 5, "d1") == (None, "disabled") and c.prompts == []


def test_caps_parse_per_deal_hour_day_round(tmp_path):
    clk = Clock()
    o = caps(tmp_path, clock=clk)
    c = MockClient("x")
    assert o.call("parse", c, "p", 5, "d0")[1] == "ok"
    assert o.call("parse", c, "p", 5, "d0")[1] == "per_deal"
    assert o.call("parse", c, "p", 5, None)[1] == "no_deal_id"
    for i in range(1, 6):
        assert o.call("parse", c, "p", 5, f"d{i}")[1] == "ok"
    assert o.call("parse", c, "p", 5, "d6")[1] == "per_hour"
    n = 6
    for h in range(1, 30):
        clk.t += 3600
        for k in range(6):
            if o.call("parse", c, "p", 5, f"h{h}k{k}")[1] == "ok":
                n += 1
        if n >= 72:
            break
    clk.t += 3600
    assert o.call("parse", c, "p", 5, "late")[1] == "per_day" and n == 72
    st = json.loads((tmp_path / "caps_parse.json").read_text())
    assert st["round_total"] == 72 and CP.PARSE["per_round"] == 1080


def test_caps_editor_interval_day_and_separate_counters(tmp_path):
    clk = Clock()
    o = caps(tmp_path, clock=clk)
    c = MockClient("x")
    for i in range(6):
        o.call("parse", c, "p", 5, f"d{i}")                             # parse use does not touch the editor
    assert o.editor_call(c, "p", 5)[1] == "ok"
    clk.t += 4 * 3600 - 1
    assert o.editor_call(c, "p", 5)[1] == "min_interval"
    ok = 1
    for _ in range(10):
        clk.t += 4 * 3600
        ok += o.editor_call(c, "p", 5)[1] == "ok"
        if ok == 6:
            break
    assert ok == 6
    assert CP.EDITOR["max_edits"] == 1 and CP.EDITOR["timeout_s"] == 60 and CP.EDITOR["per_round"] == 90
    assert not (tmp_path / "caps_editor.json").samefile(tmp_path / "caps_parse.json")


def test_caps_editor_six_per_day_hard(tmp_path, monkeypatch):
    monkeypatch.setitem(CP.EDITOR, "min_interval_s", 0)
    monkeypatch.setitem(CP.EDITOR, "per_hour", 99)
    clk = Clock()
    r = CP.RoleCounter(tmp_path / "e.json", "editor")
    got = [r.reserve(clk.t + k * 60)[1] for k in range(7)]
    assert got == ["ok"] * 6 + ["per_day"]
    assert r.reserve(clk.t + 86400 + 1)[1] == "ok"


class _Raise:
    def __init__(self, msg):
        self.msg, self.calls = msg, 0

    def complete(self, p, *, max_tokens, timeout_s):
        self.calls += 1
        raise RuntimeError(self.msg)


def test_caps_breaker_quota_error_off_1h_then_day(tmp_path):
    clk = Clock(1_793_700_000.0)                                       # 2026-11-03 ~ 19:00 KST
    o = caps(tmp_path, clock=clk)
    assert o.call("parse", _Raise("HTTP 429 Too Many Requests"), "p", 5, "a")[1].startswith("error")
    c = MockClient("x")
    assert o.call("parse", c, "p", 5, "b") == (None, "breaker_open")
    assert o.call("editor", c, "p", 5) == (None, "breaker_open")        # both roles
    clk.t += 3601
    assert o.call("parse", c, "p", 5, "c")[1] == "ok"
    o.call("parse", _Raise("usage limit reached"), "p", 5, "d")         # second trip same KST day
    clk.t += 3601
    assert o.call("parse", c, "p", 5, "e") == (None, "breaker_open")
    off = json.loads((tmp_path / "caps_breaker.json").read_text())["off_until"]
    assert (off + 9 * 3600) % 86400 == 0


def test_caps_breaker_three_timeouts_and_success_resets(tmp_path, monkeypatch):
    monkeypatch.setitem(CP.PARSE, "timeout_s", 0.05)
    monkeypatch.setitem(CP.PARSE, "per_hour", 99)
    monkeypatch.setitem(CP.PARSE, "per_day", 99)
    clk = Clock()
    o = caps(tmp_path, clock=clk)
    slow = MockClient("x", delay_s=0.3)
    ok = MockClient("x")
    assert o.call("parse", slow, "p", 5, "a")[1] == "timeout"
    assert o.call("parse", slow, "p", 5, "b")[1] == "timeout"
    time.sleep(0.6)
    assert o.call("parse", ok, "p", 5, "c")[1] == "ok"                  # streak reset
    for k in "def":
        assert o.call("parse", slow, "p", 5, k)[1] == "timeout"
    time.sleep(0.6)
    assert o.call("parse", ok, "p", 5, "g") == (None, "breaker_open")


def test_caps_generic_error_does_not_trip(tmp_path):
    o = caps(tmp_path)
    o.call("parse", _Raise("boom"), "p", 5, "a")
    assert o.call("parse", MockClient("x"), "p", 5, "b")[1] == "ok"


def test_caps_concurrency_one_busy_falls_back(tmp_path, monkeypatch):
    monkeypatch.setitem(CP.PARSE, "per_hour", 99)
    o = caps(tmp_path)
    slow = MockClient("x", delay_s=0.5)
    res = []
    t = threading.Thread(target=lambda: res.append(o.call("parse", slow, "p", 5, "a")))
    t.start()
    time.sleep(0.1)
    assert o.call("parse", MockClient("y"), "p", 5, "b") == (None, "busy")
    t.join()
    assert res[0][1] == "ok"


def test_caps_unreadable_state_fails_closed(tmp_path):
    o = caps(tmp_path)
    (tmp_path / "caps_parse.json").write_text("{bad")
    assert o.call("parse", MockClient("x"), "p", 5, "a") == (None, "state_unreadable")
    (tmp_path / "caps_breaker.json").write_text("{bad")
    assert o.call("editor", MockClient("x"), "p", 5) == (None, "breaker_open")


def test_caps_async_parse_rule_first_then_llm_then_8s_fallback(tmp_path, monkeypatch):
    from llm import parse_assist as PA
    o = caps(tmp_path)
    rule = lambda t: 9000 if t[0].isdigit() else None                   # noqa: E731
    assert asyncio.run(o.aoffer("9000", rule, MockClient('{"offer": 1}'), PA.build_prompt, PA.parse_reply,
                                "d", 15000)) == (9000, "rule")
    assert asyncio.run(o.aoffer("nine thousand", rule, MockClient('{"offer": 9000}'), PA.build_prompt,
                                PA.parse_reply, "d2", 15000)) == (9000, "llm")
    assert asyncio.run(o.aoffer("x" * 301, rule, MockClient('{"offer": 9}'), PA.build_prompt, PA.parse_reply,
                                "d3", 15000)) == (None, "rule_none")
    monkeypatch.setitem(CP.PARSE, "timeout_s", 0.1)
    t0 = time.monotonic()
    v = asyncio.run(o.aoffer("eight", rule, MockClient('{"offer": 8}', delay_s=2), PA.build_prompt,
                             PA.parse_reply, "d4", 15000))
    assert v[0] is None and v[1].startswith("fallback") and time.monotonic() - t0 < 1.5
    off = CP.OpsCaps(tmp_path / "off", config={"enabled": False}) if (tmp_path / "off").mkdir() is None else None
    assert asyncio.run(off.aoffer("eight", rule, MockClient('{"offer": 8}'), PA.build_prompt, PA.parse_reply,
                                  "d5", 15000)) == (None, "rule_none")


def test_caps_not_wired_into_deploy_or_hooks():
    import subprocess as sp
    r = sp.run(["grep", "-rl", "caps_v4", str(HERE / "deploy_r3"), "/workspace/course/r3/hooks",
                "/workspace/course/r3_deploy"], capture_output=True, text=True)
    assert r.stdout.strip() == ""
