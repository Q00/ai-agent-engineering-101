"""11-11 16:00 fixes from the first hour of the role operators: refusals shown to the next wake, code_read end/count,
the experiments tool, the step cap from the environment."""
import importlib
import json
import os

from kernel import experiments as ex
from kernel import operator, tools

NOW = 1794380000.0


def test_done_lines_show_a_refused_item_and_an_applied_one():
    done = {"policy": [{"section": "buyer.defaults.food", "key": "step_frac", "value": 0.05},
                       {"section": "buyer.defaults.goods", "key": "step_frac", "value": 0.05}]}
    result = {"policy": [{"ok": False, "error": "17:01까지 시작할 수 없음"}, {"ok": True, "experiment": 21, "eval_kst": "20:00"}]}
    out = operator.done_lines(done, result, NOW)
    assert "거절됨" in out[1] and "17:01" in out[1] and "step_frac" in out[1]
    assert "적용됨" in out[2] and "#21" in out[2]


def test_done_lines_say_when_nothing_was_submitted():
    assert "낸 것 없음" in operator.done_lines({"notes": "x"}, {"note": ""}, NOW)[1]


def test_code_read_takes_end_and_count(tmp_path):
    (tmp_path / "policy.py").write_text("".join(f"x{i} = {i}\n" for i in range(1, 60)))
    a = tools.call("code_read", "operator", str(tmp_path), file="policy.py", start=10, end=12)
    b = tools.call("code_read", "operator", str(tmp_path), file="policy.py", start=10, count=3)
    assert "인자 오류" not in a and "x12 = 12" in a and "x13 = 13" not in a
    assert "x12 = 12" in b and "x13 = 13" not in b


def test_experiments_tool_lists_every_running_one_and_the_block(tmp_path):
    os.makedirs(tmp_path / "a2")
    rows = [{"id": i, "status": "running", "mode": "sequential", "section": f"식품>잎{i}", "scope": f"식품>잎{i}",
             "key": "stock_target", "old": 5, "new": 9, "eval_ts": NOW + 3600, "side": "shop"} for i in range(1, 10)]
    rows.append({"id": 13, "status": "reverted", "mode": "sequential", "section": "buyer.defaults.food", "role": "buyer",
                 "scope": "buyer:food", "key": "open_frac", "old": 0.55, "new": 0.5, "ended_ts": NOW - 600,
                 "side": "buyer", "verdict": {"effect": -3.4, "before_adj": 8.5, "after": 5.0}})
    with open(ex.ledger_path(str(tmp_path)), "w") as f:
        json.dump(rows, f)
    out = tools.call("experiments", "operator", str(tmp_path), now=NOW)
    assert "진행 중 9건" in out and all(f"#{i} " in out for i in range(1, 10))
    assert "#13" in out and "막힘" in out
    assert "experiments" in tools.RECOMMENDED["shop"] and "experiments" in tools.RECOMMENDED["buyer"]


def test_step_cap_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("A2_COMPACT_STEPS", "10")
    assert operator._compact_steps() == 10
    monkeypatch.setenv("A2_COMPACT_STEPS", "99")
    assert operator._compact_steps() == 12
    monkeypatch.delenv("A2_COMPACT_STEPS")
    assert operator._compact_steps() == 6


def test_shop_limit_keys_are_editable_in_shop_only():
    from kernel import policy_edit as pe
    assert pe._spec("max_inventory_cost_frac") == (0.5, 6.0, 0.5)
    assert pe._fits("shop", "max_inventory_cost_frac") and not pe._fits("defaults.food", "max_inventory_cost_frac")
    assert not pe._fits("buyer.defaults.food", "max_inventory_cost_frac")
    for k in ("balance_floor", "allow_bankrupt", "liquidation_line"):
        assert pe._spec(k) is None  # the bankruptcy line stays with people
    assert "max_lines_per_run" in pe.INT_KEYS and "max_inventory_cost_frac" in pe._keys("shop")


def test_shop_limit_key_overlaps_only_the_same_key(tmp_path):
    os.makedirs(tmp_path / "a2")
    rows = [{"id": 5, "status": "running", "mode": "sequential", "section": "식품>음료>주스", "scope": "주스",
             "key": "stock_target", "old": 10, "new": 22, "eval_ts": NOW + 3600},
            {"id": 6, "status": "running", "mode": "sequential", "section": "shop", "scope": "shop:all",
             "key": "max_spend_frac", "old": 0.5, "new": 0.6, "eval_ts": NOW + 3600}]
    with open(ex.ledger_path(str(tmp_path)), "w") as f:
        json.dump(rows, f)
    root = str(tmp_path)
    assert ex.blocked(root, "shop", "max_inventory_cost_frac", NOW) is None
    assert ex.blocked(root, "shop", "max_spend_frac", NOW) is not None
    assert ex.blocked(root, "shop", "grade_floor_list.C", NOW) is not None  # shop:all keys still wait for the others
    assert ex.blocked(root, "식품>음료>주스", "qty_per_buy", NOW) is not None
