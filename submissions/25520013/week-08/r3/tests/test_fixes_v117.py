"""v1.17 fixes after QA review_r3_v117 (PASS WITH CONDITIONS): M10-1 locked budget / hard counter under 40 threads
and 12 processes, M10-2 allow-list for the adopt path (realpath), M10-3 parse / lesson exceptions -> rejected,
M10-4 codex command (--ephemeral, --sandbox read-only, -C empty cwd, tool-off flags). Mock / fake CLI only."""
import json
import multiprocessing as mp
import os
import threading
from pathlib import Path

import pytest

from llm import codex_client as C
from llm import editor as E
from llm.budget import Budget
from llm.client import MockClient, json_reply
from tests import helpers as H
from tests.test_editor import budget, cycle, good, live_bytes, sb   # noqa: F401

NT, NP = 40, 12


def _go_threads(fn, n=NT):
    start, out, errs = threading.Barrier(n), [], []

    def w(i):
        start.wait()
        try:
            out.append(fn(i))
        except Exception as e:          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    return out, errs


def _no_tmp_left(d):
    return [p.name for p in Path(d).iterdir() if p.name.endswith(".tmp")]


# ---------------- M10-1 Budget
@pytest.mark.parametrize("rep", range(3))
def test_budget_40_threads_exact_cap(tmp_path, rep):
    b = Budget(tmp_path / "b.json", per_hour=6, per_day=100, tokens_per_day=10**9)
    out, errs = _go_threads(lambda i: b.reserve(1000.0, 10)[0])
    assert errs == [] and sum(out) == 6 and len(out) == NT
    assert len(json.loads((tmp_path / "b.json").read_text())["rows"]) == 6
    assert _no_tmp_left(tmp_path) == []


def _proc_reserve(path, start, q):
    start.wait()
    b = Budget(path, per_hour=6, per_day=100, tokens_per_day=10**9)
    q.put(b.reserve(1000.0, 10)[0])


def _proc_take(path, start, q, roles):
    start.wait()
    hc = C.HardCounter(path)
    got = []
    for r in roles:
        try:
            hc.take(r)
            got.append(r)
        except C.CallCapReached:
            pass
    q.put(got)


def _run_procs(target, args_for):
    ctx = mp.get_context("spawn")
    start, q = ctx.Barrier(NP), ctx.Queue()
    ps = [ctx.Process(target=target, args=(args_for(i)[0], start, q, *args_for(i)[1:])) for i in range(NP)]
    [p.start() for p in ps]
    res = [q.get(timeout=60) for _ in ps]
    [p.join(60) for p in ps]
    assert all(p.exitcode == 0 for p in ps)
    return res


@pytest.mark.parametrize("rep", range(3))
def test_budget_12_processes_exact_cap(tmp_path, rep):
    p = str(tmp_path / "b.json")
    res = _run_procs(_proc_reserve, lambda i: (p,))
    assert sum(res) == 6
    assert len(json.loads(Path(p).read_text())["rows"]) == 6 and _no_tmp_left(tmp_path) == []


def test_budget_tmp_name_has_pid_thread_uuid(tmp_path, monkeypatch):
    seen = []
    real = os.replace

    def spy(a, b):
        seen.append(Path(a).name)
        return real(a, b)
    monkeypatch.setattr(os, "replace", spy)
    Budget(tmp_path / "b.json", 6, 100, 10**9).reserve(1.0, 1)
    import re
    m = re.fullmatch(r"\.b\.json\.(\d+)\.(\d+)\.([0-9a-f]{32})\.tmp", seen[-1])
    assert m and m.group(1) == str(os.getpid()) and m.group(2) == str(threading.get_ident()), seen


# ---------------- M10-1 HardCounter
@pytest.mark.parametrize("rep", range(3))
def test_counter_40_threads_parse_exact(tmp_path, rep):
    hc = C.HardCounter(tmp_path / "c.json")
    out, errs = _go_threads(lambda i: hc.take("parse"))
    assert len(out) == C.PARSE_CAP + C.RESERVE_CAP == 14
    assert all(isinstance(e, C.CallCapReached) for e in errs) and len(errs) == NT - 14
    assert hc.read() == {"editor": 0, "parse": 10, "reserve": 4} and hc.total() == 14
    assert _no_tmp_left(tmp_path) == []


@pytest.mark.parametrize("rep", range(3))
def test_counter_40_threads_mixed_total_20(tmp_path, rep):
    hc = C.HardCounter(tmp_path / "c.json")
    out, errs = _go_threads(lambda i: hc.take("editor" if i % 2 else "parse"))
    assert len(out) == C.TOTAL_CAP == 20 and hc.total() == 20
    d = hc.read()
    assert d["editor"] <= 6 and d["parse"] <= 10 and d["reserve"] <= 4


@pytest.mark.parametrize("rep", range(3))
def test_counter_12_processes_total_20(tmp_path, rep):
    p = str(tmp_path / "c.json")
    res = _run_procs(_proc_take, lambda i: (p, ("parse", "editor", "parse")))
    taken = [r for got in res for r in got]
    hc = C.HardCounter(p)
    assert len(taken) == 20 == hc.total()                       # 36 attempts, exactly 20 granted and recorded
    d = hc.read()
    assert d["editor"] + d["parse"] + d["reserve"] == 20 and d["editor"] <= 6 and d["parse"] <= 10
    assert _no_tmp_left(tmp_path) == []


# ---------------- M10-2 allow-list
def test_default_allow_list_is_the_one_sandbox_copy():
    assert E.ALLOWED_LIVE == (E.SANDBOX_LIVE,)
    assert E.SANDBOX_LIVE == Path(__file__).resolve().parents[1] / "sandbox" / "live_policy.json"
    assert E.SANDBOX_LIVE.is_file()
    assert E._live_ok(E.SANDBOX_LIVE) and E._live_ok(str(E.SANDBOX_LIVE))
    assert E._live_ok(E.SANDBOX_LIVE.parent / "." / "live_policy.json")


@pytest.mark.parametrize("p", ["/workspace/course/r3/policy.json", "/workspace/course/r3_deploy/v1.16.1/policy.json",
                               "/home/box/labpc/r3/policy.json", "/tmp/policy.json", "policy.json",
                               "/workspace/course/r3_v117_llm/sandbox/other.json",
                               "/workspace/course/r3_v117_llm/llm_config.json"])
def test_default_allow_list_refuses_everything_else(sb, tmp_path, p):
    assert not E._live_ok(p)
    s2 = dict(sb, live=Path(p))
    with pytest.raises(E.EditorConfigError):
        cycle(s2, MockClient(json_reply(good())), tmp_path, allowed_live=None)       # None = default list


def test_tmp_copy_refused_by_default_even_though_it_exists(sb, tmp_path):
    c = MockClient(json_reply(good()))
    before = live_bytes(sb)
    with pytest.raises(E.EditorConfigError):
        cycle(sb, c, tmp_path, allowed_live=None)
    assert c.prompts == [] and live_bytes(sb) == before and sb["runs"].latest() == 1


def test_symlink_to_live_or_repo_refused(sb, tmp_path):
    link = tmp_path / "link.json"
    link.symlink_to(sb["live"])
    assert E._live_ok(link, (sb["live"],))                             # realpath equal -> same file, allowed
    repo = tmp_path / "repo_link.json"
    repo.symlink_to("/workspace/course/r3/policy.json")
    assert not E._live_ok(repo, (repo,))                               # listed, but realpath is in the r3 tree
    assert not E._live_ok("/workspace/course/r3/policy.json", ("/workspace/course/r3/policy.json",))
    other = tmp_path / "other.json"
    other.write_text("{}")
    assert not E._live_ok(other, (sb["live"],))


@H.needs_proc_fd
def test_listed_tmp_copy_still_adopts(sb, tmp_path):
    r = cycle(sb, MockClient(json_reply(good())), tmp_path)            # allowed_live=(sb live,)
    assert r["status"] == "adopted"


def test_post_adopt_check_uses_allow_list(sb, tmp_path):
    with pytest.raises(E.EditorConfigError):
        E.post_adopt_check(sb["runs"], 1, sb["live"], sb["events"], H.T0 + 2000, 5_000_000)
    with pytest.raises(E.EditorConfigError):
        E.post_adopt_check(sb["runs"], 1, "/workspace/course/r3/policy.json", sb["events"], H.T0 + 2000,
                           5_000_000, allowed_live=("/workspace/course/r3/policy.json",))


# ---------------- M10-3
@H.needs_proc_fd
def test_huge_int_is_rejected_with_audit_row(sb, tmp_path):
    p = good()
    p["edits"][0]["value"] = "__BIG__"
    out = json.dumps(p).replace('"__BIG__"', "9" * 400)
    before = live_bytes(sb)
    r = cycle(sb, MockClient(out), tmp_path)
    assert r["status"] == "rejected_schema" and live_bytes(sb) == before
    assert json.loads(Path(sb["audit"]).read_text().splitlines()[-1])["status"] == "rejected_schema"


@H.needs_proc_fd
def test_broken_lesson_change_is_rejected_not_raised(sb, tmp_path):
    bad = [dict(x) for x in H.LESSONS]
    for x in bad:
        for k, v in list(x.items()):
            if isinstance(v, str) and "->" in v:
                x[k] = v.split(":")[0] + ": 1.2.3 -> 1..2" if ":" in v else "x.price_mult: 1.2.3 -> 1..2"
    r = cycle(sb, MockClient(json_reply(good())), tmp_path, lessons=bad)
    assert r["status"] in ("rejected_lesson", "adopted")                # never an exception
    assert json.loads(Path(sb["audit"]).read_text().splitlines()[-1])["status"] == r["status"]


# ---------------- M10-4 codex command
def test_codex_cmd_fixed_flags(tmp_path):
    cfg = C.load_config()
    cmd = C.build_cmd("/x/codex", cfg, tmp_path)
    assert cmd[:2] == ["/x/codex", "exec"] and cmd[-1] == "-"
    for f in ("--ephemeral", "--skip-git-repo-check", "--ignore-user-config"):
        assert f in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only" and cmd.count("--sandbox") == 1
    assert cmd[cmd.index("-C") + 1] == str(tmp_path)
    off = {cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use", "view_image"} <= off
    assert "shell_environment_policy.inherit=none" in cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-6.1-terra" and cfg["real_calls_enabled"] is False


@pytest.mark.parametrize("bad", [["--sandbox", "danger-full-access"], ["--dangerously-bypass-approvals-and-sandbox"],
                                 ["-C", "/"], ["--add-dir", "/"], ["-s", "workspace-write"]])
def test_codex_extra_args_cannot_loosen(tmp_path, bad):
    with pytest.raises(ValueError):
        C.build_cmd("/x/codex", dict(C.load_config(), extra_args=bad), tmp_path)


def test_fake_cli_sees_empty_cwd_and_flags(tmp_path):
    from tests.test_codex_client import cfg, fake_cli
    exe = fake_cli(tmp_path, f'cat > /dev/null; pwd > {tmp_path}/cwd.txt; ls -A > {tmp_path}/ls.txt; '
                             f'echo "$@" > {tmp_path}/args.txt; printf ok')
    cl = C.CodexClient("parse", config_path=cfg(tmp_path, real_calls_enabled=True, cli=exe), base=tmp_path)
    assert cl.complete("p", max_tokens=5, timeout_s=10) == "ok"
    args = (tmp_path / "args.txt").read_text().split()
    cwd = (tmp_path / "cwd.txt").read_text().strip()
    assert (tmp_path / "ls.txt").read_text() == "" and os.path.realpath(args[args.index("-C") + 1]) == os.path.realpath(cwd)
    assert "--ephemeral" in args and args[args.index("--sandbox") + 1] == "read-only"
