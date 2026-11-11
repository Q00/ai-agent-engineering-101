"""Compact mode of the operator (DESIGN 14.2, A2_OPERATOR_COMPACT=1): small prompt, observations kept one step,
policy edits registered as experiments, event-driven wakes."""

import json
import os
import shutil

import pytest
from kernel import experiments as ex
from kernel import operator as op
from kernel.trajectory import Trajectory
from tests import test_policy_edit_wide as W

NOW = 1_794_000_000.0


class Script:
    """Model stand-in: answers the queued actions in order and records every prompt."""

    def __init__(self, *acts):
        self.acts, self.prompts = list(acts), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        return json.dumps(self.acts.pop(0), ensure_ascii=False)


@pytest.fixture
def o(tmp_path, monkeypatch):
    root = str(tmp_path / "root")
    os.makedirs(os.path.join(root, "a2"))
    json.dump(W.POLICY, open(os.path.join(root, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
    shutil.copy(W.A1_POLICY, os.path.join(root, "policy.py"))
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    monkeypatch.delenv("A2_OPERATOR_INTERVAL_S", raising=False)
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"))
    return op.Operator(root, None, traj, harness_root=str(tmp_path / "noharness"))


def _item(key="price_mult", value=0.85, section="defaults.goods"):
    return {"section": section, "key": key, "value": value, "reason": "근거 1", "expect": "순이익 +0.1M/h"}


def test_compact_only_when_env_set_and_live_off(o, monkeypatch):
    assert o.compact()
    open(os.path.join(o.root, "a2", "LIVE"), "w").close()
    assert not o.compact()
    os.unlink(os.path.join(o.root, "a2", "LIVE"))
    monkeypatch.delenv("A2_OPERATOR_COMPACT")
    assert not o.compact()


def test_wake_keeps_one_step_of_observations_and_registers_experiments(o):
    o.terra = Script(
        {"tools": [{"tool": "policy", "args": {}}, {"tool": "guide", "args": {}}]},
        {"tools": [{"tool": "policy", "args": {}}], "keep": "goods price_mult 0.9 확인"},
        {"done": {"policy": [_item(), _item("min_margin", 0.1)], "notes": "가설: 값 0.85", "note": "실험 1건"}},
    )
    res = o.wake("주기 점검", NOW)
    p = o.terra.prompts
    assert res["status"] == "done" and res["calls"] == 3 and res["prompt_chars"] == sum(map(len, p))
    assert "## 상태 카드" in p[0] and "운영자 일" in p[0] and "harness_read" not in p[0]
    assert "편집 가능 키" in p[1]  # the first step's observation is shown in full on the next step
    assert "goods price_mult 0.9 확인" in p[2]
    assert p[2].count("편집 가능 키") == p[1].count("편집 가능 키")  # one policy observation, not two
    assert "시장 설명서 목차" in p[1].split("이번 점검의 관찰")[1] and "시장 설명서 목차" in p[2].split("이번 점검의 관찰")[1]
    pol = res["policy"]
    assert pol[0]["ok"] and pol[0]["experiment"] == 1
    assert not pol[1]["ok"] and "실험" in pol[1]["error"]  # same scope as the experiment just started
    assert json.load(open(os.path.join(o.root, "policy.json"), encoding="utf-8"))["defaults"]["goods"]["price_mult"] == 0.85
    run = ex.running(o.root)
    assert len(run) == 1 and run[0]["expect"] == "순이익 +0.1M/h"
    assert open(os.path.join(o.root, "a2/operator_notes.md"), encoding="utf-8").read() == "가설: 값 0.85"
    o.terra = Script({"done": {}})
    o.wake("주기 점검", NOW + 60)
    assert "가설: 값 0.85" in o.terra.prompts[0] and "#1" in o.terra.prompts[0]


def test_bad_policy_value_is_a_refusal_not_a_crash(o):
    o.terra = Script({"done": {"policy": [_item(value=float("nan")), "x", _item(key=["a"])]}})
    res = o.wake("주기 점검", NOW)
    assert res["status"] == "done" and [r["ok"] for r in res["policy"]] == [False, False, False]


def test_last_step_must_be_done(o):
    o.terra = Script(*[{"tools": [{"tool": "policy", "args": {}}]}] * op.COMPACT_STEPS)
    res = o.wake("주기 점검", NOW)
    assert res["status"] == "no_done" and res["calls"] == op.COMPACT_STEPS
    assert "반드시 done" in o.terra.prompts[-1]


def test_wake_reason_interval_stretches_while_an_experiment_runs(o, monkeypatch):
    monkeypatch.setattr(op, "BUSY_INTERVAL_S", 3600)  # the live value equals the idle interval since 11-11
    assert "주기 점검" in o.wake_reason(NOW)
    o.terra = Script({"done": {"policy": [_item()]}})
    o.wake("주기 점검", NOW)
    assert ex.running(o.root)
    assert o.wake_reason(NOW + 1900) is None  # past the idle interval (1800) but an experiment runs
    assert "주기 점검" in o.wake_reason(NOW + op.BUSY_INTERVAL_S)


def test_verdict_wakes_at_once(o):
    o.terra = Script({"done": {"policy": [_item()]}})
    o.wake("주기 점검", NOW)
    e = ex.running(o.root)[0]
    o._save_state(dict(o._state(), last_wake_ts=e["eval_ts"]))  # just woke: only a verdict can wake now
    reason = o.wake_reason(e["eval_ts"] + 1)
    assert reason is None or "실험 판정" in reason
    rows = ex.load(o.root)
    if rows[0]["status"] != "running":
        assert "실험 판정" in reason


def test_rev_weight_from_board(o):
    assert o._rev_weight() == 0.0  # no board


def test_split_env_starts_a_concurrent_comparison_and_leaves_policy_json(o, monkeypatch):
    monkeypatch.setenv("A2_SPLIT", "1")
    before = open(os.path.join(o.root, "policy.json"), encoding="utf-8").read()
    o.terra = Script({"done": {"policy": [_item(), _item("max_pay_frac", 0.8, "buyer.defaults.goods")]}})
    r = o.wake("주기 점검", NOW)
    rows = {e["key"]: e for e in ex.running(o.root)}
    assert rows["price_mult"]["mode"] == "split" and rows["max_pay_frac"]["mode"] == "sequential", r
    arms = json.load(open(os.path.join(o.root, "policy_arms.json"), encoding="utf-8"))["arms"]
    assert [a["id"] for a in arms] == [rows["price_mult"]["id"]] and arms[0]["value"] == 0.85
    now = json.load(open(os.path.join(o.root, "policy.json"), encoding="utf-8"))
    assert now["defaults"]["goods"]["price_mult"] == json.loads(before)["defaults"]["goods"]["price_mult"]
    assert now["buyer"]["defaults"]["goods"]["max_pay_frac"] == 0.8


def test_without_split_env_the_same_key_is_sequential(o, monkeypatch):
    monkeypatch.delenv("A2_SPLIT", raising=False)
    o.terra = Script({"done": {"policy": [_item()]}})
    o.wake("주기 점검", NOW)
    assert ex.running(o.root)[0]["mode"] == "sequential"
    assert not os.path.exists(os.path.join(o.root, "policy_arms.json"))


def test_bad_item_is_refused_and_nothing_is_left_unregistered(o):
    before = open(os.path.join(o.root, "policy.json"), encoding="utf-8").read()
    bad = dict(_item(), window_h=10**400)
    o.terra = Script({"done": {"policy": [bad, dict(_item(), value=float("inf")), "x"]}})
    r = o.wake("주기 점검", NOW)
    after = json.load(open(os.path.join(o.root, "policy.json"), encoding="utf-8"))
    n_run = len(ex.running(o.root))
    changed = after["defaults"]["goods"]["price_mult"] != json.loads(before)["defaults"]["goods"]["price_mult"]
    assert changed == (n_run == 1), r  # an edit is in the file only when its experiment is in the ledger


def test_harness_tools_are_refused_in_compact_mode(o):
    for name in op.HARNESS_TOOLS:
        assert "쓸 수 없는" in o._run_tool(name, {})


def test_expect_is_masked(o):
    o.secrets = ("secretlogin",)
    o.terra = Script({"done": {"policy": [dict(_item(), expect="secretlogin 순이익 +1")]}})
    o.wake("주기 점검", NOW)
    assert "secretlogin" not in json.dumps(ex.load(o.root), ensure_ascii=False)


def test_main_loop_survives_a_failing_poll(o, monkeypatch, tmp_path):
    calls = {"n": 0}

    def boom(self, now=None):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise KeyboardInterrupt
        raise RuntimeError("poll broke")

    monkeypatch.setattr(op.Operator, "wake_reason", boom)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        op.main(["--root", o.root, "--dry", "--harness", str(tmp_path / "noharness")])
    rows = [json.loads(x) for x in open(os.path.join(o.root, "a2/trajectory.jsonl"), encoding="utf-8")]
    assert sum(1 for r in rows if r.get("error") == "RuntimeError") == 1  # the same error is logged once an hour


def test_meta_harness_wiring(tmp_path, monkeypatch):
    """Guidance and audit lines reach the prompt, hidden tools leave the list, new_tool goes through made_tools."""
    from kernel import auditor, made_tools
    from kernel import operator as opm

    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"), exist_ok=True)
    with open(os.path.join(root, auditor.GUIDANCE), "w", encoding="utf-8") as f:
        f.write("재입고 절을 한 점검에 한 번만 읽음")
    with open(os.path.join(root, auditor.TOOL_STATE), "w", encoding="utf-8") as f:
        json.dump({"hidden": ["shops", "policy"], "desc": {"board": "순위표 새 설명"}}, f)
    op = opm.Operator.__new__(opm.Operator)
    op.root = root
    names = dict(op.listed())
    assert "shops" not in names and names["board"] == "순위표 새 설명" and "stockouts" in names
    assert auditor.guidance(root) == "재입고 절을 한 점검에 한 번만 읽음"
    seen = {}
    monkeypatch.setattr(made_tools, "add", lambda r, spec, now, **kw: seen.update(spec=spec, kw=kw) or {"ok": True, "name": spec["name"], "sample": "x" * 900})
    op.secrets, op.traj = (), None
    res = op._finish_compact({"new_tool": {"name": "q_abc"}, "note": "n"}, 1000.0)
    assert res["new_tool"]["ok"] and len(res["new_tool"]["sample"]) == 300
    assert seen["kw"]["by"] == "operator" and "policy" in seen["kw"]["reserved"]


def test_stockouts_reads_sku_from_request_text(tmp_path):
    from kernel import tools

    root = str(tmp_path)
    for d in (".market", ".r3"):
        os.makedirs(os.path.join(root, d))
    cat = [{"sku": "G0000aaaa", "name": "사과", "category": "식품>과일", "kind": "food", "list_price": 10, "shelf_hours": 48}]
    json.dump(cat, open(os.path.join(root, ".market/catalog.json"), "w"))
    json.dump({"units": [{"sku": "G0000aaaa"}]}, open(os.path.join(root, ".market/inventory.json"), "w"))
    rows = [
        {"ts": 990.0, "decision": "no_stock", "outcome": {"request": "want 사과 (G0000aaaa)", "short_shelf": 0.5}},
        {"ts": 995.0, "decision": "no_stock", "outcome": {"request": "want (G0000aaaa)"}},
        {"ts": 2000.0, "decision": "no_stock", "outcome": {"request": "future G0000aaaa"}},
        {"ts": 996.0, "decision": "no_stock", "outcome": {"request": "Greetings"}},
    ]
    with open(os.path.join(root, ".r3/metrics.jsonl"), "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    out = tools.stockouts(root, hours=1, now=1000.0)
    assert "거절 3건" in out and "요청 1건" in out and "식품>과일: 2건" in out and "짧아 거절 1건" in out and "재고 1개" in out


def test_ask_retries_once_after_a_timeout(o):
    from kernel.llm import ModelError

    class Flaky:
        def __init__(self, fails):
            self.fails, self.timeouts = fails, []

        def complete(self, prompt, *, agent, timeout_s, effort="medium"):
            self.timeouts.append(timeout_s)
            if len(self.timeouts) <= self.fails:
                raise ModelError("timeout")
            return '{"done": {"note": "ok"}}'

    o.terra = Flaky(1)
    assert o.wake("주기 점검", NOW)["status"] == "done" and o.terra.timeouts == [op.ASK_TIMEOUT_S] * 2
    o.terra = Flaky(2)
    assert o.wake("주기 점검", NOW + 1)["status"] == "model_error" and len(o.terra.timeouts) == 2


def test_repeat_call_is_answered_from_the_page_and_agent_arg_works(o):
    from kernel import memfeed, tools

    o.terra = Script(
        {"tools": [{"tool": "policy", "args": {}}]},
        {"tools": [{"tool": "guide", "args": {"section": "1"}}]},
        {"tools": [{"tool": "policy", "args": {}}]},
        {"done": {"note": "끝"}},
    )
    o.wake("주기 점검", NOW)
    p = o.terra.prompts
    assert "편집 가능 키" in p[2] and p[3].count("편집 가능 키") == p[2].count("편집 가능 키")
    assert "이미 읽음" not in p[2]
    with open(os.path.join(o.root, "a2/trajectory.jsonl"), encoding="utf-8") as f:
        rows = [r for r in map(json.loads, f) if r.get("kind") == "tool"]
    assert [r.get("cached", False) for r in rows] == [False, False, True]
    assert "TypeError" not in str(o._run_tool("recall", {"agent": "operator", "query": "소파 가격", "k": 3}))
    assert "TypeError" not in str(o._run_tool("trajectory", {"agent": "operator", "hours": 1}))
    row = {"kind": "experiment_end", "ts": NOW, "id": 2, "mode": "split", "section": "가구>소파", "key": "price_mult",
           "old": 0.68, "new": 0.73, "status": "dropped", "verdict": {"a": 1301770.9, "b": 1123829.7, "se": 502977.8}}
    (agent, kind, text), = memfeed.sentence(row)
    assert agent == "operator" and "판정 버림" in text and "1123830" in text and "0.68 → 0.73" in text
    assert memfeed.sentence(dict(row, status="running")) is None
