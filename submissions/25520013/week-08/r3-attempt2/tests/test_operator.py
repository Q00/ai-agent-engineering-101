"""Step 4: harness versions, breakage revert, per-version results, operator wake conditions and tool loop.

Fake Terra and fixture files only: no model, no market, no network.
"""

import json
import os
import pathlib
import shutil
import sys
import time

import pytest
from kernel import brief, tools, versions
from kernel import operator as op
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN = "secret-login"
NOW = 1_794_000_000.0


class FakeTerra:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def _jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _rows(root, kind=None):
    path = os.path.join(root, "a2/trajectory.jsonl")
    if not os.path.exists(path):
        return []
    rows = [json.loads(x) for x in open(path, encoding="utf-8")]
    return [r for r in rows if kind is None or r.get("kind") == kind]


@pytest.fixture
def hroot(tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(os.path.join(HERE, "harness"), proj / "harness")
    return str(proj / "harness")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    (r / ".market").mkdir(parents=True)
    (r / ".r3").mkdir()
    (r / "a2/board").mkdir(parents=True)
    (r / "a2/LIVE").write_text("")  # these tests run attempt 2 live; the LIVE-off path is in test_policy_edit.py
    (r / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    (r / ".market/catalog.json").write_text(
        json.dumps(
            {
                "BED1": {"category": "beds", "list_price": 1000},
                "SOFA1": {"category": "sofas", "price": 500},
            }
        )
    )
    (r / ".market/inventory.json").write_text(
        json.dumps(
            {
                "balance": 1e7,
                "bankrupt_line": 5e5,
                "units": [{"unit": "u1", "sku": "BED1", "grade": "A"}],
            }
        )
    )
    (r / ".market/shops.json").write_text(
        json.dumps(
            [
                {"name": "LSS", "owner": LOGIN, "url": "https://x.example/me"},
                {"name": "A", "owner": "rival-a", "url": "https://x.example/a"},
            ]
        )
    )
    _board(str(r), NOW - 60, my_total=1.5)
    return str(r)


def _board(root, ts, my_total):
    players = [
        {"owner": o, "player": o, "total": t}
        for o, t in (
            ("rival-a", 1.7),
            ("rival-b", 1.6),
            ("rival-c", 1.4),
            ("rival-d", 1.3),
            (LOGIN, my_total),
        )
    ]
    with open(os.path.join(root, f"a2/board/{int(ts)}.json"), "w") as f:
        json.dump({"players": players}, f)


def _tree(root):
    return {
        os.path.relpath(os.path.join(top, f), root)
        for top, _, files in os.walk(root)
        for f in files
    }


def _read(hroot, agent, rel):
    with open(os.path.join(hroot, agent, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- apply_edit and revert
@pytest.mark.parametrize(
    "files",
    [
        {"../evil.md": "x"},
        {"/etc/evil.md": "x"},
        {"notes.txt": "x"},
        {"other/x.md": "x"},
        {"routing.json": "{not json"},
        {"scripts/s.py": "def broken(:\n"},
        {"systemprompt.md": "x" * 9000},
        {
            "routing.json": json.dumps(
                {"seller.open": {"by": "script", "script": "missing.py"}}
            )
        },
        {"VERSION": "99"},
    ],
)
def test_apply_edit_rejects(hroot, files):
    before = _read(hroot, "seller", "systemprompt.md")
    ok, info = versions.apply_edit(hroot, "seller", files, "test", now=NOW)
    assert not ok and info["errors"]
    assert _read(hroot, "seller", "VERSION").strip() == "1"
    assert _read(hroot, "seller", "systemprompt.md") == before
    assert not [n for n in os.listdir(hroot) if n.startswith(".")]


def test_apply_edit_accepts_stores_and_reverts(hroot):
    v1 = _read(hroot, "seller", "systemprompt.md")
    ok, info = versions.apply_edit(
        hroot,
        "seller",
        {"systemprompt.md": "# 새 판\n", "scripts/s.py": "print('{}')\n"},
        "근거 있음",
        now=NOW,
    )
    assert ok and info["version"] == 2
    assert _read(hroot, "seller", "VERSION").strip() == "2"
    lib = versions.library_dir(hroot)
    assert open(os.path.join(lib, "harnesses/seller/1/systemprompt.md")).read() == v1
    assert os.path.isfile(os.path.join(lib, "harnesses/seller/2/scripts/s.py"))
    index = open(os.path.join(lib, "INDEX.md")).read()
    assert "seller 판 1" in index and "seller 판 2" in index and "근거 있음" in index
    ok, info = versions.revert(hroot, "seller", 1, now=NOW + 1)
    assert ok and info["version"] == 3 and info["restored_from"] == 1
    assert _read(hroot, "seller", "systemprompt.md") == v1
    assert not os.path.exists(os.path.join(hroot, "seller/scripts/s.py"))
    st = versions.load_state(hroot)["seller"]
    assert st["current"] == 3 and [h["version"] for h in st["history"]] == [1, 2, 3]
    assert not versions.revert(hroot, "seller", 42)[0]


def _decisions(root, agent, version, n, broken=True, errors=None, ts=None):
    t = ts or time.time()
    _jsonl(
        os.path.join(root, "a2/trajectory.jsonl"),
        [
            {
                "ts": t + i * 0.001,
                "agent": agent,
                "kind": "decision",
                "decision_kind": "seller.open",
                "harness_version": version,
                "route_used": "safe" if broken else "model",
                "errors": (errors or ["model: ParseError: no JSON object in output"])
                if broken
                else [],
                "decision": {"action": "decline"},
                "memory_keys": ["BED1"],
            }
            for i in range(n)
        ],
    )


def test_breakage_auto_revert(root, hroot):
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    _decisions(root, "seller", 1, 5, broken=False)
    assert versions.check_breakage(root, hroot, traj, 5) == {}
    ok, _ = versions.apply_edit(
        hroot, "seller", {"systemprompt.md": "# 깨진 판\n"}, "x", now=NOW
    )
    assert ok
    _decisions(root, "seller", 2, 4)
    assert versions.check_breakage(root, hroot, traj, 5) == {}  # 4 < n
    _decisions(root, "seller", 2, 5, errors=["model: ModelRefused: subscription limit"])
    assert (
        versions.check_breakage(root, hroot, traj, 5) == {}
    )  # quota is not the harness's fault
    _decisions(root, "seller", 2, 5, errors=["model: ModelError: timeout"])
    out = versions.check_breakage(root, hroot, traj, 5)
    assert out["seller"]["version"] == 3 and out["seller"]["restored_from"] == 1
    assert _read(hroot, "seller", "systemprompt.md") != "# 깨진 판\n"
    rev = _rows(root, "harness_revert")
    assert (
        len(rev) == 1 and rev[0]["agent"] == "operator" and rev[0]["target"] == "seller"
    )
    assert rev[0]["version"] == 3 and rev[0]["restored_from"] == 1


def test_breakage_n_is_bounded():
    assert versions.bound_breakage_n(1) == 3 and versions.bound_breakage_n(999) == 50
    assert versions.bound_breakage_n("x") == versions.BREAKAGE_DEFAULT


# ---------------------------------------------------------------- per-version results
def _version_fixture(root):
    t0 = NOW - 3600
    path = os.path.join(root, "a2/trajectory.jsonl")
    _jsonl(
        path,
        [
            {
                "ts": t0 - 100,
                "agent": "seller",
                "kind": "decision",
                "decision_kind": "seller.open",
                "harness_version": 1,
                "route_used": "model",
                "errors": [],
                "decision": {"action": "decline"},
            }
        ],
    )
    _jsonl(
        path,
        [
            {
                "ts": t0,
                "agent": "operator",
                "kind": "harness_edit",
                "target": "seller",
                "version": 2,
                "files": ["systemprompt.md"],
                "reason": "r",
            }
        ],
    )
    acts = ["offer", "offer", "offer", "decline"]
    _jsonl(
        path,
        [
            {
                "ts": t0 + 10 + i,
                "agent": "seller",
                "kind": "decision",
                "decision_kind": "seller.open",
                "harness_version": 2,
                "route_used": "model",
                "errors": [],
                "decision": {"action": a},
            }
            for i, a in enumerate(acts)
        ],
    )
    _jsonl(
        path,
        [
            {
                "ts": t0 + 20,
                "agent": "seller",
                "kind": "decision",
                "decision_kind": "seller.respond",
                "harness_version": 2,
                "decision": {"action": "accept"},
            }
        ],
    )
    _jsonl(
        path,
        [
            {
                "ts": t0 + 30,
                "agent": "buyer",
                "kind": "buyer_exec",
                "event": "attempt",
                "order": "o1",
                "sku": "SOFA1",
                "result": "accepted",
            },
            {
                "ts": t0 + 31,
                "agent": "buyer",
                "kind": "buyer_exec",
                "event": "outcome",
                "order": "o1",
                "deal": True,
                "price": 400,
            },
            {
                "ts": t0 + 40,
                "agent": "buyer",
                "kind": "buyer_exec",
                "event": "outcome",
                "order": "o2",
                "deal": False,
                "price": None,
            },
        ],
    )
    ev = [
        {
            "type": "settle",
            "src": "market",
            "ts": t0 + 50,
            "data": {
                "sku": "BED1",
                "amount": 800,
                "own_owner": True,
                "buyer": "rival-x",
            },
        },
        {
            "type": "settle",
            "src": "market",
            "ts": t0 + 60,
            "data": {
                "sku": "BED1",
                "amount": 900,
                "own_owner": True,
                "buyer": "rival-y",
            },
        },
        {
            "type": "settle",
            "src": "market",
            "ts": t0 + 70,
            "data": {"sku": "BED1", "amount": 100, "own_owner": True, "buyer": LOGIN},
        },
        {
            "type": "settle",
            "src": "market",
            "ts": t0 + 80,
            "data": {
                "sku": "BED1",
                "amount": 50,
                "own_owner": False,
                "buyer": "rival-z",
            },
        },
        {
            "type": "settle",
            "src": "market",
            "ts": t0 - 50,
            "data": {
                "sku": "BED1",
                "amount": 1000,
                "own_owner": True,
                "buyer": "rival-q",
            },
        },
        {
            "type": "review",
            "src": "market",
            "role": "shop",
            "mine": True,
            "ts": t0 + 90,
            "data": {"stars": 4},
        },
        {
            "type": "review",
            "src": "market",
            "role": "shop",
            "mine": True,
            "ts": t0 + 91,
            "data": {"stars": 5},
        },
        {
            "type": "review",
            "src": "market",
            "role": "buyer",
            "mine": True,
            "ts": t0 + 92,
            "data": {"stars": 1},
        },
    ]
    _jsonl(os.path.join(root, ".r3/events.jsonl"), ev)
    return t0


def test_version_results_math(root):
    t0 = _version_fixture(root)
    (cur,) = brief.version_results(root, "seller", NOW, last=1)
    assert cur["version"] == 2 and cur["since_ts"] == t0 and cur["until_ts"] == NOW
    m = cur["metrics"]
    assert (m["requests"], m["offers"], m["declines"], m["sales"]) == (
        4,
        3,
        1,
        2,
    )  # our purchase excluded
    assert m["conversion"] == 0.5 and m["conversion_se"] == 0.25
    assert (
        m["price_to_list_median"] == pytest.approx(0.85) and m["price_to_list_n"] == 2
    )
    assert m["stars_mean"] == 4.5 and m["stars_n"] == 2
    prev, cur = brief.version_results(root, "seller", NOW)
    assert prev["version"] == 1 and not prev["since_known"] and prev["until_ts"] == t0
    assert prev["metrics"]["requests"] == 1 and prev["metrics"]["sales"] == 1
    (buyer,) = brief.version_results(root, "buyer", NOW)
    bm = buyer["metrics"]
    assert (bm["orders"], bm["deals"], bm["deal_rate"]) == (2, 1, 0.5)
    assert bm["price_to_list_median"] == pytest.approx(0.8)
    text = brief.build(root, now=NOW)
    assert (
        "[판별 성과 판매자] 지금 판 2" in text
        and "SE 0.250, n 4" in text
        and "별점 평균 4.50 (n 2)" in text
    )
    assert text.index("[판별 성과") < text.index("[가게 상태]")
    assert "[모델 호출 24시간]" in text and text.index(
        "[모델 호출 24시간]"
    ) < text.index("[가게 상태]")


def test_version_results_omit_stars_without_reviews(root):
    _version_fixture(root)
    lines = [
        ln
        for ln in open(os.path.join(root, ".r3/events.jsonl"))
        if '"review"' not in ln
    ]
    open(os.path.join(root, ".r3/events.jsonl"), "w").write("".join(lines))
    (cur,) = brief.version_results(root, "seller", NOW, last=1)
    assert "stars_mean" not in cur["metrics"]


# ---------------------------------------------------------------- tools
def test_operator_tools(root, hroot):
    _version_fixture(root)
    s = tools.call("sku_stats", "operator", root, now=NOW, hours=1)
    assert "판매 2" in s
    assert tools.call("sku_stats", "seller", root).startswith("seller는")
    versions.apply_edit(
        hroot, "seller", {"systemprompt.md": "# v2\n"}, "이유 하나", now=NOW
    )
    v = tools.call("versions", "operator", root, harness_root=hroot)
    assert "seller: 지금 판 2, 마지막 정상 판 1" in v and "이유 하나" in v
    t = tools.call(
        "trajectory", "operator", root, kind="harness_edit", hours=10, now=NOW
    )
    assert "1줄" in t
    assert "systemprompt.md" in tools.call(
        "harness_read", "operator", root, target="seller", harness_root=hroot
    )
    assert "path traversal" in tools.call(
        "harness_read",
        "operator",
        root,
        target="seller",
        path="../x.md",
        harness_root=hroot,
    )


def test_replay_probe_writes_only_under_probe(root, hroot):
    _jsonl(
        os.path.join(root, "logs/buyer_attempts.jsonl"),
        [{"event": "request", "text": f"bed please {i}"} for i in range(3)],
    )
    before = _tree(root)
    out = tools.call(
        "replay_probe",
        "operator",
        root,
        target="seller",
        files={"guardrails.md": "# 메모\n"},
        n=500,
        harness_root=hroot,
        dry=True,
    )
    assert "n 3" in out and "decline" in out
    after = _tree(root)
    assert before <= after and all(p.startswith("a2/probe/") for p in after - before)
    assert _read(hroot, "seller", "VERSION").strip() == "1"
    bad = tools.call(
        "replay_probe",
        "operator",
        root,
        target="seller",
        files={"../x.md": "x"},
        harness_root=hroot,
    )
    assert "형식 오류" in bad
    assert "a2/probe" in tools.call(
        "replay_probe",
        "operator",
        root,
        target="seller",
        harness_dir="/tmp",
        harness_root=hroot,
    )


# ---------------------------------------------------------------- operator
def _op(root, hroot, answers, dry=False):
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))
    terra = FakeTerra(answers)
    return op.Operator(
        root, terra, traj, harness_root=hroot, dry=dry, spawn=lambda *a, **k: None
    ), terra


def _valid_plan(root, until):
    with open(os.path.join(root, "a2/restock_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"buy": [], "valid_until": until}, f)


def test_wake_conditions(root, hroot):
    o, _ = _op(root, hroot, [])
    _valid_plan(root, NOW + 10 * 86400)
    assert o.wake_reason(NOW) == "주기 점검"
    o._save_state({"last_wake_ts": NOW, "last_rank": 5})
    assert o.wake_reason(NOW + 60) is None
    assert o.wake_reason(NOW + 1800) == "주기 점검"
    _decisions(root, "seller", 1, 150, broken=False, ts=NOW + 1)
    assert "판매 요청 150건" in o.wake_reason(NOW + 60)
    o._save_state({"last_wake_ts": NOW + 200, "last_rank": 5})
    assert o.wake_reason(NOW + 300) is None
    _board(root, NOW + 250, my_total=1.65)  # 5위 -> 2위
    assert "순위 5위에서 2위" in o.wake_reason(NOW + 300)
    o._save_state({"last_wake_ts": NOW + 200, "last_rank": 2})
    with open(os.path.join(root, ".market/shops.json"), "w") as f:
        json.dump([{"name": "A", "owner": "rival-a"}], f)
    assert "가게 목록에서 빠짐" in o.wake_reason(NOW + 300)
    o._save_state({"last_wake_ts": NOW + 200, "last_rank": 2, "shop_missing": True})
    with open(os.path.join(root, ".market/inventory.json"), "w") as f:
        json.dump({"balance": 9e5, "bankrupt_line": 5e5}, f)
    assert o.wake_reason(NOW + 300) == "잔고가 파산선 가까이 내려감"


def test_settings_are_bounded():
    s = op.bounded(
        {
            "interval_s": 10,
            "wake_after_requests": 10**9,
            "max_steps": 0,
            "breakage_n": True,
        }
    )
    assert (
        s["interval_s"] == 300
        and s["wake_after_requests"] == 5000
        and s["max_steps"] == 1
    )
    assert s["breakage_n"] == versions.BREAKAGE_DEFAULT


def test_tool_loop_then_done(root, hroot, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "kernel.forum", None
    )  # import fails -> forum unavailable
    new_prompt = "# 판매자\n\n새 판매자 설명서\n"
    answers = [
        '생각 중... ```json\n{"tool": "board", "args": {"top": 3}}\n```',
        {"tool": "versions", "args": {"harness_root": "/etc"}},
        "no json here",
        {
            "done": {
                "reflection": [
                    {
                        "target": "seller",
                        "version": 1,
                        "lesson": "작은 표본은 판단 불가",
                        "effects": [
                            {"edit": "첫 판", "effect": "모름", "evidence": "n 4"}
                        ],
                    }
                ],
                "edits": [
                    {
                        "target": "seller",
                        "files": {"systemprompt.md": new_prompt},
                        "reason": "궤적 근거",
                    },
                    {
                        "target": "buyer",
                        "files": {"routing.json": "{bad"},
                        "reason": "깨진 수정",
                    },
                ],
                "settings": {"interval_s": 60, "wake_after_requests": 200},
                "restock_plan": {
                    "buy": [{"sku": "BED1", "qty": 2}, {"sku": "X", "qty": 0}],
                    "valid_until": NOW + 3600,
                },
                "forum": [{"post": f"hello from {LOGIN}", "as": "shop"}],
                "memcheck": True,
                "note": "첫 점검",
            }
        },
        {"done": {"edits": []}},  # one retry after the format error, gives up
    ]
    o, terra = _op(root, hroot, answers)
    res = o.wake("주기 점검", now=NOW)
    assert res["status"] == "done" and res["steps"] == 4
    assert terra.answers == []
    assert "## 브리핑" in terra.prompts[0] and "# 운영자 에이전트" in terra.prompts[0]
    assert "형식 검사 오류" in terra.prompts[-1] and "routing.json" in terra.prompts[-1]
    assert _read(hroot, "seller", "systemprompt.md") == new_prompt
    assert _read(hroot, "buyer", "VERSION").strip() == "1"

    tool_rows = _rows(root, "tool")
    assert [r["name"] for r in tool_rows] == ["board", "versions"]
    assert "/etc" not in tool_rows[1]["args"] and all(
        isinstance(r["obs_chars"], int) for r in tool_rows
    )
    edits = _rows(root, "harness_edit")
    assert {(r["target"], r["version"]) for r in edits} == {
        ("seller", 2),
        ("operator", 2),
    }
    seller_edit = next(r for r in edits if r["target"] == "seller")
    assert set(seller_edit) == {
        "ts",
        "agent",
        "kind",
        "target",
        "version",
        "files",
        "reason",
    }
    assert (
        seller_edit["files"] == ["systemprompt.md"]
        and seller_edit["agent"] == "operator"
    )
    vr = _rows(root, "version_result")
    assert (
        len(vr) == 1
        and vr[0]["target"] == "seller"
        and vr[0]["version"] == 1
        and vr[0]["until_ts"] == NOW
    )
    assert set(vr[0]) == {
        "ts",
        "agent",
        "kind",
        "target",
        "version",
        "since_ts",
        "until_ts",
        "metrics",
    }
    (refl,) = _rows(root, "reflection")
    assert (
        refl["effects"][0]["effect"] == "판단 불가"
        and refl["lesson"] == "작은 표본은 판단 불가"
    )
    exp = os.path.join(versions.library_dir(hroot), "harnesses/seller/1/EXPERIENCE.md")
    assert "작은 표본은 판단 불가" in open(exp).read()
    assert (
        "교훈 있음"
        in open(os.path.join(versions.library_dir(hroot), "INDEX.md")).read()
    )
    s = json.load(open(os.path.join(hroot, "operator/settings.json")))
    assert s["interval_s"] == 60 and o.settings()["interval_s"] == 300
    plan = json.load(open(os.path.join(root, "a2/restock_plan.json")))
    assert (
        plan["buy"] == [{"sku": "BED1", "qty": 2}] and plan["valid_until"] == NOW + 3600
    )
    assert res["forum"]["status"] == "forum unavailable"
    assert res["memcheck"] == {"started": True}
    assert res["edit_errors"] and res["edits"] == [{"target": "seller", "version": 2}]
    (wake,) = _rows(root, "operator_wake")
    assert wake["status"] == "done"
    assert LOGIN not in open(os.path.join(root, "a2/trajectory.jsonl")).read()


def test_edit_retry_fixes_error(root, hroot):
    answers = [
        {
            "done": {
                "edits": [
                    {"target": "buyer", "files": {"memory.json": "{bad"}, "reason": "r"}
                ]
            }
        },
        {
            "done": {
                "edits": [
                    {
                        "target": "buyer",
                        "files": {"memory.json": '{"store": [], "top_k": 0}'},
                        "reason": "r2",
                    }
                ]
            }
        },
    ]
    o, _ = _op(root, hroot, answers)
    res = o.wake("test", now=NOW)
    assert (
        res["edits"] == [{"target": "buyer", "version": 2}] and res["edit_errors"] and res["retry_errors"] == []
    )


def test_forum_queue_called_when_available(root, hroot, monkeypatch):
    calls = []
    fake = type(sys)("kernel.forum")
    fake.queue = lambda r, posts: calls.append(posts) or [{"ok": True, "why": ""}]
    monkeypatch.setitem(sys.modules, "kernel.forum", fake)
    o, _ = _op(
        root,
        hroot,
        [
            {
                "done": {
                    "forum": [
                        {"post": "hi", "as": "buyer"},
                        {"post": "x", "as": "rival"},
                    ]
                }
            }
        ],
    )
    res = o.wake("test", now=NOW)
    assert calls == [[{"post": "hi", "as": "buyer"}]] and res["forum"]["queued"] == 1


def test_max_steps_forces_done(root, hroot):
    with open(os.path.join(hroot, "operator/settings.json"), "w") as f:
        json.dump({"max_steps": 2}, f)
    o, terra = _op(
        root, hroot, [{"tool": "board", "args": {}}, {"tool": "board", "args": {}}]
    )
    res = o.wake("test", now=NOW)
    assert res["status"] == "no_done" and len(_rows(root, "tool")) == 1
    assert "반드시 done" in terra.prompts[-1]


def test_main_dry_once(root, hroot, capsys):
    assert op.main(["--root", root, "--once", "--dry", "--harness", hroot]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "done" and out["edits"] == []


@pytest.mark.parametrize(
    "files",
    [
        {"a\u0000.md": "x"},
        {"a" * 300 + ".md": "x"},
        {"scripts/a.py": "x = 1\n", "scripts/a.py/b.py": "x = 1\n"},
    ],
)
def test_apply_edit_unwritable_names_are_errors_not_crashes(hroot, files):
    before = open(os.path.join(hroot, "seller/systemprompt.md"), encoding="utf-8").read()
    ok, info = versions.apply_edit(hroot, "seller", files, "r", now=NOW)
    assert not ok and info["errors"]
    assert open(os.path.join(hroot, "seller/systemprompt.md"), encoding="utf-8").read() == before


def test_apply_edit_does_not_follow_planted_symlink(hroot, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE TEXT")
    link = os.path.join(hroot, "seller/LongTermMEMORY.md")
    if os.path.exists(link):
        os.remove(link)
    os.symlink(outside, link)
    ok, info = versions.apply_edit(hroot, "seller", {"guardrails.md": "g\n"}, "r", now=NOW)
    assert not ok and any("symlink" in e for e in info["errors"])


def test_restock_guard_infinite_balance_buys_nothing():
    from kernel import restock

    inv = {"balance": float("inf"), "bankrupt_line": 0}
    cat = [{"sku": "S1", "cost": 10}]
    plan = {"buy": [{"sku": "S1", "qty": 3}]}
    assert restock.guard(plan, {"balance": 100, "bankrupt_line": 0}, cat)["buy"]  # finite balance does buy
    assert restock.guard(plan, inv, cat) == {"buy": []}


def test_missing_or_expired_restock_plan_wakes_every_ten_minutes(root, hroot):
    o, _ = _op(root, hroot, [])
    o._save_state({"last_wake_ts": NOW, "last_rank": 5})
    assert o.wake_reason(NOW + 60) is None  # no plan, but woke a minute ago
    assert o.wake_reason(NOW + 600) == "재입고 계획 없음 또는 만료"
    _valid_plan(root, NOW + 700)
    assert o.wake_reason(NOW + 650) is None
    assert o.wake_reason(NOW + 701) == "재입고 계획 없음 또는 만료"
    text = "\n".join(brief.section_restock(root, NOW + 701))
    assert "계획 만료" in text and "아무것도 사지 않음" in text


def test_restock_brief_shows_food_a_request_without_need_can_get(root):
    now = time.time()
    pathlib.Path(root, ".market/catalog.json").write_text(json.dumps({
        "KIM": {"name": "포기김치", "category": "식품>김치", "list_price": 300, "shelf_hours": 168},
        "APL": {"name": "사과", "category": "식품>과일", "list_price": 100, "shelf_hours": 48},
        "BED1": {"category": "beds", "list_price": 1000},
    }))
    units = [
        {"unit": f"k{i}", "sku": "KIM", "expires_at": now + h * 3600, "supplied_at": now - 3600}
        for i, h in enumerate([60, 70])
    ] + [
        {"unit": f"a{i}", "sku": "APL", "expires_at": now + h * 3600, "supplied_at": now - 3600}
        for i, h in enumerate([10, 30, 40])
    ] + [{"unit": "b1", "sku": "BED1"}, "junk"]
    pathlib.Path(root, ".market/inventory.json").write_text(json.dumps({"units": units, "balance": 1000}))
    rows = [
        {"ts": now - 60, "decision": "no_stock", "outcome": {"sku": "KIM", "why": "half_shelf", "matched": 2}},
        {"ts": now - 50, "decision": "no_stock", "outcome": {"sku": "KIM", "why": "half_shelf", "matched": 2}},
        {"ts": now - 40, "decision": "no_stock", "outcome": {"sku": "APL", "why": "need", "matched": 3}},
        {"ts": now - 30, "decision": "no_stock", "outcome": {"sku": "APL", "why": "half_shelf", "matched": 0}},
        {"ts": now - 20, "decision": "no_stock", "outcome": {"request": "old row"}},
        {"ts": now - 15, "decision": "no_stock", "outcome": {"sku": "KIM", "why": "half_shelf", "matched": 0}},
        {"ts": now - 14, "decision": "no_stock", "outcome": {"sku": "Greetings", "why": "half_shelf", "matched": 0}},
        {"ts": now - 13, "decision": "no_stock", "outcome": "string outcome"},
        {"ts": "bad", "decision": "no_stock", "outcome": {}},
        {"ts": now - 7200, "decision": "no_stock", "outcome": {"sku": "KIM", "why": "half_shelf", "matched": 2}},
    ]
    with open(os.path.join(root, ".r3/metrics.jsonl"), "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    text = "\n".join(brief.section_restock(root, now))
    assert "(남은 기한이 진열기한 50% 이상)/재고" in text
    assert "APL 사과 2/3 (공급 때 65%)" in text and "KIM 포기김치 0/2 (공급 때 39%)" in text
    assert "BED1" not in text.split("식품 재고")[1].split("\n")[0]
    assert "최근 1시간 재고 없음 거절 8건" in text
    assert "진열기한 절반 이상인 단위 없음 4 (KIM 3, APL 1)" in text
    assert "적힌 요구 시간을 넘기는 단위 없음 1 (APL 1)" in text
    assert "팔 수 있는 재고 없음(다른 협상에 묶인 것 포함) 1" in text and "포함) 1 (" not in text
    assert "사유 기록 전 2" in text and "Greetings" not in text


def test_restock_brief_says_when_the_plan_was_already_bought(root):
    # live 11-06 08:09 and 09:52: the operator rewrote a bought plan to keep it "valid" and it was bought again
    from kernel import restock

    _valid_plan(root, NOW + 3600)
    assert "이미 구매함" not in "\n".join(brief.section_restock(root, NOW))
    path = os.path.join(root, "a2/restock_plan.json")
    with open(os.path.join(root, "a2/restock_done.json"), "w", encoding="utf-8") as f:
        json.dump({"plan_id": restock.plan_id(path, json.load(open(path))), "ts": NOW, "buy": [{"sku": "S", "qty": 1}]}, f)
    text = "\n".join(brief.section_restock(root, NOW + 60))
    assert "이미 구매함 (1줄)" in text and "다시 쓰면 새 계획" in text
    time.sleep(0.01)
    _valid_plan(root, NOW + 3600)  # rewritten: a new plan, not bought yet
    assert "이미 구매함" not in "\n".join(brief.section_restock(root, NOW + 60))
