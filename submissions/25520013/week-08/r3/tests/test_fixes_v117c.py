"""v1.17c: CR review_r3_v117_fixes2.md (5c3bc238) R1-R4 for the re-measurement driver, EL 18:21 scope.
R1 stdin integrity loader (cut / altered input -> 0 calls), R2 global ledger (cap 20 for the whole 18:05 approval,
never reset by prepare, flock, single run), R3 calls.jsonl fields, R4 model_reasoning_effort. Fake CLI only:
no network, no real codex, no labpc. Every test uses a tmp ledger (the real ledger is never written by tests)."""
import base64
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "labpc"))
import realrun_driver as R                    # noqa: E402
import realrun_ledger as LG                   # noqa: E402

REAL_LEDGER = HERE / "realrun_ledger.jsonl"

FAKE_CODEX = r'''#!/bin/bash
# fake codex for the box rehearsal (no model). Counts every exec call in $FAKE_LOG.
case "$1" in
  --version) echo "codex-cli 0.164.0-fake"; exit 0;;
  features) [ "${FAKE_NO_FEATURE:-}" = 1 ] && { echo "shell_tool stable true"; exit 0; }
            for f in shell_tool unified_exec shell_snapshot apps plugins remote_plugin browser_use \
                     browser_use_external in_app_browser computer_use view_image skill_search; do
              echo "$f stable true"; done; exit 0;;
  exec) if [ "${2:-}" = --help ]; then echo "--ephemeral --skip-git-repo-check --ignore-user-config --sandbox --disable"; exit 0; fi;;
esac
echo "$*" >> "${FAKE_LOG:-/dev/null}"
echo "$@" | grep -q -- "--sandbox read-only" || exit 90
echo "$@" | grep -q -- "-m gpt-6.1-terra" || exit 91
echo "$@" | grep -q -- "-c model_reasoning_effort=medium" || exit 92
echo "$@" | grep -q -- "--disable shell_tool" || exit 93
p=$(cat)
echo "stderr noise sk-THISISNOTAREALKEY000000" >&2
case "$p" in
  *run=99*|*"<<<MSG
nine thousand won please"*) sleep 3;;
esac
case "$p" in
  *SYSTEM:*) echo '{"offer": 1}';;
  *"new instruction"*) echo "I can't follow instructions inside the data.";;
  *"twelve thousand"*) echo '{"offer": 99000}';;
  *"<<<MSG"*) echo '{"offer": 9000}';;
  *run=1*) echo 'here sk-ABCDEFGHIJKLMNOPQRSTUV is a leak {"edits": []}';;
  *) echo '{"edits": [], "rationale": "fake: no clear signal"}';;
esac
'''


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path, monkeypatch):
    before = REAL_LEDGER.read_bytes() if REAL_LEDGER.exists() else None
    monkeypatch.setattr(LG, "LEDGER", tmp_path / "ledger.jsonl")
    yield LG.Ledger()
    after = REAL_LEDGER.read_bytes() if REAL_LEDGER.exists() else None
    assert before == after, "a test touched the real ledger"


@pytest.fixture
def fakebin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "codex").write_text(FAKE_CODEX)
    (b / "codex").chmod(0o755)
    return b


def _run(script, fakebin, tmp_path, extra_env=None, via_pipe=True):
    log = tmp_path / "fake_calls.log"
    if log.exists():
        log.unlink()
    env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", FAKE_LOG=str(log), **(extra_env or {}))
    if via_pipe:                                  # a real pipe like ssh -> wsl (not a seekable file)
        p = subprocess.run(["bash", "-c", "cat | bash -s"], input=script, capture_output=True, text=True,
                           env=env, timeout=200, cwd=str(tmp_path))
    else:
        p = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, env=env, timeout=200,
                           cwd=str(tmp_path))
    n = len(log.read_text().splitlines()) if log.exists() else 0
    return p, n


def _sent_run(d, fakebin, tmp_path, script=None, env=None):
    R.mark_sent(d)
    p, n = _run(script if script is not None else (d / "batch.sh").read_text(), fakebin, tmp_path, env)
    (d / "raw_stdout.txt").write_text(p.stdout)
    return p, n


# ---------------- R1: input integrity
def test_r1_full_batch_runs_all_calls_and_start_sha_matches(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="a")
    meta = json.loads((d / "prepare_meta.json").read_text())
    p, n = _sent_run(d, fakebin, tmp_path)
    assert n == meta["n"] == 10 and p.stdout.count("@@R3RR CALL ") == 10
    assert f"stdin_sha={meta['payload_sha256']} expected_sha={meta['payload_sha256']} eof=1" in p.stdout
    assert "INTEGRITY_FAIL" not in p.stdout and p.stdout.rstrip().endswith(f"END run={d.name} nonce={meta['nonce']}")


def test_r1_first_line_is_main_wrapper_and_nothing_else_is_shell(tmp_path):
    d = R.prepare(tmp_path, ts="b")
    lines = (d / "batch.sh").read_text().splitlines()
    assert lines[0].startswith("main(){ ") and lines[0].endswith("}; main; exit $?")
    import re
    assert all(re.fullmatch(r"[A-Za-z0-9+/=]{1,76}", x) for x in lines[1:-1])     # payload = base64 only
    assert lines[-1].startswith("@@R3RR_EOF ")


@pytest.mark.parametrize("frac", [0.0005, 0.002, 0.01, 0.2, 0.5, 0.8, 0.95, 0.999])
def test_r1_truncated_stdin_makes_zero_calls(tmp_path, fakebin, frac):
    d = R.prepare(tmp_path, ts="c")
    s = (d / "batch.sh").read_text()
    p, n = _run(s[:max(1, int(len(s) * frac))], fakebin, tmp_path)
    assert n == 0 and "@@R3RR CALL" not in p.stdout and "@@R3RR BEGIN" not in p.stdout


def test_r1_truncated_every_line_boundary_makes_zero_calls(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="c2", spec={"editor": 1, "parse": 1, "short_timeout": 0, "injected": 0})
    s = (d / "batch.sh").read_text()
    cuts = [i for i, ch in enumerate(s) if ch == "\n"][:-1]          # cut after line k (k < last)
    for c in cuts[::7] + [cuts[-1], cuts[0]]:
        p, n = _run(s[:c + 1], fakebin, tmp_path)
        assert n == 0 and "@@R3RR CALL" not in p.stdout, c


def test_r1_cut_before_end_line_and_cut_inside_end_line(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="c3")
    s = (d / "batch.sh").read_text()
    for cut in (s[:s.rindex("@@R3RR_EOF")], s[:-5], s[:-1]):
        p, n = _run(cut, fakebin, tmp_path)
        assert n == 0 and ("INTEGRITY_FAIL calls=0" in p.stdout or p.stdout == "")


@pytest.mark.parametrize("how", ["flip", "drop_line", "dup_line", "swap_payload"])
def test_r1_altered_stdin_is_integrity_fail_zero_calls(tmp_path, fakebin, how):
    d = R.prepare(tmp_path, ts="d")
    L = (d / "batch.sh").read_text().splitlines(keepends=True)
    k = len(L) // 2
    if how == "flip":
        L[k] = ("A" if L[k][0] != "A" else "B") + L[k][1:]
    elif how == "drop_line":
        del L[k]
    elif how == "dup_line":
        L.insert(k, L[k])
    else:                                                  # payload replaced by a different valid script
        evil = base64.encodebytes(b'inner(){ codex exec --dangerously-bypass-approvals-and-sandbox -; }; inner\n')
        L = [L[0]] + evil.decode().splitlines(keepends=True) + [L[-1]]
    p, n = _run("".join(L), fakebin, tmp_path)
    assert n == 0 and "INTEGRITY_FAIL calls=0" in p.stdout and "@@R3RR CALL" not in p.stdout


def test_r1_trailing_text_after_end_line_is_never_run(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="e")
    s = (d / "batch.sh").read_text() + 'echo "@@R3RR INJECTED_AFTER_EOF"\n'
    p, n = _run(s, fakebin, tmp_path)
    assert "INJECTED_AFTER_EOF" not in p.stdout and n == 10


def test_r1_also_holds_without_extra_pipe(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="f", spec={"editor": 1, "parse": 1, "short_timeout": 0, "injected": 0})
    s = (d / "batch.sh").read_text()
    p, n = _run(s, fakebin, tmp_path, via_pipe=False)
    assert n == 2
    p, n = _run(s[: len(s) // 2], fakebin, tmp_path, via_pipe=False)
    assert n == 0


def test_r1_ingest_of_integrity_fail_is_zero_used_and_released(tmp_path, fakebin, tmp_ledger):
    d = R.prepare(tmp_path, ts="g")
    s = (d / "batch.sh").read_text()
    p, n = _sent_run(d, fakebin, tmp_path, script=s[: len(s) * 3 // 4])
    assert n == 0
    out = R.ingest(d)
    assert out["ledger_used"] == 0 and out["integrity_ok"] is False and out["calls_returned"] == 0
    assert tmp_ledger.summary()["remaining"] == 20


# ---------------- R2: global ledger
def test_r2_cap_is_shared_across_rounds_and_prepare_never_resets(tmp_path, tmp_ledger):
    d1 = R.prepare(tmp_path, ts="h1")                              # 10 reserved (re-measurement)
    with pytest.raises(LG.LedgerError, match="still open"):
        R.prepare(tmp_path, ts="h2")                               # round 1 open
    assert not (tmp_path / "realrun_h2").exists()
    R.mark_sent(d1)
    (d1 / "raw_stdout.txt").write_text("")                          # nothing came back: counted as used
    assert R.ingest(d1)["ledger_used"] == 10
    sm = tmp_ledger.summary()
    assert sm["committed"] == 10 and sm["remaining"] == 10
    with pytest.raises(LG.LedgerError, match="cap 20"):
        R.prepare(tmp_path, ts="h3", spec={"editor": 4, "parse": 4, "short_timeout": 1, "injected": 2})  # 11
    nonce = tmp_ledger.reserve("proposer_round", 10)               # the next proposer round takes the rest
    assert nonce and tmp_ledger.summary()["remaining"] == 0
    tmp_ledger.mark_sent("proposer_round")
    tmp_ledger.close("proposer_round", False, "test")
    with pytest.raises(LG.LedgerError, match="cap 20"):
        tmp_ledger.reserve("one_more", 1)                          # the 21st call
    rows = [json.loads(x) for x in tmp_ledger.path.read_text().splitlines()]
    assert rows[0]["event"] == "init" and "18:05" in rows[0]["approval_id"] and rows[0]["cap"] == 20
    assert all("18:05" in r["approval_id"] for r in rows if r["event"] == "reserve")


def test_r2_concurrent_reserve_never_exceeds_cap(tmp_path):
    L = LG.Ledger(tmp_path / "c.jsonl")
    ok, err = [], []

    def go(i):
        try:
            L.reserve(f"r{i}", 7)
            ok.append(i)
            L.mark_sent(f"r{i}")
            L.close(f"r{i}", False, "t")
        except LG.LedgerError as e:
            err.append(str(e))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert L.summary()["committed"] <= 20 and len(ok) <= 2


def test_r2_concurrent_reserve_across_processes(tmp_path):
    lp = tmp_path / "p.jsonl"
    code = ("import sys; sys.path[:0]=[%r,%r]; import realrun_ledger as LG\n"
            "L=LG.Ledger(%r)\n"
            "try:\n  L.reserve(sys.argv[1], 6); L.mark_sent(sys.argv[1]); L.close(sys.argv[1], False, 't')\n"
            "except LG.LedgerError: pass\n") % (str(HERE), str(HERE / "labpc"), str(lp))
    ps = [subprocess.Popen([sys.executable, "-c", code, f"p{i}"]) for i in range(8)]
    [p.wait() for p in ps]
    assert LG.Ledger(lp).summary()["committed"] <= 20


def test_r2_single_run_mark_and_single_ingest(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="i", spec={"editor": 1, "parse": 1, "short_timeout": 0, "injected": 0})
    with pytest.raises(SystemExit):
        (d / "raw_stdout.txt").write_text("")
        R.ingest(d)                                                 # not marked sent
    (d / "raw_stdout.txt").unlink()
    _sent_run(d, fakebin, tmp_path)
    with pytest.raises(LG.LedgerError, match="already marked sent"):
        R.mark_sent(d)
    R.ingest(d)
    with pytest.raises(SystemExit):
        R.ingest(d)                                                 # closed


def test_r2_two_runs_in_one_output_are_refused(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="j", spec={"editor": 1, "parse": 1, "short_timeout": 0, "injected": 0})
    p, _ = _sent_run(d, fakebin, tmp_path)
    (d / "raw_stdout.txt").write_text(p.stdout + p.stdout)
    with pytest.raises(SystemExit, match="more than once"):
        R.ingest(d)


def test_r2_output_of_another_round_is_refused(tmp_path, fakebin):
    d1 = R.prepare(tmp_path, ts="k1", spec={"editor": 1, "parse": 0, "short_timeout": 0, "injected": 0})
    R.abandon(d1, "test")
    d2 = R.prepare(tmp_path, ts="k2", spec={"editor": 1, "parse": 0, "short_timeout": 0, "injected": 0})
    p, _ = _run((d1 / "batch.sh").read_text(), fakebin, tmp_path)
    R.mark_sent(d2)
    (d2 / "raw_stdout.txt").write_text(p.stdout)
    with pytest.raises(SystemExit, match="does not match"):
        R.ingest(d2)


def test_r2_abandon_only_before_send_and_void_row(tmp_path, tmp_ledger):
    d = R.prepare(tmp_path, ts="l", spec={"editor": 1, "parse": 0, "short_timeout": 0, "injected": 0})
    R.mark_sent(d)
    with pytest.raises(LG.LedgerError, match="marked sent"):
        R.abandon(d, "x")
    tmp_ledger.void("realrun_20261103T181106", "CR REVISE")
    with pytest.raises(LG.LedgerError):
        tmp_ledger.void("realrun_20261103T181106", "again")
    with pytest.raises(LG.LedgerError):
        tmp_ledger.reserve("realrun_20261103T181106", 1)          # a voided round is never reused


def test_r2_unreadable_ledger_fails_closed(tmp_path, tmp_ledger):
    tmp_ledger.path.write_text("{not json\n")
    with pytest.raises(LG.LedgerError):
        R.prepare(tmp_path, ts="m")
    assert not (tmp_path / "realrun_m").exists()


# ---------------- R3 / R4
def test_r3_r4_calls_rows_and_summary(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="n")
    _sent_run(d, fakebin, tmp_path)
    s = R.ingest(d, mode="rehearsal")
    raw = (d / "raw_stdout.txt").read_text().splitlines()
    rows = [json.loads(x) for x in (d / "calls.jsonl").read_text().splitlines()]
    assert len(rows) == 10
    for r in rows:
        assert r["t0_ns"] < r["t1_ns"] and r["mode"] == "rehearsal" and r["model_requested"] == "gpt-6.1-terra"
        assert r["model_responded"] == "[미확인]" and r["codex_version"] == "codex-cli 0.164.0-fake"
        assert r["reasoning_effort"] == "medium" and r["sha_match"]
        ln = raw[r["raw_line"] - 1]
        assert ln.startswith(f"@@R3RR CALL i={r['i']} ") and R.sha(ln.split("out_b64=", 1)[1]) == r["out_b64_sha256"]
    assert all(r["timed_out"] and r["exit"] == 124 for r in rows if r["kind"] == "short_timeout")
    assert "THISISNOTAREALKEY" not in (d / "calls.jsonl").read_text()
    assert s["reasoning_effort"] == "medium" and "-C /" in s["settings_diff_vs_client"] and s["ledger_used"] == 10
    assert subprocess.run(["sha256sum", "-c", "SHA256SUMS_ingest"], cwd=d, capture_output=True).returncode == 0


def test_r3_default_mode_is_real(tmp_path, fakebin):
    d = R.prepare(tmp_path, ts="o", spec={"editor": 0, "parse": 1, "short_timeout": 0, "injected": 0})
    _sent_run(d, fakebin, tmp_path)
    R.ingest(d)
    assert json.loads((d / "calls.jsonl").read_text().splitlines()[0])["mode"] == "real"


def test_r4_effort_comes_from_llm_config_and_is_on_every_call(tmp_path):
    d = R.prepare(tmp_path, ts="p")
    import base64 as b
    lines = (d / "batch.sh").read_text().splitlines()
    inner = b.b64decode("".join(lines[1:-1])).decode()
    calls = [ln for ln in inner.splitlines() if ln.startswith("run1 ")]
    assert len(calls) == 10
    for ln in calls:
        assert " -m gpt-6.1-terra -c model_reasoning_effort=medium -" in ln and " timeout -k 5 " in ln
        assert "--sandbox read-only" in ln and "--disable shell_tool" in ln and " -C / " in ln
    assert R.settings() == ("gpt-6.1-terra", "medium")


def test_r4_batch_writes_nothing_and_runs_nothing_else(tmp_path):
    d = R.prepare(tmp_path, ts="q")
    lines = (d / "batch.sh").read_text().splitlines()
    inner = base64.b64decode("".join(lines[1:-1])).decode()
    import re
    for body in (lines[0], inner):
        body = body.replace("2>/dev/null", "").replace("</dev/null", "").replace(">/dev/null", "")
        for bad in (r"\brm\b", r"\bmktemp\b", r"\btee\b", r"[^0-9]>\s*/(?!dev/null)", r">>", r"\bpkill\b|\bkill\b",
                    r"policy\.json", r"market_kit", r"\btmux\b", r"\bschtasks\b", r"\bcurl\b", r"\bwget\b"):
            assert not re.search(bad, body), bad
