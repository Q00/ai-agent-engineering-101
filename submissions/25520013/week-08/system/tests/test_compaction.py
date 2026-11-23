"""Context management of the compact loop (kernel.operator._compact, shared by role operators and the investigator):
compaction at the threshold with recent steps verbatim, numbers passed through, fallback, two compactions, the env
switch, the wake summary handed to memory; prompt sizes by section on llm rows (operators, investigator, auditor); the investigator's short tool list; memfeed of findings and code verdicts."""

import json
import os
import subprocess
import sys
import time

import pytest
from kernel import analyst as an
from kernel import auditor as au
from kernel import llm, memfeed
from kernel import operator as op
from kernel import questions as qs
from kernel.llm import ModelError
from kernel.trajectory import Trajectory
from tests.test_operator_roles import _root

NOW = 1_794_000_000.0
DEFAULTS = (op.COMPACT_AT, op.COMPACT_MIN_OLD)  # the module's values, before the fixture sets the test scale
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBS = 3_000  # chars of one fake tool result: two per step, 6,000 per step
SUMMARY_1 = '## 확인된 숫자와 출처\n- 순이익 1,234,567.89원, net_profit {"hours": 12}\n## 세운 가설과 결과\n- 없음\n## 이미 한 변경과 요청\n- 없음\n## 아직 안 풀린 것\n- 없음\n## 다음에 하려던 것\n- 없음'
SUMMARY_2 = '## 확인된 숫자와 출처\n- 순이익 1,234,567.89원과 C등급 리뷰 0.4167, reviews {"by": "grade"}\n- 없음\n## 세운 가설과 결과\n- 없음\n## 이미 한 변경과 요청\n- 없음\n## 아직 안 풀린 것\n- 없음\n## 다음에 하려던 것\n- 없음'


def _obs(name, args):
    """A deterministic tool result of OBS chars that starts with a marker of its call and carries numbers."""
    head = f"<<{name}:{json.dumps(args, sort_keys=True)}>> 값 12,345.67 표본 n=89 "
    return (head + "가" * OBS)[:OBS]


class Model:
    """Model stand-in: summary calls (prompts that start with SUMMARY_JOB) answer from summaries (str as is, an
    exception raised); step calls answer the queued actions and record the prompt and the sections on the call."""

    def __init__(self, acts, summaries=()):
        self.acts, self.summaries = list(acts), list(summaries)
        self.prompts, self.summary_prompts, self.sections = [], [], []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        if prompt.startswith(op.SUMMARY_JOB):
            self.summary_prompts.append(prompt)
            s = self.summaries.pop(0)
            if isinstance(s, Exception):
                raise s
            return s
        self.prompts.append(prompt)
        self.sections.append((llm._SECTIONS.get() or {}).get("sections"))
        a = self.acts.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def _acts(n_tool_steps, done=None):
    """n tool steps of two distinct calls each, then a done object."""
    out = [
        {
            "tools": [{"tool": "events", "args": {"i": i, "j": j}} for j in (0, 1)],
            "keep": f"결론 {i}: 값 {i}0,001",
        }
        for i in range(n_tool_steps)
    ]
    return out + [{"done": done or {"note": "끝"}}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    for k in (
        "A2_OPERATOR_INTERVAL_S",
        "A2_SHOP_LEAD_S",
        "A2_SPLIT",
        "A2_COMPACT_SUMMARY",
        "A2_ANALYST_STEPS",
        "A2_ANALYST_EFFORT",
        "A2_OPERATOR_EFFORT",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(
        op.Operator, "_run_tool", lambda self, name, args: _obs(name, args)
    )
    # test scale: 6,000 chars of results per step, compaction once three steps are carried (test_default_thresholds
    # runs the real values)
    monkeypatch.setattr(op, "COMPACT_AT", 18_000)
    monkeypatch.setattr(op, "COMPACT_MIN_OLD", 6_000)
    root = _root(tmp_path)
    harness = str(tmp_path / "noharness")

    def make(role, model):
        traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role=role)
        if role == "analyst":
            return an.Analyst(root, model, traj, harness_root=harness)
        return op.Operator(root, model, traj, harness_root=harness, role=role)

    return root, make


def _rows(root, kind=None):
    p = os.path.join(root, "a2/trajectory.jsonl")
    rows = (
        [json.loads(x) for x in open(p, encoding="utf-8")] if os.path.exists(p) else []
    )
    return [r for r in rows if kind is None or r.get("kind") == kind]


def _marker(i, j):
    return f"<<events:{json.dumps({'i': i, 'j': j}, sort_keys=True)}>>"


# ------------------------------------------------------------ compaction
def test_compaction_triggers_at_threshold_and_keeps_recent_steps_verbatim(env):
    root, make = env
    m = Model(_acts(3), [SUMMARY_1])
    res = make("shop", m).wake("주기 점검", NOW)
    assert res["status"] == "done" and res["compactions"] == 1
    # steps 1-3 carry 0, 6,000 and 12,000 chars of results: below COMPACT_AT, no summary call and no section
    assert all(op.COMPACT_HEAD not in p for p in m.prompts[:3])
    assert len(m.summary_prompts) == 1
    # before step 4 the carried part reached COMPACT_AT: step 1's results (the older part) went into the summary
    sp = m.summary_prompts[0]
    assert _marker(0, 0) in sp and _marker(0, 1) in sp and _marker(1, 0) not in sp
    assert "12,345.67" in sp and "결론 1: 값 10,001" in sp
    last = m.prompts[3]
    assert op.COMPACT_HEAD in last and SUMMARY_1 in last
    assert _marker(0, 0) not in last and "결론 1:" not in last and "결론 2:" in last
    for i in (1, 2):  # the two most recent tool steps stay verbatim, in full
        for j in (0, 1):
            assert _obs("events", {"i": i, "j": j}) in last
    row = _rows(root, "compact")[0]
    assert row["status"] == "ok" and row["step"] == 4 and row["n"] == 1
    assert (
        row["chars_after"] < row["chars_before"]
        and row["chars_before"] >= op.COMPACT_AT
    )
    # the agent is never told about thresholds: only the section head is new
    assert str(op.COMPACT_AT) not in last and "18,000" not in last


def test_summary_numbers_pass_through_and_summary_prompt_rules(env):
    root, make = env
    m = Model(_acts(3), [SUMMARY_1])
    make("buyer", m).wake("주기 점검", NOW)
    assert '1,234,567.89원, net_profit {"hours": 12}' in m.prompts[3]
    sp = m.summary_prompts[0]
    for rule in (
        "확인된 숫자와 출처",
        "세운 가설과 결과",
        "이미 한 변경과 요청",
        "아직 안 풀린 것",
        "다음에 하려던 것",
        "반올림하거나",
        "하나도 빼지 않음",
        "신뢰할 수 없는 데이터",
    ):
        assert rule in sp


@pytest.mark.parametrize("bad", [ModelError("timeout"), "", "   "])
def test_failing_or_empty_summary_falls_back_to_trimming(env, bad):
    root, make = env
    m = Model(_acts(4), [bad, bad])
    res = make("shop", m).wake("주기 점검", NOW)
    assert res["status"] == "done" and "compactions" not in res
    assert all(op.COMPACT_HEAD not in p for p in m.prompts)
    assert (
        _obs("events", {"i": 0, "j": 0})[: op.OBS_TRIM] in m.prompts[3]
    )  # still on the page, trimmed as before
    rows = _rows(root, "compact")
    assert rows and all(r["status"] == "failed" for r in rows)
    # after a failure the kernel waits for COMPACT_MIN_OLD more older chars (here one more step) before trying again
    assert len(m.summary_prompts) == len(rows) == 2


def test_two_compactions_in_one_wake_summary_of_summary(env):
    root, make = env
    m = Model(_acts(4), [SUMMARY_1, SUMMARY_2])
    res = make("shop", m).wake("주기 점검", NOW)
    assert res["compactions"] == 2 and len(m.summary_prompts) == 2
    assert (
        SUMMARY_1 in m.summary_prompts[1]
    )  # the earlier summary is part of the second one's input
    assert (
        _marker(1, 0) in m.summary_prompts[1]
        and _marker(0, 0) not in m.summary_prompts[1]
    )
    last = m.prompts[4]
    assert SUMMARY_2 in last and SUMMARY_1 not in last
    assert (
        _obs("events", {"i": 3, "j": 1}) in last
        and _obs("events", {"i": 2, "j": 0}) in last
    )
    assert [r["n"] for r in _rows(root, "compact")] == [1, 2]


def test_a_compacted_call_may_be_made_again(env):
    root, make = env
    acts = _acts(3)[:-1] + [
        {"tools": [{"tool": "events", "args": {"i": 0, "j": 0}}]},
        {"done": {"note": "끝"}},
    ]
    m = Model(acts, [SUMMARY_1])
    make("shop", m).wake("주기 점검", NOW)
    assert (
        "이미 읽음" not in m.prompts[4]
        and _obs("events", {"i": 0, "j": 0}) in m.prompts[4]
    )


def test_default_thresholds(env, monkeypatch):
    root, make = env
    assert DEFAULTS == (60_000, 30_000)  # 24,000 / 16,000 until 11-12 23:50 (live: a summary every 2-3 steps)
    monkeypatch.setattr(op, "COMPACT_AT", DEFAULTS[0])
    monkeypatch.setattr(op, "COMPACT_MIN_OLD", DEFAULTS[1])
    m = Model(_acts(11), [SUMMARY_1, SUMMARY_2])
    make("shop", m).wake("주기 점검", NOW)
    rows = _rows(root, "compact")
    # 6,000 chars per step: before step 10, 54,000 carried (< 60,000); before step 11, 60,000 carried with 48,000
    # older: the first compaction; the second needs 30,000 more older chars, which this wake does not reach
    assert [r["step"] for r in rows] == [11] and rows[0]["chars_before"] >= 60_000
    assert all(op.COMPACT_HEAD not in p for p in m.prompts[:10]) and SUMMARY_1 in m.prompts[10]


def test_env_switch_turns_compaction_off(env, monkeypatch):
    root, make = env
    monkeypatch.setenv("A2_COMPACT_SUMMARY", "0")
    m = Model(_acts(5))
    assert make("shop", m).wake("주기 점검", NOW)["status"] == "done"
    assert m.summary_prompts == [] and _rows(root, "compact") == []
    assert all(op.COMPACT_HEAD not in p for p in m.prompts)


def test_investigator_gets_compaction_too(env, monkeypatch):
    root, make = env
    monkeypatch.setattr(an, "JOB", os.path.join(root, "no_job.md"))
    qs.ask(root, "shop", "C등급 리뷰는 어디서?", NOW)
    ans = {
        "finding": "C등급 n=12",
        "evidence": ["12건, reviews"],
        "suggest": "",
        "for": ["shop"],
    }
    m = Model(_acts(3, {"answer": ans}), [SUMMARY_1])
    a = make("analyst", m)
    res = a.poll(NOW, force=True)
    assert res["status"] == "done" and res["compactions"] == 1
    assert SUMMARY_1 in m.prompts[3]
    assert [r["agent"] for r in _rows(root, "compact")] == ["analyst"]


def test_wake_summary_row_goes_to_memory(env):
    root, make = env
    m = Model(_acts(3), [SUMMARY_1])
    make("shop", m).wake("주기 점검", NOW)
    row = _rows(root, "wake_summary")[-1]
    assert (
        row["role"] == "shop" and row["compactions"] == 1 and SUMMARY_1 in row["text"]
    )
    assert (
        "결론 2:" in row["text"]
    )  # the conclusions kept after the summary come with it
    sent = []
    r = memfeed.run_once(
        root,
        "/nonexistent",
        harness_dir=os.path.join(HERE, "harness"),
        since_hours=1e6,
        now=time.time(),
        add=lambda sock, agent, text, infer, metadata: (
            sent.append((agent, text, metadata)) or True
        ),
    )
    assert r["ok"] and any(
        "가게 운영자 점검 간추림" in t
        and "1,234,567.89" in t
        and md.get("role") == "shop"
        for _, t, md in sent
    )
    # no compaction: the kept conclusions are handed over
    m2 = Model(_acts(2))
    make("buyer", m2).wake("주기 점검", NOW + 1)
    row = _rows(root, "wake_summary")[-1]
    assert (
        row["role"] == "buyer" and row["compactions"] == 0 and "결론 1" in row["text"]
    )


# ------------------------------------------------------------ sizes on llm rows
def _fake_cli(tmp_path):
    p = tmp_path / "codex"
    p.write_text("#!/bin/sh\n")
    os.chmod(p, 0o755)
    return str(p)


class _Out:
    def __init__(self, out):
        self.returncode, self.stdout, self.stderr = 0, out, ""


def test_operator_and_investigator_llm_rows_carry_sizes(env, tmp_path, monkeypatch):
    root, make = env
    cli = _fake_cli(tmp_path)
    answers = [json.dumps(a, ensure_ascii=False) for a in _acts(1)]
    o = make("shop", None)
    o.terra = llm.Terra(
        o.traj, cli=cli, allowed=(cli,), runner=lambda cmd, **kw: _Out(answers.pop(0))
    )
    o.wake("주기 점검", NOW)
    rows = [r for r in _rows(root, "llm") if r.get("role") == "shop"]
    assert len(rows) == 2
    for r in rows:
        assert sum(r["sections"].values()) == r["prompt_chars"]
        assert {"job", "상태 카드", "도구", "답 형식", "남긴 결론", "관찰"} <= set(
            r["sections"]
        )
    monkeypatch.setattr(an, "JOB", os.path.join(root, "no_job.md"))
    qs.ask(root, "shop", "C등급 리뷰는?", NOW)
    ans = {"finding": "n=12", "evidence": ["12건, reviews"], "for": ["shop"]}
    answers[:] = [json.dumps(a, ensure_ascii=False) for a in _acts(1, {"answer": ans})]
    a = make("analyst", None)
    a.terra = llm.Terra(
        a.traj, cli=cli, allowed=(cli,), runner=lambda cmd, **kw: _Out(answers.pop(0))
    )
    a.poll(NOW, force=True)
    rows = [r for r in _rows(root, "llm") if r.get("role") == "analyst"]
    assert len(rows) == 2 and all(
        sum(r["sections"].values()) == r["prompt_chars"] for r in rows
    )
    assert "이번 질문" in rows[0]["sections"]


def test_compaction_call_is_marked_on_its_llm_row(env, tmp_path):
    root, make = env
    cli = _fake_cli(tmp_path)

    def runner(cmd, **kw):
        if kw["input"].startswith(op.SUMMARY_JOB):
            return _Out(SUMMARY_1)
        return _Out(json.dumps(acts.pop(0), ensure_ascii=False))

    acts = _acts(3)
    o = make("shop", None)
    o.terra = llm.Terra(o.traj, cli=cli, allowed=(cli,), runner=runner)
    assert o.wake("주기 점검", NOW)["compactions"] == 1
    rows = _rows(root, "llm")
    comp = [r for r in rows if r.get("purpose") == "compact"]
    assert len(comp) == 1 and set(comp[0]["sections"]) == {
        "간추리기 지시",
        "앞선 간추림",
        "남긴 결론",
        "관찰",
    }
    assert "간추림" in rows[-1]["sections"] and "purpose" not in rows[-1]


def test_auditor_llm_row_carries_sizes(tmp_path):
    root = _root(tmp_path)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    cli = _fake_cli(tmp_path)
    terra = llm.Terra(
        traj,
        cli=cli,
        allowed=(cli,),
        runner=lambda cmd, **kw: _Out('{"findings": [], "note": "n"}'),
    )
    au.run(root, terra, traj, NOW, listed=["board", "policy"])
    row = next(r for r in _rows(root, "llm") if r["agent"] == "auditor")
    assert sum(row["sections"].values()) == row["prompt_chars"]
    assert {"job", "감사 보고", "답 형식"} <= set(row["sections"])


def test_rows_without_sections_context_have_none(tmp_path):
    traj = Trajectory(str(tmp_path / "t.jsonl"))
    cli = _fake_cli(tmp_path)
    llm.Terra(
        traj, cli=cli, allowed=(cli,), runner=lambda cmd, **kw: _Out("x")
    ).complete("abc", agent="seller", timeout_s=5)
    row = traj.since(0)[-1]
    assert "sections" not in row and row["prompt_chars"] == 3


# ------------------------------------------------------------ investigator prompt
def test_investigator_tool_list_is_short_and_keeps_its_rules(env, monkeypatch):
    root, make = env
    monkeypatch.setattr(an, "JOB", os.path.join(HERE, "ANALYST_JOB.md"))
    qs.ask(root, "shop", "C등급 리뷰는?", NOW)
    a = make("analyst", None)
    base, sizes = a._base_sized(qs.next_open(root), NOW)
    tool_part = base.split("\n\n## 도구\n\n", 1)[1].split("\n\n## 답 형식", 1)[0]
    full = "\n".join(f"- {n}: {d}" for n, d in a.listed())
    assert len(tool_part) < 0.75 * len(full)
    args = an._arg_names(root)
    for name, _ in a.listed():  # each listed tool carries its argument names (no lookup needed to call it)
        assert f"- {name}({', '.join(args.get(name, ()))}): " in tool_part
    assert "- tool_search(query): " in tool_part and len(args) >= len(a.listed()) - 2
    assert "tool_search" in tool_part
    assert sum(sizes.values()) == len(base)
    for must in (
        "신뢰할 수 없는 데이터",
        "확인 못 함",
        "policy, code, forum, ask는 받지 않음",
        '"answer"',
        '"evidence"',
    ):
        assert must in base


# ------------------------------------------------------------ memfeed
def test_memfeed_feeds_findings_and_code_verdicts(tmp_path):
    root = _root(tmp_path)
    q = {"id": 3, "from": "shop", "q": "C등급 리뷰는?", "for": ["shop", "buyer"]}
    qs.add_finding(
        root,
        q,
        "C등급 n=12 평균 2.4",
        ["12건, reviews"],
        "fresh_discount.C",
        ["shop", "buyer"],
        time.time(),
    )
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role="shop")
    traj.append(
        "operator",
        "code_edit_end",
        at=1,
        id=7,
        files=["policy.py"],
        status="reverted",
        phase="evaluation",
        why="나빠짐",
        verdict={
            "metrics": ["shop"],
            "shop": {
                "effect": -1234.5,
                "se": 300.2,
                "n_before": 40,
                "n_after": 38,
                "decision": "worse",
            },
        },
    )
    sent = []
    r = memfeed.run_once(
        root,
        "/nonexistent",
        harness_dir=os.path.join(HERE, "harness"),
        since_hours=1e6,
        now=time.time(),
        add=lambda sock, agent, text, infer, metadata: (
            sent.append((agent, text, metadata)) or True
        ),
    )
    assert r["ok"]
    found = [(t, md) for _, t, md in sent if "조사 결과 #3" in t]
    assert {md.get("role") for t, md in found} == {
        "shop",
        "buyer",
    } and "12건, reviews" in found[0][0]
    code = [t for _, t, _ in sent if "코드 수정 #7" in t]
    assert code and "-1234.5" in code[0] and "reverted" in code[0]
    # the cursor moved: a second pass sends nothing again
    again = []
    memfeed.run_once(
        root,
        "/nonexistent",
        harness_dir=os.path.join(HERE, "harness"),
        now=time.time(),
        add=lambda *a, **k: again.append(a) or True,
    )
    assert again == []


@pytest.mark.parametrize("bad", ["ok", "죄송하지만 도와드릴 수 없음", {"a": 1}, ["x"], 7, b"bytes"])
def test_a_reply_that_is_not_the_asked_summary_is_never_used(env, bad):
    root, make = env
    m = Model(_acts(6), [bad, bad, SUMMARY_1])
    make("shop", m).wake("주기 점검", NOW)
    rows = _rows(root, "compact")
    assert [r["status"] for r in rows] == ["failed", "failed"]  # and no third try in this wake
    assert all(op.COMPACT_HEAD not in p for p in m.prompts)


def test_summary_input_is_bounded(env, monkeypatch):
    root, make = env
    monkeypatch.setattr(op, "SUMMARY_IN", 5_000)
    m = Model(_acts(4), [SUMMARY_1, SUMMARY_2])
    make("shop", m).wake("주기 점검", NOW)
    assert m.summary_prompts and all(len(p) < len(op.SUMMARY_JOB) + 5_000 + 4_000 + len(SUMMARY_1) for p in m.summary_prompts)


def test_summary_job_states_the_length_line():
    """The rejecting line (SUMMARY_MAX) and the asked length are in the instruction the summary call reads."""
    assert f"{op.SUMMARY_MAX:,}자를 넘으면" in op.SUMMARY_JOB and f"{op.SUMMARY_TARGET:,}자 안팎" in op.SUMMARY_JOB
    assert op.SUMMARY_TARGET < op.SUMMARY_MAX


def test_retry_after_a_failed_summary_waits_min_old_and_gets_every_result_whole(env, monkeypatch):
    """At the real values: the first summary (before step 11, 48,000 older chars) fails; the retry waits for
    COMPACT_MIN_OLD more older chars (before step 16, 78,000) and its prompt holds every older result in full, since
    those results are marked gone once the summary is used (with SUMMARY_IN 60,000 they reached it cut: verifier
    simulation 11-12, 18 of them)."""
    root, make = env
    monkeypatch.setattr(op, "COMPACT_AT", DEFAULTS[0])
    monkeypatch.setattr(op, "COMPACT_MIN_OLD", DEFAULTS[1])
    assert op.SUMMARY_IN >= DEFAULTS[0] + DEFAULTS[1] + 6_000
    m = Model(_acts(16), ["", SUMMARY_1])
    make("shop", m).wake("주기 점검", NOW)
    rows = _rows(root, "compact")
    assert [(r["step"], r["status"]) for r in rows] == [(11, "failed"), (16, "ok")]
    retry = m.summary_prompts[1]
    assert all(_obs("events", {"i": i, "j": j}) in retry for i in range(13) for j in (0, 1))


def test_no_summary_while_the_older_part_is_under_min_old(env, monkeypatch):
    """At the real values: a small first step and two large recent ones carry 60,000+ chars, but the older part (the
    first step, 6,000) is under COMPACT_MIN_OLD: no summary call, the recent steps stay verbatim."""
    root, make = env
    monkeypatch.setattr(op, "COMPACT_AT", DEFAULTS[0])
    monkeypatch.setattr(op, "COMPACT_MIN_OLD", DEFAULTS[1])
    big = lambda i: {"tools": [{"tool": "events", "args": {"i": i, "j": j}} for j in range(10)], "keep": f"결론 {i}"}
    acts = [{"tools": [{"tool": "events", "args": {"i": 0, "j": j}} for j in (0, 1)], "keep": "결론 0"}, big(1), big(2),
            {"done": {"note": "끝"}}]
    m = Model(acts, [SUMMARY_1])
    make("shop", m).wake("주기 점검", NOW)
    assert m.summary_prompts == [] and _rows(root, "compact") == []
    assert all(_marker(2, j) in m.prompts[3] for j in range(10))
