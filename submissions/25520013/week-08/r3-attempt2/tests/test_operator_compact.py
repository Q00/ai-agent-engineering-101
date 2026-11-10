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
    assert "시장 설명서 목차" in p[1].split("바로 앞 단계의 관찰")[1] and "시장 설명서 목차" not in p[2].split("바로 앞 단계의 관찰")[1]
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


def test_wake_reason_interval_stretches_while_an_experiment_runs(o):
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
