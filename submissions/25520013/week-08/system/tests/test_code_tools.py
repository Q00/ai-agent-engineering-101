"""Read-only code tools: static gate, sandboxed child run over hand-made logs, hashing of identities, limits, the
saved code tool lifecycle through made_tools and the analyze tool."""

import json
import os
import subprocess
import sys
import time

import pytest
from kernel import code_tools as ct
from kernel import common as c
from kernel import made_tools as mt
from kernel import tools
from kernel.trajectory import Trajectory

LOGIN = "secret-login"
NOW = 1_794_000_000.0
H = 3600

JOIN = """
import statistics
from collections import defaultdict

def run(load, args):
    buyer = {}
    for e in load("events", hours=24, where='"settle"'):
        d = e.get("data") or {}
        if e.get("type") == "settle" and d.get("order"):
            buyer[d["order"]] = d.get("buyer")
    stars = defaultdict(list)
    for e in load("events", hours=24, where='"review"'):
        d = e.get("data") or {}
        if e.get("type") == "review" and d.get("order") in buyer:
            stars[buyer[d["order"]]].append(d["stars"])
    low = args.get("low", 5)
    return {a: round(statistics.mean(v), 3) for a, v in sorted(stars.items()) if min(v) <= low}
"""


def _settle(ts, order, buyer):
    return {
        "ts": ts,
        "type": "settle",
        "src": "market",
        "data": {"order": order, "buyer": buyer, "amount": 100, "own_owner": True},
    }


def _review(ts, order, stars):
    return {
        "ts": ts,
        "type": "review",
        "role": "shop",
        "data": {"order": order, "stars": stars},
    }


@pytest.fixture
def root(tmp_path):
    r = str(tmp_path)
    with open(os.path.join(r, "policy.json"), "w") as f:
        json.dump({"owner": {"github": LOGIN}}, f)
    rows = [
        _settle(NOW - 5 * H, "o1", "acct-a"),
        _settle(NOW - 5 * H, "o2", "acct-a"),
        _settle(NOW - 4 * H, "o3", "acct-b"),
        _settle(NOW - 4 * H, "o4", LOGIN),
        _settle(NOW - 30 * H, "old", "acct-c"),  # outside 24 h
        _review(NOW - 3 * H, "o1", 5),
        _review(NOW - 3 * H, "o2", 2),
        _review(NOW - 2 * H, "o3", 4),
        _review(NOW - 2 * H, "o4", 1),
        _review(NOW - 2 * H, "old", 1),  # its settle is outside the window
        _review(NOW - 2 * H, "nobody", 3),  # no settle
        {
            "ts": NOW - H,
            "type": "probe",
            "owner": LOGIN,
            "data": {
                "seller": "shop-x",
                "own_owner": True,
                "owner": "acct-z",
                "participants": ["acct-a", LOGIN],
                "note": "y" * 500,
                "amount": 7,
            },
        },
    ]
    os.makedirs(os.path.join(r, ".r3"))
    with open(os.path.join(r, ".r3/events.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return r


def test_join_reviews_with_settles_gives_per_account_mean(root):
    out = json.loads(ct.execute(root, JOIN, None, NOW))
    assert out == {c.hid("acct-a"): 3.5, c.hid("acct-b"): 4, "우리": 1}
    assert c.hid("acct-a") == mt._cell(
        "data.buyer", "acct-a", LOGIN
    )  # same hash as the made tools show
    assert json.loads(ct.execute(root, JOIN, {"low": 2}, NOW)) == {
        c.hid("acct-a"): 3.5,
        "우리": 1,
    }


def test_identities_hashed_owner_keys_dropped_long_strings_cut(root):
    code = 'def run(load, args):\n    return [r for r in load("events", where="probe") if r["type"] == "probe"][0]'
    text = ct.execute(root, code, None, NOW)
    row = json.loads(text)
    assert (
        "owner" not in row
        and "owner" not in row["data"]
        and "own_owner" not in row["data"]
    )
    assert row["data"]["seller"] == c.hid("shop-x")
    assert row["data"]["participants"] == [c.hid("acct-a"), "우리"]
    assert row["data"]["note"] == "y" * ct.STR_CHARS and row["data"]["amount"] == 7
    assert LOGIN not in text and "acct-" not in text and "shop-x" not in text


@pytest.mark.parametrize(
    "code, why",
    [
        ("import os\ndef run(load, args):\n    return 1", "import os"),
        ("import socket\ndef run(load, args):\n    return 1", "import socket"),
        ("def run(load, args):\n    return open('/etc/passwd').read()", "open"),
        ("def run(load, args):\n    return ().__class__", "밑줄 속성"),
        ("def run(load, args):\n    return getattr(args, 'x')", "getattr"),
        ("def helper(load, args):\n    return 1", "def run"),
        ("def run(load):\n    return 1", "두 개"),
        (
            "def run(load, args):\n    g = (x for x in [1])\n    return g.gi_frame",
            "gi_frame",
        ),
        ("def run(load, args):\n    return '{0.gi_frame}'.format(args)", "format"),
        ("class A:\n    pass\ndef run(load, args):\n    return 1", "class"),
        ("x = 1\ndef run(load, args):\n    global x\n    return x", "global"),
        ("def run(load, args):\n    return _secret", "밑줄 이름"),
        ("def run(load, args) return 1", "문법 오류"),
        ("def run(load, args):\n    return 1\n" + "#" * ct.CODE_CHARS, "너무 김"),
    ],
)
def test_check_refuses(root, code, why):
    err = ct.check(code)
    assert err and why in err, err
    assert ct.execute(root, code, None, NOW).startswith("코드 오류:")


def test_check_allows_helpers_and_underscore_loop_variable():
    code = (
        "import math\ndef helper(x):\n    return math.sqrt(x)\n"
        "def run(load, args):\n    return [helper(4) for _ in range(2)]"
    )
    assert ct.check(code) is None


def test_runtime_errors_come_back_as_korean_text(root):
    out = ct.execute(
        root, "def run(load, args):\n    x = 1\n    return x / 0", None, NOW
    )
    assert out.startswith("코드 오류: ZeroDivisionError 3번째 줄")
    out = ct.execute(
        root, "def run(load, args):\n    return load('passwords')", None, NOW
    )
    assert out.startswith("코드 오류: ValueError") and "source는" in out
    out = ct.execute(
        root,
        "import statistics\ndef run(load, args):\n    return str(statistics.random)",
        None,
        NOW,
    )
    assert out.startswith(
        "코드 오류: AttributeError"
    )  # allowed modules come without their submodules


def test_infinite_loop_times_out(root):
    t = time.time()
    out = ct.execute(
        root,
        "def run(load, args):\n    while True:\n        pass",
        None,
        NOW,
        timeout_s=2,
    )
    assert out.startswith("코드 오류: 시간 한도 2초") and time.time() - t < 6


def test_output_is_capped_and_masked(root):
    out = ct.execute(
        root, "def run(load, args):\n    return 'a\\x00b ' + 'x' * 20000", None, NOW
    )
    assert (
        len(out) <= ct.OUT_CHARS + 100
        and "[잘림: 결과 20,004자" in out
        and "\x00" not in out
    )
    out = ct.execute(
        root,
        f"def run(load, args):\n    return 'me {LOGIN} sk-abcdefghijklmnop'",
        None,
        NOW,
    )
    assert LOGIN not in out and "[TOKEN]" in out


def test_writing_a_file_is_impossible(root, tmp_path, monkeypatch):
    assert ct.check("def run(load, args):\n    open('x', 'w').write('y')\n    return 1")
    monkeypatch.setattr(
        ct, "check", lambda code: None
    )  # even past the static gate there is no open
    out = ct.execute(
        root,
        "def run(load, args):\n    open('x', 'w').write('y')\n    return 1",
        None,
        NOW,
    )
    assert out.startswith("코드 오류: NameError")
    d = tmp_path / "w"
    d.mkdir()
    r = subprocess.run(
        [sys.executable, "-I", "-c", "open('f', 'w').write('x' * 10)"],
        cwd=d,
        env=ct.child_env(),
        preexec_fn=lambda: ct._limits(5),
        capture_output=True,
    )
    assert (d / "f").stat().st_size == 0 and b"File too large" in r.stderr  # RLIMIT_FSIZE 0: no byte lands


def test_child_env_carries_nothing_of_the_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_X", "s3cret-value")
    assert "SECRET_X" not in ct.child_env()
    r = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import os, json; print(json.dumps(dict(os.environ)))",
        ],
        cwd=tmp_path,
        env=ct.child_env(),
        capture_output=True,
        text=True,
    )
    assert "SECRET_X" not in json.loads(r.stdout) and "s3cret-value" not in r.stdout


def test_saved_code_tool_lifecycle(root):
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    spec = {
        "name": "q_review_acct",
        "description": "계정별 리뷰 평균 (주문으로 settle과 이음)",
        "code": JOIN,
    }
    res = mt.add(root, spec, NOW, traj=traj, by="analyst", reserved=tuple(tools.TOOLS))
    assert res["ok"] and "우리" in res["sample"]
    stored = json.load(open(os.path.join(root, mt.STORE), encoding="utf-8"))
    assert stored[0]["kind"] == "code" and stored[0]["code"] == JOIN
    assert ("q_review_acct", spec["description"]) in mt.listing(root)
    assert json.loads(mt.call(root, "q_review_acct", {"low": 2}, now=NOW)) == {
        c.hid("acct-a"): 3.5,
        "우리": 1,
    }
    assert mt.load(root)[0]["uses"] == 1
    out = tools.call("tool_search", "operator", root, query="계정별 리뷰")
    assert "- q_review_acct() 추천:" in out
    assert (
        mt.remove(root, "q_review_acct", NOW, traj=traj)["ok"]
        and mt.listing(root) == []
    )
    kinds = [
        json.loads(ln)["kind"]
        for ln in open(os.path.join(root, "a2/trajectory.jsonl"), encoding="utf-8")
    ]
    assert kinds == ["tool_made", "tool_removed"]


def test_code_tool_gate_refuses_bad_code_and_failing_run(root):
    bad = {
        "name": "q_bad_code",
        "description": "운영 체제 정보 읽기",
        "code": "import os\ndef run(load, args):\n    return 1",
    }
    res = mt.add(root, bad, NOW)
    assert not res["ok"] and "import os" in res["error"]
    fails = dict(bad, code="def run(load, args):\n    return 1 / 0")
    res = mt.add(root, fails, NOW)
    assert not res["ok"] and res["error"].startswith(
        "시험 실행 실패: 코드 오류: ZeroDivisionError"
    )
    res = mt.add(
        root, dict(bad, code="def run(load, args):\n    return 1", hours=3), NOW
    )
    assert not res["ok"] and "모르는 키: hours" in res["error"]
    assert mt.load(root) == []


def test_analyze_is_registered_and_found(root):
    assert "analyze" in tools.TOOLS and "operator" in tools.TOOLS["analyze"][1]
    out = tools.call(
        "analyze",
        "operator",
        root,
        code="def run(load, args):\n    return len(load('events'))",
        now=NOW,
    )
    assert out == "11"
    for q in ("코드", "분석", "잇기", "join"):
        assert "- analyze(code, args)" in tools.call(
            "tool_search", "operator", root, query=q
        ), q


def test_settle_rows_carry_our_side(tmp_path):
    (tmp_path / ".r3").mkdir(exist_ok=True)
    now = 1_800_000_000
    rows = [{"ts": now - 60, "type": "settle", "data": {"order": "o1", "buyer": "someone", "own_owner": True}},
            {"ts": now - 50, "type": "settle", "data": {"order": "o2", "buyer": "x", "own_owner": False}}]
    (tmp_path / ".r3/events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    code = "def run(load, args):\n    return [[r['data']['order'], r['data']['side'], 'own_owner' in r['data']] for r in load('events', 1)]"
    out = ct.execute(str(tmp_path), code, now=now)
    assert json.loads(out) == [["o1", "shop", False], ["o2", None, False]]
