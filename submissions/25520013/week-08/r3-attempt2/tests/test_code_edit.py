"""Operator edits of the rule code (kernel/code_edit.py): tools, gates, staged tests, apply, seller restart flow,
canary and evaluation, state card, audit and operator wiring. Tiny fake rule code in a tmp root, fake test runner and
fake restarter unless said otherwise. Fixed clock values."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
import types

import pytest
from kernel import audit
from kernel import auditor as au
from kernel import code_edit as ce
from kernel import common as c
from kernel import experiments as ex
from kernel import operator as op
from kernel.trajectory import Trajectory

NOW = 1_794_000_000.0
H = 3600

POLICY_PY = """import json


def load(path=None):
    pol = json.load(open(path or "policy.json"))
    validate(pol)
    return pol


def validate(pol):
    if not isinstance(pol, dict):
        raise ValueError("policy must be an object")
    return True


def floor_price(unit, pol):
    return max(unit["cost"] + 1, int(unit["cost"] * 1.1))


def guard_price(unit, price):
    if price <= unit["cost"]:
        raise ValueError("price not above cost")
    return price


def restock_plan(inv, pol):
    qty = 2
    return [{"sku": "A", "qty": qty}]
"""
RESTOCK_PY = """import policy as P


def main():
    return P.restock_plan({}, {})


def helper():
    return 1
"""
BUYER_PY = """def over_cap(price, cap):
    return price > cap


def rank(cands):
    return sorted(cands)
"""
SELLER_PY = """import policy as P

ENV_NAME = "shop"


def sign(receipt):
    return dict(receipt, sig="x")


class Seller:
    def decide(self, unit, price):
        return P.guard_price(unit, price)

    def opening(self):
        return "hello"
"""
TEST_X = """import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy as P


def test_floor():
    assert P.floor_price({"cost": 100}, {}) >= 101


def test_plan():
    assert P.restock_plan({}, {})[0]["qty"] >= 1
"""


class Runner:
    """Fake test runner: the baseline (dir .../base) and the edited copy (.../new) answer as set."""

    def __init__(self, base=None, new=None):
        self.base = base or {
            "ok": True,
            "failed": [],
            "summary": "2 passed",
            "error": None,
        }
        self.new = new or {
            "ok": True,
            "failed": [],
            "summary": "2 passed",
            "error": None,
        }
        self.calls = []

    def __call__(self, stage_dir, timeout_s):
        self.calls.append(os.path.basename(stage_dir))
        assert timeout_s == ce.TEST_TIMEOUT_S
        return dict(self.base if stage_dir.endswith("base") else self.new)


class Handle:
    def __init__(self, rc=None):
        self.pid, self.rc = 4242, rc

    def poll(self):
        return self.rc


class Restarter:
    """Fake restart launcher: records calls and returns at once."""

    def __init__(self, rc=None, fail=False):
        self.calls, self.rc, self.fail = [], rc, fail

    def __call__(self, root, change_id, log_path):
        self.calls.append(change_id)
        if self.fail:
            raise OSError("no such command")
        return Handle(self.rc)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _rule_code(root):
    _write(os.path.join(root, "policy.py"), POLICY_PY)
    _write(
        os.path.join(root, "policy.json"),
        json.dumps({"owner": {"github": "secretlogin"}}),
    )
    _write(os.path.join(root, "hooks/restock.py"), RESTOCK_PY)
    _write(os.path.join(root, "hooks/market_buyer.py"), BUYER_PY)
    _write(os.path.join(root, "hooks/seller.py"), SELLER_PY)
    _write(os.path.join(root, "hooks/common.py"), "X = 1\n")
    _write(os.path.join(root, "tests/test_x.py"), TEST_X)
    os.makedirs(os.path.join(root, "a2"), exist_ok=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "root")
    _rule_code(root)
    monkeypatch.delenv(ce.RESTART_ENV, raising=False)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    return types.SimpleNamespace(root=root, traj=traj)


def _rows(root, kind=None):
    return [
        r
        for r in c.tail_json(os.path.join(root, "a2/trajectory.jsonl"), 10**7)
        if kind is None or r.get("kind") == kind
    ]


def _metrics(root, rows):
    with open(
        os.path.join(root, ".r3/metrics.jsonl")
        if os.path.isdir(os.path.join(root, ".r3"))
        else _mk(os.path.join(root, ".r3/metrics.jsonl")),
        "a",
        encoding="utf-8",
    ) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _mk(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


RS = "hooks/restock.py"
EDIT = {
    "file": RS,
    "old": "def helper():\n    return 1",
    "new": "def helper():\n    return 2",
}
REASON = "재입고가 예산 막힘으로 3회 멈춰서 helper 값을 바꿈"
EXPECT = "산 실행 수가 1시간에 2회에서 3회로 늘어남"


def _p(env, edits=None, read=(RS,), runner=None, now=NOW, reason=REASON, expect=EXPECT):
    return ce.propose(
        env.root,
        [EDIT] if edits is None else edits,
        reason,
        expect,
        now,
        env.traj,
        read_files=read,
        runner=runner or Runner(),
    )


def _text(root, rel):
    return open(os.path.join(root, rel), encoding="utf-8").read()


# ------------------------------------------------------------ tools
def test_read_and_search_refuse_paths_outside_the_rule_code(env, tmp_path):
    outside = tmp_path / "secret.py"
    outside.write_text("TOKEN = 1\n")
    os.symlink(str(outside), os.path.join(env.root, "hooks/evil.py"))
    assert "hooks/evil.py" not in ce.readable(
        env.root
    ) and "hooks/seller.py" in ce.readable(env.root)
    for bad in (
        "../secret.py",
        str(outside),
        "hooks/../policy.py",
        "hooks/evil.py",
        "tests/test_x.py",
        "policy.json",
        None,
        3,
    ):
        assert ce.read(env.root, bad).startswith("인자 오류"), bad
        if bad is not None:
            assert ce.search(env.root, "TOKEN", file=bad).startswith("인자 오류"), bad
    assert ce.search(env.root, "TOKEN").endswith(": 0줄")
    os.symlink(str(tmp_path), os.path.join(env.root, "linkdir"))
    assert ce._safe(env.root, "linkdir/x.py") is None


def test_read_numbers_lines_and_caps_at_200(env):
    _write(
        os.path.join(env.root, "hooks/big.py"),
        "".join(f"x{i} = {i}\n" for i in range(500)),
    )
    out = ce.read(env.root, "hooks/big.py", start=10, lines=900)
    body = out.splitlines()
    assert (
        body[0].startswith("hooks/big.py 전체 500줄, 10~209줄")
        and "(읽기 전용)" in body[0]
    )
    assert (
        body[1] == "   10| x9 = 9"
        and body[-1] == "[여기까지 온전히 보임. 이어 읽기: start=210]"
        and len(body) == 202
    )
    assert "(편집 가능)" in ce.read(env.root, "policy.py")


def test_search_regex_substring_fallback_and_file_filter(env):
    out = ce.search(env.root, r"def \w+_price")
    assert (
        "policy.py:16: def floor_price(unit, pol):" in out
        and "2줄" in out.splitlines()[0]
    )
    out = ce.search(
        env.root, "guard_price(", file="hooks/seller.py"
    )  # bad regex -> substring
    assert "(글자)" in out and "hooks/seller.py:" in out and "policy.py:" not in out
    assert ce.search(env.root, "").startswith("인자 오류")


def test_restock_log_counts_runs_buys_and_stops(env):
    def run(ts, why=None, **o):
        out = dict(
            {
                "lines": 2,
                "qty": 5,
                "budget": 1_000_000,
                "notes": ["n1", "n2", "n3", "n4"],
                "inventory_cost": 9_000_000,
                "balance": 50_000_000,
            },
            **o,
        )
        if why:
            out["why"] = why
        return {"ts": ts, "hook": "restock", "decision": "restock_plan", "outcome": out}

    _metrics(
        env.root,
        [
            run(NOW - 3 * H),
            run(NOW - 600),
            run(NOW - 500, "window_closed"),
            run(NOW - 400, "inventory_cap"),
            run(NOW - 300, "budget", warnings=["x"]),
            {"ts": NOW - 200, "hook": "seller", "decision": "restock_plan"},
        ],
    )
    ev = [
        {
            "ts": NOW - 590,
            "type": "restock",
            "mine": True,
            "data": {"sku": "S1", "category": "a>잎1", "name": "사과"},
        },
        {
            "ts": NOW - 590,
            "type": "restock",
            "mine": True,
            "data": {"sku": "S1", "category": "a>잎1", "name": "사과"},
        },
        {
            "ts": NOW - 590,
            "type": "restock",
            "mine": False,
            "data": {"sku": "S2", "category": "a>잎2"},
        },
    ]
    with open(os.path.join(env.root, ".r3/events.jsonl"), "w") as f:
        f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n")
    out = ce.restock_log(env.root, hours=2, now=NOW)
    assert (
        "4회" in out.splitlines()[0] and "구매함 1" in out and "window_closed 1" in out
    )
    assert "산 실행 1회" in out and "메모: n1 / n2 / n3" in out and "n4" not in out
    assert "잎1 2" in out and "S1 사과: 2개" in out and "S2" not in out
    assert (
        "inventory_cap)으로 멈춤 1회" in out
        and "예산(budget)으로 멈춤 1회" in out
        and "경고 1건" in out
    )
    assert ce.restock_log(env.root, hours="x", now=NOW).startswith("재입고")


def test_tools_are_registered_for_the_operator():
    from kernel import tools

    for name in ("code_search", "code_read", "restock_log"):
        assert "operator" in tools.TOOLS[name][1] and tools.TOOLS[name][0].__doc__
    assert "code_read" in op.TEXT_TOOLS and "code_search" in op.TEXT_TOOLS


# ------------------------------------------------------------ propose
def test_propose_happy_path(env):
    r = Runner()
    res = _p(env, runner=r)
    assert (
        res["ok"]
        and res["id"] == 1
        and res["files"] == [RS]
        and res["status"] == "watching"
    ), res
    assert "return 2" in _text(env.root, RS)
    assert "return 1" in _text(env.root, f"a2/code_backups/1/{RS}")
    assert r.calls == ["base", "new"]
    ch = ce.load(env.root)[0]
    assert (
        ch["status"] == "watching"
        and ch["watch_until"] == NOW + ce.WATCH_S
        and ch["eval_until"] == NOW + 4 * H
    )
    assert (
        ch["metrics"] == ["shop"]
        and ch["applied_ts"] == NOW
        and "+    return 2" in ch["diff"]
    )
    assert ch["sha_before"][RS] != ch["sha_after"][RS] and ch["changed_lines"] == 2
    row = _rows(env.root, "code_edit")[0]
    assert (
        row["id"] == 1
        and row["files"] == [RS]
        and row["at"] == NOW
        and "return 2" in row["diff"]
    )
    assert os.listdir(os.path.join(env.root, ce.STAGE)) == []
    assert json.load(open(os.path.join(env.root, ce.BASELINE)))["failed"] == []


def test_baseline_is_cached_and_baseline_failures_do_not_block(env):
    r = Runner(
        base={
            "ok": False,
            "failed": ["tests/test_x.py::test_plan"],
            "summary": "1 failed",
            "error": None,
        },
        new={
            "ok": False,
            "failed": ["tests/test_x.py::test_plan"],
            "summary": "1 failed",
            "error": None,
        },
    )
    res = _p(env, runner=r)
    assert res["ok"] and res["tests"]["baseline_failed"] == 1, res
    rows = ce.load(env.root)
    ce.revert(env.root, rows[0], NOW, env.traj, "test", "canary")  # back to the original text
    rows[0]["ts"] = NOW - 2 * H
    ce._save(env.root, rows)
    edit2 = {
        "file": RS,
        "old": "def helper():\n    return 1",
        "new": "def helper():\n    return 3",
    }
    r2 = Runner(base={"error": "must not run"}, new=r.new)
    assert _p(env, [edit2], runner=r2, now=NOW + 7 * H)["ok"] and r2.calls == [
        "new"
    ]  # same code: cached baseline


@pytest.mark.parametrize(
    "edits,read,cls,word",
    [
        (
            [{"file": "hooks/common.py", "old": "X = 1", "new": "X = 2"}],
            ("hooks/common.py",),
            "not_editable",
            "고칠 수 없는",
        ),
        (
            [{"file": "tests/test_x.py", "old": "a", "new": "b"}],
            ("tests/test_x.py",),
            "not_editable",
            "고칠 수 없는",
        ),
        ([EDIT], (), "not_read", "code_read로 읽지 않은"),
        (
            [{"file": RS, "old": "nothing like this", "new": "x"}],
            (RS,),
            "old",
            "파일에 없음",
        ),
        ([{"file": RS, "old": "return", "new": "return"}], (RS,), "old", "2번 나와"),
        ([{"file": RS, "old": "", "new": "x"}], (RS,), "old", "비어 있음"),
        ([{"file": RS, "old": "return 1", "new": "#" * 4001}], (RS,), "size", "4000자"),
        (
            [
                {
                    "file": RS,
                    "old": "return 1",
                    "new": "return 1\n" + "    y = 1\n" * 160,
                }
            ],
            (RS,),
            "size",
            "150줄",
        ),
        (
            [{"file": RS, "old": "return 1", "new": "return (1"}],
            (RS,),
            "syntax",
            "문법 오류",
        ),
        (
            [{"file": RS, "old": "return 1", "new": "return 1"}],
            (RS,),
            "noop",
            "바뀐 것이 없음",
        ),
        ([EDIT] * 4, (RS,), "shape", "1~3개"),
        ("x", (RS,), "shape", "목록"),
        ([{"file": RS, "old": "x"}], (RS,), "shape", "목록"),
    ],
)
def test_refusals_before_tests(env, edits, read, cls, word):
    r = Runner()
    res = _p(env, edits, read=read, runner=r)
    assert not res["ok"] and res["cls"] == cls and word in res["error"], res
    assert (
        r.calls == [] and _text(env.root, RS) == RESTOCK_PY and ce.load(env.root) == []
    )
    assert _rows(env.root, "code_edit_refused")[-1]["cls"] == cls


@pytest.mark.parametrize(
    "reason,expect", [("짧음", EXPECT), (REASON, "짧음"), (None, EXPECT), (REASON, 5)]
)
def test_short_reason_or_expect_refused(env, reason, expect):
    res = _p(env, reason=reason, expect=expect)
    assert res["cls"] == "short" and "20자" in res["error"]


@pytest.mark.parametrize(
    "file,old,new,word",
    [
        (
            "policy.py",
            'int(unit["cost"] * 1.1)',
            'int(unit["cost"] * 0.9)',
            "floor_price",
        ),
        (
            "policy.py",
            "    qty = 2\n",
            "    qty = 2\n    guard_price = None\n",
            "guard_price",
        ),
        (
            "policy.py",
            "def restock_plan(",
            "guard_price = lambda u, p: p\n\n\ndef restock_plan(",
            "guard_price",
        ),
        (
            "policy.py",
            "def validate(pol):\n    if",
            "def validate(pol):\n    return True\n    if",
            "validate",
        ),
        ("hooks/market_buyer.py", "return price > cap", "return False", "over_cap"),
        (
            "hooks/seller.py",
            "return P.guard_price(unit, price)",
            "return price",
            "Seller.decide",
        ),
        ("hooks/seller.py", 'ENV_NAME = "shop"', 'ENV_NAME = "other"', "ENV_NAME"),
        ("hooks/restock.py", "return P.restock_plan({}, {})", "return []", "main"),
    ],
)
def test_protected_functions_refused(env, monkeypatch, file, old, new, word):
    monkeypatch.setenv(ce.RESTART_ENV, "restart-seller")
    res = _p(env, [{"file": file, "old": old, "new": new}], read=(file,))
    assert res["cls"] == "protected" and word in res["error"], res


def test_guard_reference_may_not_be_dropped(env, monkeypatch):
    monkeypatch.setenv(ce.RESTART_ENV, "restart-seller")
    # Seller.decide is protected as a whole; a sign() call removed elsewhere is a dropped guard reference
    _write(
        os.path.join(env.root, "hooks/seller.py"),
        SELLER_PY + "\n\ndef deal(r):\n    return sign(r)\n",
    )
    res = _p(
        env,
        [{"file": "hooks/seller.py", "old": "return sign(r)", "new": "return r"}],
        read=("hooks/seller.py",),
    )
    assert res["cls"] == "protected" and "sign" in res["error"]


@pytest.mark.parametrize(
    "new,word",
    [
        ("import subprocess\n    return 2", "import subprocess"),
        ("from urllib import request\n    return 2", "import urllib"),
        ("return eval('2')", "eval"),
        ("return __import__('os')", "__import__"),
        ("import os\n    return os.system('ls')", "os.system"),
        ("import os\n    return os.environ.get('X')", "os.environ"),
        ("return open('f', 'w')", "open(쓰기)"),
        ("return open('f', mode='a')", "open(쓰기)"),
        ("return 'https://example.com'", "URL"),
    ],
)
def test_banned_additions_refused(env, new, word):
    res = _p(env, [{"file": RS, "old": "return 1", "new": new}])
    assert res["cls"] == "banned" and word in res["error"], res


def test_existing_risky_code_may_stay_and_read_open_and_re_compile_are_fine(env):
    _write(
        os.path.join(env.root, RS),
        RESTOCK_PY + "\n\ndef old():\n    return eval('1')\n",
    )
    res = _p(
        env,
        [
            {
                "file": RS,
                "old": "return 1",
                "new": "import re\n    open('x').read()\n    return re.compile('a')",
            }
        ],
    )
    assert res["ok"], res


def test_tests_newly_failing_refused(env):
    r = Runner(
        new={
            "ok": False,
            "failed": ["tests/test_x.py::test_plan"],
            "summary": "1 failed",
            "error": None,
        }
    )
    res = _p(env, runner=r)
    assert (
        res["cls"] == "tests"
        and "test_plan" in res["error"]
        and res["tests"]["new_failed"] == ["tests/test_x.py::test_plan"]
    )
    assert _text(env.root, RS) == RESTOCK_PY and ce.load(env.root) == []
    assert len(os.listdir(os.path.join(env.root, ce.STAGE))) == 1  # kept for debugging


@pytest.mark.parametrize("which", ["base", "new"])
def test_runner_error_or_timeout_refused(env, which):
    bad = {"ok": False, "failed": [], "summary": "", "error": "시험 시간 초과 300초"}
    r = Runner(**{which: bad})
    res = _p(env, runner=r)
    assert (
        res["cls"] == "runner"
        and "시간 초과" in res["error"]
        and _text(env.root, RS) == RESTOCK_PY
    )


def test_runner_that_raises_is_refused(env):
    def boom(stage_dir, timeout_s):
        raise RuntimeError("no python")

    res = _p(env, runner=boom)
    assert (
        res["cls"] == "runner"
        and "RuntimeError" in res["error"]
        and _text(env.root, RS) == RESTOCK_PY
    )


def test_no_tests_dir_refused(env):
    import shutil

    shutil.rmtree(os.path.join(env.root, "tests"))
    res = _p(env)
    assert res["cls"] == "runner" and "tests 폴더" in res["error"]


def test_stage_dirs_capped(env):
    r = Runner(new={"ok": False, "failed": ["t"], "summary": "", "error": None})
    for i in range(5):
        _p(env, runner=r, now=NOW + i)
    assert len(os.listdir(os.path.join(env.root, ce.STAGE))) == ce.STAGE_KEEP


def test_one_open_cooldown_and_repeat_after_revert(env):
    assert _p(env)["ok"]
    edit2 = {"file": RS, "old": "return 2", "new": "return 3"}
    assert _p(env, [edit2], now=NOW + 60)["cls"] == "open"
    rows = ce.load(env.root)
    rows[0].update(status="kept", ended_ts=NOW + 100)
    ce._save(env.root, rows)
    res = _p(env, [edit2], now=NOW + 600)
    assert res["cls"] == "cooldown" and "60분" in res["error"]
    assert _p(env, [edit2], now=NOW + ce.COOLDOWN_S + 1)["ok"]
    rows = ce.load(env.root)
    ev = ce.revert(
        env.root, rows[1], NOW + ce.COOLDOWN_S + 100, env.traj, "감시 중 이상", "canary"
    )
    assert ev["status"] == "reverted" and "return 2" in _text(env.root, RS)
    ce._save(env.root, rows)
    res = _p(env, [edit2], now=NOW + 3 * H)
    assert res["cls"] == "repeat"
    assert _p(env, [edit2], now=NOW + 8 * H)["ok"]  # past REPEAT_S


def test_corrupt_state_files_read_as_no_history(env):
    _write(os.path.join(env.root, ce.CHANGES), "{not json")
    _write(os.path.join(env.root, ce.BASELINE), "[1, 2")
    assert (
        ce.load(env.root) == []
        and ce.lines(env.root, NOW) == []
        and ce.watch(env.root, NOW, env.traj) == []
    )
    assert (
        ce.summary(env.root, NOW)["applied"] == 0 and ce.history_lines(env.root) == []
    )
    assert _p(env)["ok"]
    _write(
        os.path.join(env.root, ce.CHANGES),
        json.dumps([1, "x", {"id": 9, "status": "watching"}]),
    )
    assert isinstance(ce.watch(env.root, NOW, env.traj), list)  # a malformed entry never raises
    assert isinstance(ce.lines(env.root, NOW), list)


def test_real_default_runner_on_the_tiny_rule_code(env, monkeypatch):
    if importlib.util.find_spec("pytest") is None:
        pytest.skip("pytest not importable")
    monkeypatch.setenv(
        "A2_CODE_TEST_CMD",
        f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider --no-header -rfE "
        "--continue-on-collection-errors --rootdir=. --confcutdir=.",
    )
    good = {"file": "policy.py", "old": "    qty = 2\n", "new": "    qty = 3\n"}
    monkeypatch.setenv(ce.RESTART_ENV, "restart-seller")
    res = ce.propose(
        env.root, [good], REASON, EXPECT, NOW, env.traj, read_files=("policy.py",)
    )
    assert res["ok"] and "2 passed" in res["tests"]["summary"], res
    assert res["status"] == "restart_pending"
    rows = ce.load(env.root)
    rows[0].update(status="kept", ended_ts=NOW)
    ce._save(env.root, rows)
    bad = {"file": "policy.py", "old": "    qty = 3\n", "new": "    qty = 0\n"}
    res = ce.propose(
        env.root,
        [bad],
        REASON,
        EXPECT,
        NOW + 2 * H,
        env.traj,
        read_files=("policy.py",),
    )
    assert res["cls"] == "tests" and "test_plan" in res["error"], res
    assert "qty = 3" in _text(env.root, "policy.py")


# ------------------------------------------------------------ restart flow
def test_restart_needed_without_command_is_refused(env):
    res = _p(
        env,
        [{"file": "policy.py", "old": "    qty = 2\n", "new": "    qty = 3\n"}],
        read=("policy.py",),
    )
    assert (
        res["cls"] == "restart"
        and ce.RESTART_ENV in res["error"]
        and "qty = 2" in _text(env.root, "policy.py")
    )
    res = _p(
        env,
        [{"file": "hooks/seller.py", "old": 'return "hello"', "new": 'return "hi"'}],
        read=("hooks/seller.py",),
    )
    assert res["cls"] == "restart"


def _result(root, cid, ok, ts, why="done"):
    _write(
        os.path.join(root, ce.RESTART_RESULT),
        json.dumps({"id": cid, "ok": ok, "ts": ts, "why": why}),
    )


SELLER_EDIT = {
    "file": "hooks/seller.py",
    "old": 'return "hello"',
    "new": 'return "hi there"',
}


def _seller_change(env, monkeypatch):
    monkeypatch.setenv(ce.RESTART_ENV, "restart-seller")
    res = _p(env, [SELLER_EDIT], read=("hooks/seller.py",))
    assert res["ok"] and res["status"] == "restart_pending", res
    return res["id"]


def test_restart_pending_to_watching_on_ok_result(env, monkeypatch):
    cid = _seller_change(env, monkeypatch)
    ch = ce.load(env.root)[0]
    assert (
        ch["applied_ts"] is None
        and ch["metrics"] == ["shop"]
        and "hi there" in _text(env.root, "hooks/seller.py")
    )
    rs = Restarter()
    t = time.monotonic()
    assert (
        ce.watch(env.root, NOW + 10, env.traj, restarter=rs) == []
    )  # started, no result yet: no wait
    assert time.monotonic() - t < 2 and rs.calls == [cid]
    ch = ce.load(env.root)[0]
    assert (
        ch["status"] == "restart_pending"
        and ch["restart"]["pid"] == 4242
        and ch["restart"]["started_ts"] == NOW + 10
    )
    assert ce.watch(env.root, NOW + 70, env.traj, restarter=rs) == [] and rs.calls == [
        cid
    ]  # launched once
    _result(env.root, cid, True, NOW + 120)
    ev = ce.watch(env.root, NOW + 130, env.traj, restarter=rs)
    assert ev and ev[0]["status"] == "watching" and not ev[0]["wake"]
    ch = ce.load(env.root)[0]
    assert ch["status"] == "watching" and ch["applied_ts"] == NOW + 120
    assert (
        ch["watch_until"] == NOW + 120 + ce.WATCH_S
        and ch["eval_until"] == NOW + 120 + 4 * H
    )
    assert "판매자 재시작 대기" not in " ".join(ce.lines(env.root, NOW + 130))


@pytest.mark.parametrize("how", ["result", "timeout", "exit", "launch"])
def test_failed_restart_restores_files(env, monkeypatch, how):
    cid = _seller_change(env, monkeypatch)
    rs = Restarter(rc=3 if how == "exit" else None, fail=how == "launch")
    first = ce.watch(env.root, NOW + 10, env.traj, restarter=rs)
    if how == "result":
        _result(env.root, cid, False, NOW + 60, "seller did not answer")
        ev = ce.watch(env.root, NOW + 70, env.traj, restarter=rs)
    elif how == "timeout":
        assert (
            ce.watch(
                env.root, NOW + 10 + ce.RESTART_TIMEOUT_S - 1, env.traj, restarter=rs
            )
            == []
        )
        ev = ce.watch(env.root, NOW + 10 + ce.RESTART_TIMEOUT_S, env.traj, restarter=rs)
    elif how == "exit":  # the command already ended without a result file: judged on the launching poll
        ev = first
    else:
        ev = ce.load(env.root)[0]["status"] == "refused_restart" and [
            {"status": "refused_restart", "wake": True}
        ]
    assert ev and ev[0]["status"] == "refused_restart" and ev[0]["wake"], ev
    assert _text(env.root, "hooks/seller.py") == SELLER_PY
    ch = ce.load(env.root)[0]
    assert ch["status"] == "refused_restart" and "재시작 실패" in ch["why"]
    assert _rows(env.root, "code_edit_end")[-1]["status"] == "refused_restart"
    assert ce.summary(env.root, NOW + 2 * H)["refused_restart"] == 1


def test_revert_of_a_restarted_change_goes_through_restart(env, monkeypatch):
    cid = _seller_change(env, monkeypatch)
    rs = Restarter()
    ce.watch(env.root, NOW + 10, env.traj, restarter=rs)
    _result(env.root, cid, True, NOW + 20)
    ce.watch(env.root, NOW + 30, env.traj, restarter=rs)
    t0 = NOW + 20
    _metrics(
        env.root,
        [{"ts": t0 - 300, "hook": "seller", "decision": "open"}]
        + [
            {"ts": t0 + 60 + i, "hook": "seller", "decision": "error_a2a"}
            for i in range(10)
        ],
    )
    ev = ce.watch(env.root, t0 + 120, env.traj, restarter=rs)
    assert (
        ev[0]["status"] == "revert_restart_pending"
        and not ev[0]["wake"]
        and rs.calls == [cid, cid]
    )
    assert _text(env.root, "hooks/seller.py") == SELLER_PY  # files restored at once
    _result(env.root, cid, False, t0 + 200, "busy")
    ev = ce.watch(env.root, t0 + 210, env.traj, restarter=rs)
    assert (
        ev[0]["status"] == "revert_restart_pending"
        and ev[0]["wake"]
        and "1회" in ev[0]["why"]
    )
    assert (
        ce.watch(env.root, t0 + 300, env.traj, restarter=rs) == []
        and len(rs.calls) == 2
    )  # waits RETRY_S
    ce.watch(env.root, t0 + 210 + ce.RETRY_S, env.traj, restarter=rs)
    assert len(rs.calls) == 3
    _result(env.root, cid, True, t0 + 210 + ce.RETRY_S + 50)
    ev = ce.watch(env.root, t0 + 210 + ce.RETRY_S + 60, env.traj, restarter=rs)
    assert (
        ev[0]["status"] == "reverted" and ev[0]["wake"] and ev[0]["phase"] == "canary"
    )
    ch = ce.load(env.root)[0]
    assert ch["status"] == "reverted" and "seller 오류 비율" in ch["why"]
    assert ce.summary(env.root, NOW + 3 * H)["reverted_canary"] == 1


def test_seller_silence_after_restart_reverts(env, monkeypatch):
    cid = _seller_change(env, monkeypatch)
    rs = Restarter()
    ce.watch(env.root, NOW + 10, env.traj, restarter=rs)
    _result(env.root, cid, True, NOW + 20)
    ce.watch(env.root, NOW + 30, env.traj, restarter=rs)
    _metrics(env.root, [{"ts": NOW - 100, "hook": "seller", "decision": "open"}])
    assert (
        ce.watch(env.root, NOW + 20 + ce.SELLER_SILENCE_S - 1, env.traj, restarter=rs)
        == []
    )
    ev = ce.watch(env.root, NOW + 20 + ce.SELLER_SILENCE_S, env.traj, restarter=rs)
    assert (
        ev[0]["status"] == "revert_restart_pending"
        and "seller 기록" in ce.load(env.root)[0]["why"]
    )


# ------------------------------------------------------------ canary and evaluation
def _applied(env):
    assert _p(env)["ok"]
    return ce.load(env.root)[0]


@pytest.mark.parametrize(
    "row,word",
    [
        (
            {
                "hook": "restock",
                "decision": "restock_plan",
                "outcome": {"why": "plan_error"},
            },
            "plan_error",
        ),
        (
            {
                "hook": "restock",
                "decision": "restock_plan",
                "outcome": {"warnings": ["goods cap error: x"]},
            },
            "경고",
        ),
        (
            {
                "hook": "restock",
                "decision": "restock_plan",
                "outcome": {},
                "extra": {"activity": {"error": "boom"}},
            },
            "activity",
        ),
        (
            {"hook": "restock", "decision": "restock_error", "outcome": {"error": "x"}},
            "restock_error",
        ),
    ],
)
def test_canary_reverts_restock_errors(env, row, word):
    _applied(env)
    _metrics(
        env.root,
        [
            dict(row, ts=NOW - 60),
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW + 30,
                "outcome": {},
            },
        ],
    )
    assert (
        ce.watch(env.root, NOW + 40, env.traj) == []
    )  # the error row is before the apply
    _metrics(env.root, [dict(row, ts=NOW + 50)])
    ev = ce.watch(env.root, NOW + 60, env.traj)
    assert (
        ev[0]["status"] == "reverted" and ev[0]["wake"] and ev[0]["phase"] == "canary"
    )
    assert word in ce.load(env.root)[0]["why"] and _text(env.root, RS) == RESTOCK_PY


def test_canary_restock_silence(env):
    _applied(env)
    _metrics(
        env.root,
        [
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW - 300,
                "outcome": {},
            }
        ],
    )
    assert ce.watch(env.root, NOW + ce.SILENCE_S - 1, env.traj) == []
    ev = ce.watch(env.root, NOW + ce.SILENCE_S, env.traj)
    assert ev[0]["status"] == "reverted" and "20분" in ce.load(env.root)[0]["why"]


def test_canary_pass_then_evaluation_kept(env):
    _applied(env)
    _metrics(
        env.root,
        [
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW + 60 * i,
                "outcome": {},
            }
            for i in range(-5, 31)
        ],
    )
    ev = ce.watch(env.root, NOW + ce.WATCH_S, env.traj)
    assert ev == [{"id": 1, "status": "evaluating", "phase": "canary", "wake": False}]
    assert ce.load(env.root)[0]["status"] == "evaluating"
    assert ce.watch(env.root, NOW + 4 * H - 1, env.traj) == []
    ev = ce.watch(env.root, NOW + 4 * H, env.traj)
    assert ev[0]["status"] == "kept" and ev[0]["wake"]
    ch = ce.load(env.root)[0]
    assert ch["status"] == "kept" and ch["verdict"]["metrics"] == ["shop"]
    assert (
        ch["verdict"]["shop"]["decision"] == "no_baseline"
    )  # no events at all: nothing to compare, code stays
    assert "return 2" in _text(env.root, RS)


def test_evaluation_worse_reverts_and_metric_selection(env, monkeypatch):
    seen = []

    def fake(root, exp, now, data=None):
        seen.append(
            (
                exp["side"],
                exp["scope"],
                exp["started_ts"],
                exp["window_h"],
                exp["maturity_h"],
            )
        )
        return {
            "decision": "worse" if exp["side"] == "buyer" else "unclear",
            "reason": "clear",
            "effect": -1.0,
            "se": 0.1,
            "n_before": 9,
            "n_after": 9,
        }

    monkeypatch.setattr(ex, "evaluate", fake)
    assert ce.metrics_for(["hooks/restock.py"]) == ["shop"]
    assert ce.metrics_for(["hooks/seller.py"]) == ["shop"]
    assert ce.metrics_for(["hooks/market_buyer.py"]) == ["buyer"]
    assert ce.metrics_for(["policy.py"]) == ["shop", "buyer"]
    _applied(env)
    rows = ce.load(env.root)
    rows[0].update(status="evaluating")
    ce._save(env.root, rows)
    ev = ce.watch(env.root, NOW + 4 * H, env.traj)
    assert ev[0]["status"] == "kept" and seen == [("shop", "shop:all", NOW, 4, 0)]
    rows = ce.load(env.root)
    rows[0].update(status="evaluating", metrics=["shop", "buyer"], files=[RS])
    ce._save(env.root, rows)
    ev = ce.watch(env.root, NOW + 5 * H, env.traj)
    assert (
        ev[0]["status"] == "reverted"
        and ev[0]["phase"] == "evaluation"
        and "(buyer)" in ev[0]["why"]
    )
    assert seen[-1][:2] == ("buyer", "buyer:all") and _text(env.root, RS) == RESTOCK_PY
    assert ce.summary(env.root, NOW + 6 * H)["reverted_eval"] == 1


def test_buyer_scope_relabels_both_kinds():
    data = {
        "deals": [{"ts": 1, "kind": "food"}, {"ts": 2, "kind": "goods"}],
        "others": [(1, "food"), (2, "goods")],
    }
    d = ce._buyer_all(data)
    assert {x["kind"] for x in d["deals"]} == {"all"} and {
        k for _, k in d["others"]
    } == {"all"}
    assert data["deals"][0]["kind"] == "food"


def test_revert_conflict_when_live_file_changed(env):
    ch = _applied(env)
    _write(os.path.join(env.root, RS), "def main():\n    return 'human'\n")
    _metrics(
        env.root,
        [
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW + 30,
                "outcome": {"why": "plan_error"},
            }
        ],
    )
    ev = ce.watch(env.root, NOW + 60, env.traj)
    assert ev[0]["status"] == "conflict" and "따로 바뀜" in ev[0]["why"]
    assert (
        "human" in _text(env.root, RS) and ce.load(env.root)[0]["status"] == "conflict"
    )
    assert (
        ch["id"] == 1 and _rows(env.root, "code_edit_end")[-1]["status"] == "conflict"
    )


def test_buyer_edit_happy_path_and_canary_revert(env):
    edit = {
        "file": "hooks/market_buyer.py",
        "old": "return sorted(cands)",
        "new": "return sorted(cands, reverse=True)",
    }
    res = _p(env, [edit], read=("hooks/market_buyer.py",))
    assert res["ok"] and res["status"] == "watching", (
        res
    )  # the buyer hook runs fresh per call: no restart
    ch = ce.load(env.root)[0]
    assert (
        ch["metrics"] == ["buyer"]
        and "s0" in ch
        and "reverse=True" in _text(env.root, "hooks/market_buyer.py")
    )
    rows = [
        {
            "ts": NOW - 1000 + i,
            "hook": "market_buyer",
            "decision": "buy",
            "outcome": {"deal": True},
        }
        for i in range(20)
    ]
    rows += [
        {
            "ts": NOW + 10 + i,
            "hook": "market_buyer",
            "decision": "buy",
            "outcome": {"deal": False},
        }
        for i in range(8)
    ]
    rows += [
        {
            "ts": NOW + 30 + i,
            "hook": "market_buyer",
            "decision": "buy",
            "outcome": {"error": "KeyError"},
        }
        for i in range(2)
    ]
    _metrics(env.root, rows)
    ev = ce.watch(env.root, NOW + 100, env.traj)  # 2 of 10 rows are errors: 20% > max(10%, 2 x 0%)
    assert ev[0]["status"] == "reverted" and ev[0]["phase"] == "canary"
    assert _text(env.root, "hooks/market_buyer.py") == BUYER_PY


def test_buyer_canary_error_share_and_silence(env):
    edit = {
        "file": "hooks/market_buyer.py",
        "old": "return sorted(cands)",
        "new": "return sorted(cands, reverse=True)",
    }
    assert _p(env, [edit], read=("hooks/market_buyer.py",))["ok"]
    rows = [
        {
            "ts": NOW - 1000 + i,
            "hook": "market_buyer",
            "decision": "buy",
            "outcome": {"deal": True},
        }
        for i in range(20)
    ]
    rows += [
        {
            "ts": NOW + 10 + i,
            "hook": "market_buyer",
            "decision": "buy",
            "outcome": {"deal": False},
        }
        for i in range(9)
    ]
    _metrics(env.root, rows)
    assert ce.watch(env.root, NOW + 100, env.traj) == []  # 9 rows: under ERR_MIN_ROWS
    _metrics(
        env.root,
        [
            {
                "ts": NOW + 50,
                "hook": "market_buyer",
                "decision": "buy",
                "outcome": {"error": "KeyError"},
            }
        ],
    )
    ev = ce.watch(env.root, NOW + 110, env.traj)  # 1 of 10 = 10%: not above max(10%, 0)
    assert ev == []
    _metrics(
        env.root,
        [
            {
                "ts": NOW + 51,
                "hook": "market_buyer",
                "decision": "buy_exception",
                "outcome": {},
            }
        ],
    )
    ev = ce.watch(env.root, NOW + 120, env.traj)  # 2 of 11
    assert (
        ev[0]["status"] == "reverted"
        and "market_buyer 오류 비율" in ce.load(env.root)[0]["why"]
    )
    assert _text(env.root, "hooks/market_buyer.py") == BUYER_PY


def test_buyer_canary_silence(env):
    edit = {
        "file": "hooks/market_buyer.py",
        "old": "return sorted(cands)",
        "new": "return sorted(cands, reverse=True)",
    }
    assert _p(env, [edit], read=("hooks/market_buyer.py",))["ok"]
    _metrics(
        env.root,
        [
            {"ts": NOW - 60, "hook": "market_buyer", "decision": "buy", "outcome": {}},
            {"ts": NOW + 30, "hook": "restock", "decision": "restock_error"},
        ],
    )  # restock rows do not count
    assert ce.watch(env.root, NOW + ce.SILENCE_S - 1, env.traj) == []
    ev = ce.watch(env.root, NOW + ce.SILENCE_S, env.traj)
    assert (
        ev[0]["status"] == "reverted"
        and "market_buyer 기록" in ce.load(env.root)[0]["why"]
    )


def test_watch_survives_a_broken_entry_and_voids_after_max_fails(env):
    _write(
        os.path.join(env.root, ce.CHANGES),
        json.dumps([{"id": 1, "ts": NOW, "status": "watching", "files": [RS]}]),
    )
    for i in range(ce.MAX_FAILS):
        ev = ce.watch(env.root, NOW + i * ce.RETRY_S, env.traj)
    assert ev[0]["status"] == "voided" and ce.load(env.root)[0]["status"] == "voided"


# ------------------------------------------------------------ state card, summary, audit, auditor
def test_lines_and_summary(env):
    assert ce.lines(env.root, NOW) == []
    _applied(env)
    ln = ce.lines(env.root, NOW + 60)
    assert (
        len(ln) == 1
        and ln[0].startswith("[코드] 수정 #1 hooks/restock.py 감시 중")
        and "shop 기준" in ln[0]
    )
    _p(env, read=(), now=NOW + 70)
    _p(
        env,
        [{"file": "hooks/common.py", "old": "X", "new": "Y"}],
        read=("hooks/common.py",),
        now=NOW + 80,
    )
    _metrics(
        env.root,
        [
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW + 90,
                "outcome": {"why": "plan_error"},
            }
        ],
    )
    ce.watch(env.root, NOW + 100, env.traj)
    ln = ce.lines(env.root, NOW + 200)
    assert (
        ln[0].startswith("[코드] 수정 #1 hooks/restock.py 되돌림")
        and "plan_error" in ln[0]
    )
    assert ln[-1].startswith("[코드] 다음 코드 수정은") and all(
        "—" not in x for x in ln
    )
    s = ce.summary(env.root, NOW + 300)
    assert (
        s["applied"] == 1
        and s["refused"] == 2
        and s["refused_by"] == {"not_read": 1, "not_editable": 1}
    )
    assert s["reverted_canary"] == 1 and s["last_revert"]["id"] == 1
    assert (
        ce.summary(env.root, NOW + 30 * H)["applied"] == 0
    )  # out of the window (rows carry the kernel time)
    hl = ce.history_lines(env.root)
    assert "이유: 재입고가" in hl[0] and "기대: 산 실행" in hl[0] and "되돌림" in hl[0]


def test_audit_counts_and_findings(env):
    _applied(env)
    _p(env, read=(), now=NOW + 70)
    _p(env, read=(), now=NOW + 71)
    rep = audit.report(env.root, NOW + 100)
    assert (
        rep["code"]["applied"] == 1
        and rep["code"]["refused"] == 2
        and rep["code"]["refused_by"] == {"not_read": 2}
    )
    assert set(rep["score"]) == {"mistakes_per_wake", "idle_share", "hit_rate", "wakes"}
    env.traj.append(
        "operator",
        "operator_wake",
        status="done",
        result={"calls": 2, "prompt_chars": 10, "code": {"ok": True}},
    )
    _metrics(
        env.root,
        [
            {
                "hook": "restock",
                "decision": "restock_plan",
                "ts": NOW + 90,
                "outcome": {"why": "plan_error"},
            }
        ],
    )
    ce.watch(env.root, NOW + 100, env.traj)
    rows = [
        json.loads(x)
        for x in open(os.path.join(env.root, "a2/trajectory.jsonl"), encoding="utf-8")
    ]
    for r in rows:
        if r.get("kind") == "operator_wake":
            r["ts"] = NOW + 50
    _write(
        os.path.join(env.root, "a2/trajectory.jsonl"),
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
    )
    rep = audit.report(env.root, NOW + 200)
    assert rep["idle_wakes"] == 0  # a wake that applied code is not idle
    text = "\n".join(audit.lines(env.root, NOW + 200))
    assert "코드 수정이 되돌려짐 (감시 1건" in text and "plan_error" in text
    assert "코드 수정 제안 2건 거절됨, 최다 사유 '읽지 않은 파일' 2건임" in text


def test_auditor_prompt_has_the_code_block(env):
    _applied(env)
    p = au.build_prompt(
        env.root, NOW + 100, secrets=("secretlogin",), listed=["policy"]
    )
    assert (
        "## 코드 수정 (운영자가 규칙 코드를 고친 기록)" in p
        and "hooks/restock.py" in p
        and "이유: 재입고가" in p
    )
    assert len(p) <= au.PROMPT_CHARS
    root2 = env.root + "-empty"
    os.makedirs(os.path.join(root2, "a2"))
    p = au.build_prompt(root2, NOW, listed=["policy"])
    assert "## 코드 수정 (운영자가 규칙 코드를 고친 기록)\n\n없음" in p


# ------------------------------------------------------------ operator wiring
class Script:
    def __init__(self, *acts):
        self.acts, self.prompts = list(acts), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        return json.dumps(self.acts.pop(0), ensure_ascii=False)


@pytest.fixture
def o(env, monkeypatch, tmp_path):
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    monkeypatch.delenv("A2_OPERATOR_INTERVAL_S", raising=False)
    monkeypatch.setattr(ce, "default_runner", Runner())
    return op.Operator(
        env.root, None, env.traj, harness_root=str(tmp_path / "noharness")
    )


CODE = {"edits": [EDIT], "reason": REASON, "expect": EXPECT}


def test_compact_wake_applies_code_read_this_wake(o):
    o.terra = Script(
        {"tools": [{"tool": "code_read", "args": {"file": RS}}], "keep": "helper 확인"},
        {"done": {"code": CODE, "note": "코드 수정"}},
    )
    res = o.wake("주기 점검", NOW)
    assert (
        res["code"]["ok"]
        and res["code"]["id"] == 1
        and res["code"]["tests"] == "2 passed"
    ), res
    assert "return 2" in _text(o.root, RS)
    assert "code_read" not in res.get("grounding", {}).get("uncited", [])
    assert "code" in op.COMPACT_SCHEMA and "code_read" in o.terra.prompts[0]
    o.terra = Script({"done": {}})
    o.wake("주기 점검", NOW + 60)
    assert "[코드] 수정 #1 hooks/restock.py 감시 중" in o.terra.prompts[0]


def test_compact_wake_refuses_code_not_read_this_wake(o):
    o.terra = Script(
        {"tools": [{"tool": "code_search", "args": {"pattern": "helper"}}]},
        {"tools": [{"tool": "code_read", "args": {"file": "../policy.py"}}]},
        {"done": {"code": CODE, "policy": []}},
    )
    res = o.wake("주기 점검", NOW)
    assert (
        not res["code"]["ok"]
        and "읽지 않은" in res["code"]["error"]
        and _text(o.root, RS) == RESTOCK_PY
    )


def test_main_wakes_on_a_code_verdict(o, monkeypatch, tmp_path):
    monkeypatch.setattr(
        ce,
        "watch",
        lambda root, now, traj: [
            {"id": 1, "status": "kept", "wake": True},
            {"id": 2, "status": "evaluating", "wake": False},
        ],
    )
    monkeypatch.setattr(op.Operator, "wake_reason", lambda self, now=None: None)
    monkeypatch.setattr(
        op.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        op.main(["--root", o.root, "--dry", "--harness", str(tmp_path / "noharness")])
    wakes = _rows(o.root, "operator_wake")
    assert (
        wakes and wakes[-1]["reason"] == "코드 수정 판정 1건 (상태 카드의 [코드] 줄)"
    ), wakes


# ------------------------------------------------------------ staging and the default runner
def test_stage_copies_packages_skips_data_and_uses_tests_dir(env, monkeypatch, tmp_path):
    _write(os.path.join(env.root, "experiments/__init__.py"), "")
    _write(os.path.join(env.root, "experiments/core.py"), "Y = 1\n")
    _write(os.path.join(env.root, "taste/notes.txt"), "plain dir\n")
    _write(os.path.join(env.root, "data/x.py"), "Z = 1\n")
    _write(os.path.join(env.root, "conftest.py"), "")
    _write(os.path.join(env.root, "big/blob.bin"), "")
    with open(os.path.join(env.root, "big/blob.bin"), "wb") as f:
        f.write(b"0" * (ce.DIR_MAX + 1))
    _write(os.path.join(env.root, "logs/x.py"), "")
    dst = str(tmp_path / "stage")
    ce._copy_rule_code(env.root, dst)
    got = set(os.listdir(dst))
    assert {"experiments", "taste", "hooks", "tests", "policy.py", "policy.json", "conftest.py"} <= got
    assert not got & {"data", "big", "logs", "a2"}
    tdir = tmp_path / "suite"
    _write(str(tdir / "test_new.py"), "def test_y():\n    pass\n")
    monkeypatch.setenv(ce.TESTS_DIR_ENV, str(tdir))
    dst2 = str(tmp_path / "stage2")
    ce._copy_rule_code(env.root, dst2)
    assert os.listdir(os.path.join(dst2, "tests")) == ["test_new.py"]


def test_tests_dir_env_counts_as_the_tests_folder(env, monkeypatch, tmp_path):
    import shutil

    shutil.rmtree(os.path.join(env.root, "tests"))
    tdir = tmp_path / "suite"
    _write(str(tdir / "test_new.py"), "def test_y():\n    pass\n")
    monkeypatch.setenv(ce.TESTS_DIR_ENV, str(tdir))
    assert _p(env)["ok"]


class Proc:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.mark.parametrize(
    "rc,out,ok,failed,error",
    [
        (0, "....\n4 passed in 0.1s\n", True, [], None),
        (
            1,
            "FAILED tests/test_a.py::test_x - assert 1\nERROR tests/test_b.py - ImportError\n"
            "1 failed, 3 passed, 1 error in 0.2s\n",
            False,
            ["tests/test_a.py::test_x", "tests/test_b.py"],
            None,
        ),
        (2, "", False, [], "요약 줄이 없음"),
        (1, "something broke\n", False, [], "요약 줄이 없음"),
        (5, "no tests ran in 0.01s\n", False, [], "모은 시험이 없음"),
    ],
)
def test_default_runner_parses_failed_and_error_and_needs_a_summary(env, monkeypatch, rc, out, ok, failed, error):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"))
        return Proc(rc, out, "uv: command failed")

    monkeypatch.delenv(ce.TEST_CMD_ENV, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    stage = os.path.join(env.root, ce.STAGE, "1-1", "new")
    os.makedirs(stage)
    r = ce.default_runner(stage, 5)
    assert r["ok"] is ok and r["failed"] == failed and (error is None) == (r["error"] is None), r
    assert error is None or error in r["error"]
    assert seen["cmd"][:4] == ["uv", "run", "--project", os.path.abspath(env.root)] and "-x" not in seen["cmd"]
    assert seen["cwd"] == os.path.abspath(stage)
    monkeypatch.setenv(ce.TEST_CMD_ENV, "mytest --root {root} -q")
    ce.default_runner(stage, 5)
    assert seen["cmd"] == ["mytest", "--root", os.path.abspath(env.root), "-q"]


def test_module_erroring_only_after_the_edit_blocks(env):
    base = {"ok": False, "failed": ["tests/test_old.py"], "summary": "1 error", "error": None}
    new = {"ok": False, "failed": ["tests/test_old.py", "tests/test_x.py"], "summary": "2 errors", "error": None}
    res = _p(env, runner=Runner(base=base, new=new))
    assert res["cls"] == "tests" and "tests/test_x.py" in res["error"] and "test_old" not in res["error"]


def test_restart_command_gets_the_change_id_last(env, monkeypatch):
    seen = {}

    class P:
        pid = 7

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw["cwd"], new=kw["start_new_session"])
        return P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv(ce.RESTART_ENV, "bash /code/live/seller_restart.sh")
    h = ce.default_restarter(env.root, 3, os.path.join(env.root, "a2/logs/seller_restart-3.log"))
    assert h.pid == 7 and seen == {"cmd": ["bash", "/code/live/seller_restart.sh", "3"], "cwd": env.root, "new": True}
    assert os.path.isfile(os.path.join(env.root, "a2/logs/seller_restart-3.log"))


def test_hardening_new_imports_and_reflection_refused():
    from kernel import code_edit as ce

    base = "import os\nimport json\n\ndef f(x):\n    return os.path.join('a', x)\n"
    for add in ("import zipfile\n", "from pathlib import Path\n", "o = os\n", "y = getattr(json, 'loads')\n",
                "z = f.__globals__\n", "import sys\n", "os.rename('a', 'b')\n"):
        assert ce.banned_error("policy.py", base, base + add), add
    for ok in ("import math\n", "from collections import Counter\n", "p = os.path.basename('a')\n"):
        assert ce.banned_error("policy.py", base, base + ok) is None, ok


def test_hardening_fewer_passed_tests_refused(tmp_path, monkeypatch):
    from kernel import code_edit as ce

    root = tmp_path
    (root / "hooks").mkdir()
    (root / "tests").mkdir()
    (root / "policy.py").write_text("X = 1\n")
    (root / "a2").mkdir()
    runs = iter([{"failed": [], "summary": "10 passed", "error": None, "passed": 10},
                 {"failed": [], "summary": "7 passed, 3 skipped", "error": None, "passed": 7}])
    out = ce.run_tests(str(root), "t1", {"policy.py": "X = 2\n"}, lambda d, t: next(runs))
    assert out["ok"] is False and out["cls"] == "tests" and "10 -> 7" in out["error"]
