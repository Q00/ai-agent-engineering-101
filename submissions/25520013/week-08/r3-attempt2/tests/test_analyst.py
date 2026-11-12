"""The investigator (kernel.analyst) and its queue (kernel.questions): operators' done.ask, standing questions, a run
to a finding, refused shop changes, a failed run, the findings section and tool, and no busy lock."""

import json
import os

import pytest
from kernel import analyst as an
from kernel import audit, tools
from kernel import experiments as ex
from kernel import operator as op
from kernel import questions as qs
from kernel.trajectory import Trajectory
from tests.test_operator_roles import Script, _root

NOW = 1_794_000_000.0
H = 3600
ANSWER = {
    "finding": "C등급 식품 리뷰 n=12 평균 2.4",
    "evidence": ["별점 3점 이하 12건 중 C등급 9건, events"],
    "suggest": "defaults.food fresh_discount.C 올리기",
    "for": ["shop"],
}


class Rec(Script):
    """Script that also records the agent and effort of each call."""

    def __init__(self, *acts):
        super().__init__(*acts)
        self.calls = []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.calls.append((agent, effort))
        return super().complete(prompt, agent=agent, timeout_s=timeout_s, effort=effort)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    monkeypatch.setattr(an.Analyst, "_early_done", lambda self, obs_all: None)  # scripts here answer at step 1
    for k in (
        "A2_OPERATOR_INTERVAL_S",
        "A2_SHOP_LEAD_S",
        "A2_SPLIT",
        "A2_ANALYST_STEPS",
        "A2_ANALYST_EFFORT",
        "A2_ANALYST_MIN_GAP_S",
        "A2_OPERATOR_EFFORT",
    ):
        monkeypatch.delenv(k, raising=False)
    root = _root(tmp_path)
    harness = str(tmp_path / "noharness")
    monkeypatch.setattr(an, "JOB", str(tmp_path / "no_job.md"))  # no standing questions unless a test sets them

    def make(role):
        traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role=role)
        if role == "analyst":
            return an.Analyst(root, None, traj, harness_root=harness)
        return op.Operator(root, None, traj, harness_root=harness, role=role)

    return root, make


def _rows(root):
    p = os.path.join(root, "a2/trajectory.jsonl")
    return (
        [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []
    )


def _q(root, qid):
    return next(r for r in qs.load(root) if r["id"] == qid)


# ------------------------------------------------------------ asking
def test_ask_accepted_refused_and_duplicate(env):
    root, _ = env
    assert qs.ask(root, "shop", "C등급 리뷰는 어디서?", NOW) == {"ok": True, "id": 1}
    r = qs.ask(root, "shop", {"q": " C등급  리뷰는 어디서? "}, NOW)
    assert not r["ok"] and "#1" in r["error"]  # same question, whitespace aside
    assert qs.ask(root, "shop", {"q": "보증 환불은?"}, NOW)["ok"]
    assert qs.ask(root, "shop", "세 번째", NOW)["ok"]  # 11-11: no small open-question count
    long = qs.ask(root, "buyer", "가" * 5000, NOW)
    assert long["ok"] and len(_q(root, long["id"])["q"]) == qs.Q_CHARS  # cut silently, not refused
    assert not qs.ask(root, "buyer", ["a", "b"], NOW)["ok"]  # the operator sends each item of a list
    assert not qs.ask(root, "buyer", {"x": 1}, NOW)["ok"]
    assert qs.ask(root, "buyer", "구매 절약은?", NOW)["ok"]
    assert [(r["from"], r["for"], r["status"]) for r in qs.load(root)][:3] == [
        ("shop", ["shop"], "open"),
        ("shop", ["shop"], "open"),
        ("shop", ["shop"], "open"),
    ]
    r = qs.ask(root, "shop", "x " + "y" * 10, NOW, secrets=("y" * 10,))
    assert r["ok"] and "y" * 10 not in _q(root, r["id"])["q"]
    for i in range(qs.OPEN_PER_ROLE - 4):
        assert qs.ask(root, "shop", f"질문 {i}", NOW)["ok"]
    r = qs.ask(root, "shop", "하나 더", NOW)
    assert not r["ok"] and f"{qs.OPEN_PER_ROLE}개" in r["error"]  # the runaway backstop


def test_ask_result_reaches_the_next_wake(env):
    root, make = env
    shop = make("shop")
    shop.terra = Script(
        {"done": {"ask": "별점 낮은 구간은?", "note": "n"}},
        {"done": {"ask": "별점 낮은 구간은?"}},
        {"done": {"note": "z"}},
    )
    assert shop.wake("주기 점검", NOW)["ask"] == {"ok": True, "id": 1}
    shop.wake("주기 점검", NOW + 1200)
    assert "- 조사 질문: 접수됨 (#1," in shop.terra.prompts[1]
    shop.wake("주기 점검", NOW + 2400)
    assert (
        "- 조사 질문: 거절됨. 같은 질문이 이미 열려 있음 (#1)" in shop.terra.prompts[2]
    )
    assert '"ask": "<조사원에게 맡길 질문, 여럿이면 목록>"' in shop.terra.prompts[0]
    assert "ask는 조사원에게 질문을 맡김" in op.role_schema("buyer")
    shop.terra = Script({"done": {"ask": ["가게 질문 A", "가게 질문 B"]}})
    res = shop.wake("주기 점검", NOW + 3600)
    assert [x["ok"] for x in res["ask"]] == [True, True]  # several questions in one wake
    assert "ask" not in op.COMPACT_SCHEMA  # the single operator has no investigator
    assert "ask" not in make(None)._finish_compact({"ask": "x"}, NOW)


# ------------------------------------------------------------ standing questions
def test_standing_questions_by_interval_without_duplicates(env, monkeypatch, tmp_path):
    root, make = env
    assert qs.standing(open(os.path.join(op.HERE, "ANALYST_JOB.md"), encoding="utf-8").read()) == []  # 11-12: none shipped
    job = tmp_path / "JOB.md"
    job.write_text("- [standing 3h for=shop] 최근 24시간 별점 3점 이하 리뷰가 어디서 나오는지\n- [standing 6h for=buyer] 절약 비율\n", encoding="utf-8")
    monkeypatch.setattr(an, "JOB", str(job))
    items = qs.standing(open(an.JOB, encoding="utf-8").read())
    assert [(h, f) for h, f, _ in items] == [(3.0, ["shop"]), (6.0, ["buyer"])]
    assert items[0][2].startswith("최근 24시간 별점 3점 이하 리뷰가")
    a = make("analyst")
    assert a.enqueue_standing(NOW) == [1, 2]
    assert a.enqueue_standing(NOW + 4 * H) == []  # both still open
    qs.close(root, 1, "done", NOW + 10)
    assert a.enqueue_standing(NOW + 3 * H) == []  # answered less than 3 h ago
    assert a.enqueue_standing(NOW + 3 * H + 10) == [3]
    assert _q(root, 3)["for"] == ["shop"] and _q(root, 3)["from"] == "standing"
    qs.add(root, "buyer", "구매 질문", NOW + 3 * H + 20)
    assert (
        qs.next_open(root)["id"] == 4
    )  # an operator's question before the older standing ones


# ------------------------------------------------------------ runs
def test_run_writes_the_finding_and_logs_analyst_rows(env, monkeypatch):
    root, make = env
    from kernel import common as c

    monkeypatch.setattr(c, "now_ts", lambda: NOW + 300)  # the answer is written after the run began
    qs.ask(root, "shop", "별점 낮은 구간은?", NOW)
    a = make("analyst")
    a.terra = Rec(
        {"tools": [{"tool": "policy", "args": {}}, {"tool": "findings", "args": {}}]},
        {"tools": [{"tool": "experiments", "args": {}}], "keep": "price_mult 0.9 확인"},
        {"done": {"answer": ANSWER, "notes": "조사 메모"}},
    )
    res = a.poll(NOW)
    assert res["status"] == "done" and res["answer"] == {"ok": True, "id": 1}
    assert {x for x in a.terra.calls} == {("analyst", "medium")}
    p = a.terra.prompts[0]
    assert (
        "## 이번 질문\n\n#1 " in p
        and "별점 낮은 구간은?" in p
        and "- tool_search(query): " in p
        and "- stockouts(" in p
    )
    assert "- 그 밖의 도구" not in p and "policy, code, forum, ask는 받지 않음" in p
    f = qs.findings(root)
    assert (
        len(f) == 1
        and f[0]["finding"] == ANSWER["finding"]
        and f[0]["for"] == ["shop"]
        and f[0]["status"] == "done"
    )
    assert _q(root, 1)["status"] == "done" and _q(root, 1)["answered_ts"] == NOW + 300
    rows = _rows(root)
    assert rows and all(
        r["agent"] == "analyst" and r["role"] == "analyst" for r in rows
    )
    assert [r["name"] for r in rows if r["kind"] == "tool"] == [
        "policy",
        "findings",
        "experiments",
    ]
    assert [r["kind"] for r in rows][-1] == "analyst_run" and not any(
        r["kind"] == "operator_wake" for r in rows
    )
    assert (
        open(
            os.path.join(root, "a2/operator_notes_analyst.md"), encoding="utf-8"
        ).read()
        == "조사 메모"
    )
    assert os.path.exists(os.path.join(root, "a2/operator_state_analyst.json"))
    assert audit._wakes(root) == ([], []) and audit._wakes(root, "shop") == (
        [],
        [],
    )  # not an operator wake
    assert a.poll(NOW + 60) is None  # nothing open
    qs.ask(root, "shop", "다음 질문", NOW + 60)
    assert a.poll(NOW + 120) is None  # min gap
    a.terra = Rec({"done": {"answer": ANSWER}})
    assert a.poll(NOW + an.MIN_GAP_S)["status"] == "done"


def test_shop_changes_in_the_analysts_done_are_refused(env):
    root, make = env
    qs.ask(root, "buyer", "질문", NOW)
    before = open(os.path.join(root, "policy.json"), encoding="utf-8").read()
    a = make("analyst")
    item = {
        "section": "defaults.goods",
        "key": "price_mult",
        "value": 0.85,
        "reason": "r",
        "expect": "e",
    }
    a.terra = Rec(
        {
            "done": {
                "answer": dict(ANSWER, **{"for": "buyer"}),
                "policy": [item],
                "forum": [{"post": "글", "as": "shop"}],
                "code": {
                    "edits": [{"file": "policy.py", "old": "a", "new": "b"}],
                    "reason": "r" * 30,
                    "expect": "e" * 30,
                },
                "ask": "또 질문",
            }
        }
    )
    res = a.poll(NOW)
    assert (
        res["refused"] == ["policy", "code", "forum", "ask"]
        and "policy" not in res
        and "code" not in res
    )
    assert open(os.path.join(root, "policy.json"), encoding="utf-8").read() == before
    assert ex.load(root) == [] and len(qs.load(root)) == 1
    assert not os.path.exists(os.path.join(root, "a2/forum_queue.jsonl"))
    refused = [r for r in _rows(root) if r["kind"] == "analyst_refused"]
    assert (
        refused[0]["keys"] == ["policy", "code", "forum", "ask"]
        and refused[0]["qid"] == 1
    )
    assert qs.findings(root)[0]["for"] == ["buyer"]


def test_out_of_steps_writes_a_failed_finding(env, monkeypatch):
    root, make = env
    monkeypatch.setenv("A2_ANALYST_STEPS", "2")  # clamped to 6
    qs.ask(root, "shop", "질문", NOW)
    a = make("analyst")
    a.terra = Rec(
        *[
            {
                "tools": [{"tool": "guide", "args": {"section": str(i)}}],
                "keep": f"결론{i}",
            }
            for i in range(6)
        ]
    )
    res = a.poll(NOW)
    assert (
        res["status"] == "no_done" and res["steps"] == 6 and len(a.terra.prompts) == 6
    )
    f = qs.findings(root)[-1]
    assert (
        f["status"] == "failed"
        and "결론1" in f["finding"]
        and "확인 못 함" in f["finding"]
    )
    assert _q(root, 1)["status"] == "failed"
    a.terra = Rec({"done": {"note": "답 없음"}})
    qs.ask(root, "shop", "질문 2", NOW)
    assert a.poll(NOW + an.MIN_GAP_S)["answer"]["ok"] is False
    assert _q(root, 2)["status"] == "failed" and len(qs.findings(root)) == 2


# ------------------------------------------------------------ what the operators see
def test_role_prompt_shows_only_findings_addressed_to_it(env):
    root, make = env
    shop, buyer = make("shop"), make("buyer")
    shop.terra = Script({"done": {}})
    shop.wake("주기 점검", NOW)
    assert "## 조사 결과" not in shop.terra.prompts[0]
    qs.add_finding(
        root,
        {"id": 1, "from": "shop", "q": "가게 질문", "for": ["shop"]},
        "가게 결과",
        [],
        "가게 제안",
        None,
        NOW,
    )
    qs.add_finding(
        root,
        {"id": 2, "from": "standing", "q": "구매 질문"},
        "구매 결과",
        [],
        "",
        ["buyer"],
        NOW,
    )
    qs.add_finding(
        root,
        {"id": 3, "from": "shop", "q": "오래된 질문"},
        "옛 결과",
        [],
        "",
        ["shop"],
        NOW - 13 * H,
    )
    shop.terra, buyer.terra = Script({"done": {}}), Script({"done": {}})
    shop.wake("주기 점검", NOW + 60)
    buyer.wake("주기 점검", NOW + 60)
    p, q = shop.terra.prompts[0], buyer.terra.prompts[0]
    sec = "## 조사 결과 (조사원이 확인한 것. 결정은 네 몫)"
    assert (
        p.index("## 지난 점검에 네가 낸 것의 결과")
        < p.index(sec)
        < p.index("## 네 메모")
    )
    assert (
        "가게 질문" in p
        and "가게 제안" in p
        and "구매 질문" not in p
        and "오래된 질문" not in p
    )
    assert sec in q and "구매 질문" in q and "가게 질문" not in q
    assert (
        op.Operator(
            root, None, Trajectory(os.path.join(root, "a2/t2.jsonl"))
        )._findings_block(NOW)
        == []
    )


def test_findings_tool(env):
    root, _ = env
    assert tools.call("findings", "operator", root, now=NOW).startswith(
        "최근 24시간 조사 결과 0건"
    )
    qs.add_finding(
        root,
        {"id": 1, "from": "shop", "q": "가게 질문"},
        "가게 결과",
        ["12건, events"],
        "제안",
        ["shop"],
        NOW,
    )
    qs.add_finding(
        root,
        {"id": 2, "from": "buyer", "q": "구매 질문"},
        "구매 결과",
        [],
        "",
        ["buyer"],
        NOW,
        status="failed",
    )
    out = tools.call("findings", "operator", root, now=NOW + 60)
    assert (
        "조사 결과 2건" in out
        and "근거: 12건, events" in out
        and "제안: 제안" in out
        and "미완" in out
    )
    out = tools.call("findings", "operator", root, role="buyer", now=NOW + 60)
    assert "1건 (buyer 앞)" in out and "가게 질문" not in out
    assert tools.call("findings", "operator", root, role="x").startswith("인자 오류")
    assert (
        "findings" in tools.RECOMMENDED["shop"]
        and "findings" in tools.RECOMMENDED["buyer"]
    )


# ------------------------------------------------------------ never in the operators' way
def test_analyst_main_takes_no_busy_or_ledger_lock(env, monkeypatch, tmp_path):
    root, _ = env
    busy = {"role": "shop", "pid": os.getpid(), "ts": __import__("time").time()}
    json.dump(busy, open(os.path.join(root, op.BUSY), "w"))

    def boom(*a, **k):
        raise AssertionError("lock taken")

    monkeypatch.setattr(op, "claim_busy", boom)
    monkeypatch.setattr(op, "_flock", boom)
    qs.ask(root, "shop", "질문", NOW)
    assert (
        an.main(
            [
                "--root",
                root,
                "--dry",
                "--once",
                "--harness",
                str(tmp_path / "noharness"),
            ]
        )
        == 0
    )
    assert json.load(open(os.path.join(root, op.BUSY))) == busy
    assert _q(root, 1)["status"] == "failed"  # the dry model answers without a finding
    run = [r for r in _rows(root) if r["kind"] == "analyst_run"]
    assert run and run[0]["role"] == "analyst" and run[0]["status"] == "done"


def test_answer_without_any_tool_call_is_refused_once_tools_are_read(tmp_path, monkeypatch):
    assert "도구를 한 번도 부르지 않고" in an.Analyst._early_done(None, {})
    assert an.Analyst._early_done(None, {"k": [0, "h", "obs"]}) is None
    assert op.Operator._early_done(None, {}) is None


def test_early_answer_goes_back_to_the_tools(env, monkeypatch):
    root, make = env
    monkeypatch.undo()
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    qs.ask(root, "shop", "별점 낮은 구간은?", NOW)
    a = make("analyst")
    a.terra = Rec({"done": {"answer": ANSWER}}, {"tools": [{"tool": "policy", "args": {}}]}, {"done": {"answer": ANSWER}})
    res = a.poll(NOW)
    assert res["status"] == "done" and res["calls"] == 3
    assert "거절됨: 도구를 한 번도" in a.terra.prompts[1]
