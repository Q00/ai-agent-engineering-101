"""Auditor (meta-harness over the operator model) on synthetic roots: due(), prompt blocks and cap, guidance guards and
judging, tool list guards, model failures, corrupt files. kernel.audit and kernel.made_tools are faked."""

import json
import os
import types

import pytest
from kernel import auditor as au
from kernel import common as c
from kernel.llm import ModelError
from kernel.trajectory import Trajectory

NOW = 1_794_000_000.0
H = 3600


class Script:
    """Model stand-in: answers the queued replies in order (str as is, dict as JSON, exception raised)."""

    def __init__(self, *acts):
        self.acts, self.prompts, self.agents = list(acts), [], []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        self.agents.append(agent)
        a = self.acts.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


class FakeAudit:
    """kernel.audit stand-in: report() wakes and score() values set per test."""

    def __init__(self):
        self.wakes, self.scores, self.calls = 5, [], []

    def report(self, root, now, hours=24, since=None, listed=None):
        self.calls.append(since)
        return {
            "wakes": self.wakes,
            "calls": 9,
            "idle_wakes": 0,
            "repeat_calls": 2,
            "tools": {},
            "edits": {"asked": 0},
        }

    def score(self, rep):
        s = (
            self.scores.pop(0)
            if self.scores
            else {"mistakes_per_wake": 1.0, "idle_share": 0.2}
        )
        return dict(s, hit_rate=None, wakes=rep["wakes"])

    def lines(self, root, now, hours=24, listed=None):
        return ["[감사] 점검 5회, 반복 호출 2건 AUDITLINE"]

    def tool_table(self, root, now, hours=24, listed=None):
        return [
            {
                "name": n,
                "uses": 1,
                "wakes": 1,
                "obs_chars_mean": 100,
                "repeats": 0,
                "listed": True,
            }
            for n in listed or ()
        ]


class FakeMade:
    def __init__(self, names=("q_sales",)):
        self.names, self.removed = list(names), []

    def load(self, root):
        return [{"name": n} for n in self.names]

    def listing(self, root):
        return [(n, f"{n} 설명") for n in self.names]

    def remove(self, root, name, now, traj=None, by="auditor"):
        self.removed.append(name)
        self.names.remove(name)
        return {"ok": True}


LISTED = [
    "board",
    "ledger",
    "events",
    "policy",
    "brief_section",
    "guide",
    "recall",
    "q_sales",
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "root")
    os.makedirs(os.path.join(root, "a2"))
    fa, fm = FakeAudit(), FakeMade()
    monkeypatch.setattr(au, "audit", fa)
    monkeypatch.setattr(au, "made_tools", fm)
    monkeypatch.delenv("A2_AUDITOR_INTERVAL_S", raising=False)
    traj = Trajectory(os.path.join(root, au.TRAJ))
    return types.SimpleNamespace(root=root, audit=fa, made=fm, traj=traj)


def _row(root, **row):
    with open(os.path.join(root, au.TRAJ), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _wake(root, ts, note="요약", tools=("policy",), policy=None):
    for t in tools:
        _row(
            root,
            ts=ts - 1,
            agent="operator",
            kind="tool",
            name=t,
            args="{}",
            obs_chars=10,
        )
    _row(
        root,
        ts=ts,
        agent="operator",
        kind="operator_wake",
        status="done",
        result={"note": note, "calls": 2, "prompt_chars": 1000, "policy": policy or []},
    )


def _state(root, **st):
    json.dump(st, open(os.path.join(root, au.STATE), "w", encoding="utf-8"))


def _ledger(root, rows):
    json.dump(
        rows, open(os.path.join(root, "a2/experiments.json"), "w", encoding="utf-8")
    )


def _rows(root, kind):
    return [
        r
        for r in c.tail_json(os.path.join(root, au.TRAJ), 10**7)
        if r.get("kind") == kind
    ]


def _run(env, *acts, listed=LISTED, secrets=()):
    t = Script(*acts)
    return au.run(env.root, t, env.traj, NOW, secrets=secrets, listed=listed), t


# ------------------------------------------------------------ due
def test_due_first_run_interval_and_verdicts(env, monkeypatch):
    assert au.due(env.root, NOW) == "첫 감사"
    _state(env.root, last_run_ts=NOW - 3 * H)
    assert au.due(env.root, NOW) is None
    _ledger(
        env.root,
        [{"id": i, "status": "kept", "ended_ts": NOW - H} for i in range(3)]
        + [
            {"id": 9, "status": "running"},
            {"id": 8, "status": "reverted", "ended_ts": NOW - 4 * H},
        ],
    )
    assert au.due(env.root, NOW) == "실험 판정 3건"
    _state(
        env.root, last_run_ts=NOW - 1.5 * H
    )  # verdicts after the run but inside MIN_GAP_S
    assert au.due(env.root, NOW) is None
    _state(env.root, last_run_ts=NOW - 6 * H)
    assert au.due(env.root, NOW) == "주기 감사"
    monkeypatch.setenv("A2_AUDITOR_INTERVAL_S", str(10 * H))
    _ledger(env.root, [])
    assert au.due(env.root, NOW) is None


# ------------------------------------------------------------ prompt
def test_prompt_has_every_block_is_masked_and_capped(env):
    _wake(
        env.root,
        NOW - 2 * H,
        note="첫 점검 SECRETLOGIN " + "가" * 600,
        tools=("policy", "board"),
        policy=[{"ok": True, "key": "price_mult"}, {"ok": False, "error": "막힘"}],
    )
    _wake(env.root, NOW - H, note="둘째 점검 https://example.com/x")
    open(os.path.join(env.root, au.NOTES), "w", encoding="utf-8").write(
        "메모 NOTEHEAD " + "나" * 5000
    )
    open(os.path.join(env.root, au.GUIDANCE), "w", encoding="utf-8").write(
        "지금 안내 GUIDEHEAD"
    )
    _state(
        env.root,
        history=[
            {
                "id": 1,
                "ts": NOW - 5 * H,
                "text": "옛 안내",
                "status": "kept",
                "before": {"mistakes_per_wake": 1},
                "after": {"mistakes_per_wake": 0.5},
            }
        ],
    )
    _ledger(
        env.root,
        [
            {
                "id": 1,
                "status": "kept",
                "ended_ts": NOW - H,
                "section": "shop",
                "key": "x",
            }
        ],
    )
    p = au.build_prompt(env.root, NOW, secrets=("SECRETLOGIN",), listed=LISTED)
    with open(au.JOB, encoding="utf-8") as f:
        assert f.read()[:40] in p
    for s in (
        "## 감사 보고",
        "AUDITLINE",
        "## 감사 숫자",
        '"repeat_calls":2',
        "## 도구 표",
        "q_sales 설명",
        "## 실험 기록",
        "[실험]",
        "hit_rate",
        "## 최근 점검",
        "policy, board",
        '"key": "price_mult"',
        "막힘",
        "## 운영자 메모",
        "NOTEHEAD",
        "## 지금 안내",
        "GUIDEHEAD",
        "## 안내 이력",
        "옛 안내",
        "## 답 형식",
        '"findings"',
    ):
        assert s in p, s
    assert '"idle_wakes"' not in p  # zero fields dropped
    assert "SECRETLOGIN" not in p and "example.com" not in p and "[OWNER]" in p
    assert len(p) <= au.PROMPT_CHARS
    assert "나" * 801 not in p  # notes shown up to 800 chars
    open(os.path.join(env.root, au.GUIDANCE), "w", encoding="utf-8").write("다" * 1500)
    env.audit.lines = lambda *a, **k: ["라" * 20_000]
    p = au.build_prompt(env.root, NOW, listed=LISTED)
    assert (
        len(p) <= au.PROMPT_CHARS
        and "[잘림]" in p
        and "## 답 형식" in p
        and "GUIDEHEAD" not in p
    )


def test_run_uses_auditor_agent_and_records_row(env):
    _wake(env.root, NOW - H)
    res, t = _run(env, {"findings": ["반복 호출 2건"], "note": "한 줄"})
    assert (
        t.agents == ["auditor"]
        and res["status"] == "done"
        and res["prompt_chars"] == len(t.prompts[0])
    )
    row = _rows(env.root, "auditor_run")[-1]
    assert (
        row["findings"] == ["반복 호출 2건"]
        and row["note"] == "한 줄"
        and row["reason"] == "첫 감사"
    )
    st = json.load(open(os.path.join(env.root, au.STATE)))
    assert st["last_run_ts"] == NOW and st["verdicts_seen"] == 0


# ------------------------------------------------------------ guidance
def test_guidance_accepted_and_versioned(env):
    _wake(env.root, NOW - H)
    res, _ = _run(
        env, {"guidance": "  open_frac 실험을 먼저 걺. 판정 난 실험을 다음 결정에 씀  "}
    )
    assert res["guidance_changed"]
    assert au.guidance(env.root).startswith("open_frac 실험을 먼저 걺")
    h = json.load(open(os.path.join(env.root, au.STATE)))["history"]
    assert (
        len(h) == 1
        and h[0]["status"] == "active"
        and h[0]["prev_text"] == ""
        and h[0]["before"]["wakes"] == 5
    )
    assert (
        env.audit.calls[-1] == NOW - 24 * H
    )  # before window: 24 h when there is no previous version


@pytest.mark.parametrize(
    "text",
    [
        "open_frac를 0.5로 올림",
        "price_mult 0.73",
        "min_margin → 0.05",
        "먼저 확인. fresh_discount.B는 0.1이 좋음",
        "accept_c.max_pay_frac을 -> 0.7",
    ],
)
def test_guidance_refused_when_it_tells_a_value(env, text):
    _wake(env.root, NOW - H)
    res, _ = _run(env, {"guidance": text})
    assert not res["guidance_changed"] and "refused" in res["guidance"]
    assert au.guidance(env.root) == ""


def test_tells_value_allows_key_names_without_numbers():
    assert not au.tells_value("open_frac 실험을 먼저 걺")
    assert not au.tells_value("price_mult와 min_margin을 둘 다 policy 도구로 확인")
    assert not au.tells_value(
        "price_mult 확인\n2. 다음 줄"
    )  # a number on the next line is not attached
    assert au.tells_value("stock_target 값은 3") and au.tells_value("`price_mult` 0.73")
    assert not au.tells_value("price_mult 실험을 3건 걸 것")  # the number belongs to another word
    assert not au.tells_value("max_rounds 확인 뒤 2단계로")


def test_guidance_unchanged_is_noop_and_long_text_cut(env):
    open(os.path.join(env.root, au.GUIDANCE), "w", encoding="utf-8").write("같은 안내")
    _wake(env.root, NOW - H)
    res, _ = _run(env, {"guidance": "같은 안내\n"})
    assert not res["guidance_changed"] and res["guidance"].get("unchanged")
    assert json.load(open(os.path.join(env.root, au.STATE)))["history"] == []
    _wake(env.root, NOW + 10)
    t = Script({"guidance": "가" * 3000})
    res = au.run(env.root, t, env.traj, NOW + 20, listed=LISTED)
    assert res["guidance_changed"] and len(au.guidance(env.root)) == au.GUIDANCE_CHARS


def test_new_guidance_supersedes_active_and_history_capped(env):
    hist = [
        {
            "id": i,
            "ts": NOW - (20 - i) * H,
            "text": f"t{i}",
            "status": "kept",
            "before": {},
        }
        for i in range(12)
    ]
    hist[-1]["status"] = "active"
    _state(env.root, last_run_ts=NOW - 7 * H, history=hist)
    env.audit.wakes = 2  # too few to judge
    _wake(env.root, NOW - H)
    res, _ = _run(env, {"guidance": "새 안내"})
    h = json.load(open(os.path.join(env.root, au.STATE)))["history"]
    assert res["guidance_changed"] and len(h) == au.HISTORY_MAX
    assert (
        h[-2]["id"] == 11
        and h[-2]["status"] == "superseded"
        and h[-1]["status"] == "active"
    )
    assert (
        env.audit.calls[-1] == NOW - 9 * H
    )  # before window starts at the previous version


def _active(env, before, prev="옛 안내"):
    open(os.path.join(env.root, au.GUIDANCE), "w", encoding="utf-8").write("새 안내")
    _state(
        env.root,
        last_run_ts=NOW - 7 * H,
        history=[
            {"id": 1, "ts": NOW - 30 * H, "text": prev, "status": "kept", "before": {}},
            {
                "id": 2,
                "ts": NOW - 6 * H,
                "text": "새 안내",
                "prev_text": prev,
                "before": dict({"wakes": au.MIN_WAKES}, **before),
                "status": "active",
            },
        ],
    )


@pytest.mark.parametrize(
    "after,status",
    [
        (
            {"mistakes_per_wake": 3.0, "idle_share": 0.2},
            "kept",
        ),  # 2.0 * 1.25 + 0.5 = 3.0, not above
        ({"mistakes_per_wake": 3.01, "idle_share": 0.2}, "reverted"),
        (
            {"mistakes_per_wake": 1.0, "idle_share": 0.45},
            "kept",
        ),  # 0.2 + 0.25, not above
        ({"mistakes_per_wake": 1.0, "idle_share": 0.46}, "reverted"),
    ],
)
def test_judge_guidance_thresholds(env, after, status):
    _active(env, {"mistakes_per_wake": 2.0, "idle_share": 0.2})
    env.audit.scores = [after]
    v = au.judge_guidance(env.root, NOW, env.traj)
    assert (
        v["status"] == status
        and v["after"]["mistakes_per_wake"] == after["mistakes_per_wake"]
    )
    assert env.audit.calls[-1] == NOW - 6 * H
    row = _rows(env.root, "auditor_guidance")[-1]
    assert row["action"] == status and row["before"]["mistakes_per_wake"] == 2.0
    assert au.guidance(env.root) == ("옛 안내" if status == "reverted" else "새 안내")
    assert (
        json.load(open(os.path.join(env.root, au.STATE)))["history"][-1]["status"]
        == status
    )


def test_judge_waits_for_min_wakes_and_new_text_after_revert(env):
    _active(env, {"mistakes_per_wake": 0.0, "idle_share": 0.0})
    env.audit.wakes = au.MIN_WAKES - 1
    assert au.judge_guidance(env.root, NOW, env.traj) is None
    env.audit.wakes = au.MIN_WAKES
    env.audit.scores = [{"mistakes_per_wake": 2.0, "idle_share": 0.0}]
    _wake(env.root, NOW - H)
    res, _ = _run(env, {"guidance": "다른 안내"})
    h = json.load(open(os.path.join(env.root, au.STATE)))["history"]
    assert res["judged"]["status"] == "reverted" and res["guidance_changed"]
    assert (
        h[-2]["status"] == "reverted"
        and h[-1]["prev_text"] == "옛 안내"
        and au.guidance(env.root) == "다른 안내"
    )


# ------------------------------------------------------------ tools
def test_tool_guards(env):
    _wake(env.root, NOW - H)
    res, _ = _run(
        env,
        {
            "tools": {
                "hide": ["policy", "q_sales", "nope", "events"],
                "show": ["board"],
                "describe": {"ledger": "  장부  설명 " + "x" * 300, "nope": "x"},
                "remove": ["board", "q_missing"],
            }
        },
    )
    ap = {(a["op"], a["name"]) for a in res["tools"]["applied"]}
    rf = {(r["op"], r["name"]) for r in res["tools"]["refused"]}
    assert ap == {("hide", "events"), ("describe", "ledger")}
    assert rf == {
        ("hide", "policy"),
        ("hide", "q_sales"),
        ("hide", "nope"),
        ("show", "board"),
        ("describe", "nope"),
        ("remove", "board"),
        ("remove", "q_missing"),
    }
    ts = au.tool_state(env.root)
    assert (
        ts["hidden"] == ["events"]
        and ts["desc"]["ledger"].startswith("장부 설명")
        and len(ts["desc"]["ledger"]) == 160
    )


def test_tool_show_remove_and_cap(env):
    json.dump(
        {"hidden": ["recall"], "desc": {"q_sales": "x"}},
        open(os.path.join(env.root, au.TOOL_STATE), "w"),
    )
    _wake(env.root, NOW - H)
    res, _ = _run(
        env,
        {
            "tools": {
                "hide": ["board", "ledger", "events"],
                "show": ["recall"],
                "remove": ["q_sales"],
            }
        },
    )
    assert [a["name"] for a in res["tools"]["applied"]] == [
        "board",
        "ledger",
        "events",
        "recall",
    ]
    assert res["tools"]["refused"] == [
        {"op": "remove", "name": "q_sales", "why": "한 감사에 도구 변경 4개까지"}
    ]
    assert env.made.removed == []
    _wake(env.root, NOW + 10)
    res = au.run(
        env.root,
        Script({"tools": {"remove": ["q_sales"]}}),
        env.traj,
        NOW + 20,
        listed=LISTED,
    )
    assert res["tools"]["applied"] == [
        {"op": "remove", "name": "q_sales"}
    ] and env.made.removed == ["q_sales"]
    assert au.tool_state(env.root) == {
        "hidden": ["board", "ledger", "events"],
        "desc": {},
    }


# ------------------------------------------------------------ failures
@pytest.mark.parametrize("reply", [ModelError("timeout"), "no json here"])
def test_model_or_parse_error_applies_nothing(env, reply):
    _wake(env.root, NOW - H)
    res, _ = _run(env, reply)
    assert res["status"] == "model_error" and not res["guidance_changed"]
    assert not os.path.exists(os.path.join(env.root, au.GUIDANCE))
    assert not os.path.exists(os.path.join(env.root, au.TOOL_STATE))
    assert _rows(env.root, "auditor_run")[-1]["status"] == "model_error"


def test_skipped_without_wakes_since_last_run(env):
    _wake(env.root, NOW - 8 * H)
    _state(env.root, last_run_ts=NOW - 7 * H)
    t = Script()
    res = au.run(env.root, t, env.traj, NOW, listed=LISTED)
    assert res["status"] == "skipped" and t.prompts == []


def test_corrupt_files_and_missing_siblings(env, monkeypatch):
    for rel in (au.STATE, au.TOOL_STATE, "a2/experiments.json"):
        open(os.path.join(env.root, rel), "w").write("{not json")
    os.makedirs(os.path.join(env.root, au.GUIDANCE))  # unreadable as a file
    assert au.tool_state(env.root) == {"hidden": [], "desc": {}}
    assert au.guidance(env.root) == "" and au.due(env.root, NOW) == "첫 감사"
    json.dump(
        {"hidden": "x", "desc": ["y"]}, open(os.path.join(env.root, au.TOOL_STATE), "w")
    )
    assert au.tool_state(env.root) == {"hidden": [], "desc": {}}
    json.dump(
        {"history": "bad", "last_run_ts": "x"},
        open(os.path.join(env.root, au.STATE), "w"),
    )
    assert au.judge_guidance(env.root, NOW, env.traj) is None
    monkeypatch.setattr(au, "audit", None)
    monkeypatch.setattr(au, "made_tools", None)
    _wake(env.root, NOW - H)
    res = au.maybe_run(
        env.root,
        Script({"note": "ok", "tools": {"remove": ["q_sales"]}}),
        env.traj,
        NOW,
        listed=LISTED,
    )
    assert res["status"] == "done" and res["tools"]["refused"][0]["name"] == "q_sales"


def test_maybe_run_not_due_forced_and_never_raises(env, monkeypatch):
    _wake(env.root, NOW - H)
    _state(env.root, last_run_ts=NOW - H)
    assert au.maybe_run(env.root, Script(), env.traj, NOW, listed=LISTED) is None
    res = au.maybe_run(
        env.root, Script({"note": "강제"}), env.traj, NOW, listed=LISTED, force=True
    )
    assert res["status"] == "done" and res["reason"] == "수동 실행"

    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("kaboom")

    _state(env.root)
    res = au.maybe_run(env.root, Boom(), env.traj, NOW, listed=LISTED)
    assert res["status"] == "model_error" and "kaboom" in res["error"]
    monkeypatch.setattr(au, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(au, "due", lambda *a, **k: "주기 감사")
    res = au.maybe_run(env.root, Boom(), env.traj, NOW + 1, listed=LISTED)
    assert res["status"] == "crash" and _rows(env.root, "auditor_run")[-1]["status"] == "crash"
    assert au.maybe_run(env.root, Boom(), env.traj, NOW + 60, listed=LISTED) is None  # backoff after a crash
    assert au.maybe_run(env.root, Boom(), env.traj, NOW + 2 + au.MIN_GAP_S, listed=LISTED)["status"] == "crash"



def test_verifier_round_fixes(env):
    """describe cannot carry a value; a baseline with too few wakes is never reverted; a future stamp is 'first run'."""
    _wake(env.root, NOW - H)
    res, _ = _run(env, {"tools": {"describe": {LISTED[0]: "price_mult를 0.73으로 걸 것"}}})
    assert not res["tools"]["applied"] and res["tools"]["refused"][0]["op"] == "describe"
    _active(env, {"mistakes_per_wake": 0.0, "idle_share": 0.0, "wakes": 0})
    env.audit.wakes = au.MIN_WAKES
    env.audit.scores = [{"mistakes_per_wake": 9.0, "idle_share": 1.0}]
    assert au.judge_guidance(env.root, NOW, env.traj)["status"] == "kept"
    _state(env.root, last_run_ts=NOW + 10**9)
    assert au.due(env.root, NOW) == "첫 감사"


def test_auditor_remembers_its_runs_and_reads_them_back(env):
    _wake(env.root, NOW - H)
    _run(env, {"findings": ["반복 호출 2건"], "note": "첫 메모", "guidance": "읽은 숫자를 근거에 씀",
               "tools": {"describe": {"events": "범위를 좁혀 부름"}}})
    st = json.load(open(os.path.join(env.root, au.STATE)))
    run = st["runs"][-1]
    assert run["findings"] == ["반복 호출 2건"] and run["guidance_changed"] and "wakes" in run["nums"]
    assert run["tools"][0]["name"] == "events" and set(run["tools"][0]["before"]) == {"uses", "errors", "uncited", "cut"}
    assert st["history"][-1]["status"] == "active"
    p = au.build_prompt(env.root, NOW + H, listed=LISTED)
    assert "네 지난 감사" in p and "짚은 것: 반복 호출 2건" in p and "도구 describe events" in p and "첫 메모" in p
    st["runs"] = "깨짐"
    json.dump(st, open(os.path.join(env.root, au.STATE), "w"))
    assert "없음 (첫 감사)" in au.build_prompt(env.root, NOW + H, listed=LISTED)
