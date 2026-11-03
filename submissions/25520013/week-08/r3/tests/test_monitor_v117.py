"""v1.17 monitor copy (tools/monitor.py here; r3/tools/monitor.py untouched): restock_error / plan_error /
policy_reload_failed -> WATCH, the last 2 restock cycles failed -> ACT; v1.16.1 monitor said OK exit 0 for the same
rows (shown side by side). Subprocess runs with PYTHONPATH=r3 (policy.py), fixed --now, offline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
R3 = HERE
NEW = HERE / "tools" / "monitor.py"
OLD = Path("/workspace/course/r3/tools/monitor.py")   # the untouched v1.16.1 copy, only in the Grok staging env
needs_old = pytest.mark.skipif(not OLD.exists(), reason="v1.16.1 monitor stayed in the Grok staging env")
NOW = 1_800_000_000.0
GOODS = [{"unit": "g1", "sku": "G00001", "kind": "goods", "cost": 10000, "grade": "A"}]

BUY = {"hook": "restock", "decision": "restock_plan", "outcome": {"lines": 1, "qty": 2}}
PLAN_ERR = {"hook": "restock", "decision": "restock_plan", "outcome": {"why": "plan_error", "lines": 0, "qty": 0}}
RS_ERR = {"hook": "restock", "decision": "restock_error", "outcome": {"error": "boom", "lines": 0, "qty": 0}}
RELOAD = {"hook": "seller", "decision": "policy_reload_failed", "outcome": {"hash": "x", "error": "bad", "kept": "y"}}
ACCEPT = {"hook": "seller", "decision": "accept", "outcome": {"sku": "G1", "price": 300, "cost": 200,
                                                              "below_cost": False}}


def agent(tmp_path, *rows):
    d = tmp_path / "agent"
    (d / ".market").mkdir(parents=True, exist_ok=True)
    (d / ".r3").mkdir(exist_ok=True)
    (d / ".market" / "inventory.json").write_text(json.dumps(
        {"balance": 4_800_000, "bankrupt_line": 500_000, "units": GOODS, "storage_per_day": 0.01}))
    (d / ".r3" / "activity.json").write_text(json.dumps(
        {"settles": 5, "last_settle_ts": NOW - 60, "orders": 9, "last_order_ts": NOW - 60}))
    (d / ".r3" / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def mon(d, which=NEW):
    env = dict(os.environ, PYTHONPATH=str(R3), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(which), "--dir", str(d), "--now", str(NOW),
                           "--policy", str(R3 / "policy.json")], capture_output=True, text=True, timeout=60, env=env)


def test_clean_is_ok_in_both(tmp_path):
    d = agent(tmp_path, BUY, ACCEPT, BUY)
    for w in (OLD, NEW) if OLD.exists() else (NEW,):
        r = mon(d, w)
        assert r.returncode == 0 and "OK all thresholds" in r.stdout, (w, r.stdout, r.stderr)
    assert "error signals in the last 2000 metrics lines {}" in mon(d).stdout


@pytest.mark.parametrize("rows,kinds", [
    ((BUY, RS_ERR, BUY), {"restock_error": 1}),
    ((BUY, PLAN_ERR, BUY), {"plan_error": 1}),
    ((BUY, RELOAD, BUY), {"policy_reload_failed": 1}),
    ((PLAN_ERR, RELOAD, RELOAD, BUY), {"plan_error": 1, "policy_reload_failed": 2}),
    ((RS_ERR, BUY), {"restock_error": 1}),           # one failed cycle only (the last cycle succeeded)
    ((BUY, RS_ERR), {"restock_error": 1})])          # last cycle failed but only 1 in a row
def test_signals_are_watch_old_was_ok(tmp_path, rows, kinds):
    d = agent(tmp_path, *rows)
    if OLD.exists():
        old = mon(d, OLD)
        assert old.returncode == 0 and "OK all thresholds" in old.stdout      # the v1.16.1 gap
    r = mon(d)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"error signals in the last 2000 metrics lines {kinds}" in r.stdout
    assert f"WATCH error signals {kinds}" in r.stdout and "OK all thresholds" not in r.stdout


@pytest.mark.parametrize("rows", [(BUY, PLAN_ERR, PLAN_ERR), (BUY, RS_ERR, PLAN_ERR), (RS_ERR, RS_ERR),
                                  (BUY, PLAN_ERR, RELOAD, ACCEPT, RS_ERR)])
def test_two_failed_cycles_in_a_row_act(tmp_path, rows):
    r = mon(agent(tmp_path, *rows))
    assert r.returncode == 2 and "ACT the last 2 restock cycles failed" in r.stdout, (r.stdout, r.stderr)


def test_failed_then_recovered_is_watch_not_act(tmp_path):
    r = mon(agent(tmp_path, PLAN_ERR, PLAN_ERR, BUY))
    assert r.returncode == 1 and "ACT the last" not in r.stdout


def test_other_checks_unchanged_below_cost_still_act(tmp_path):
    below = dict(ACCEPT, outcome=dict(ACCEPT["outcome"], below_cost=True))
    r = mon(agent(tmp_path, BUY, below, RELOAD))
    assert r.returncode == 2 and "ACT 1 below-cost rows" in r.stdout and "WATCH error signals" in r.stdout


def test_bad_outcome_types_do_not_crash(tmp_path):
    rows = ({"decision": "restock_plan", "outcome": "x"}, {"decision": "restock_plan", "outcome": None},
            {"decision": "restock_error"}, {"decision": None})
    r = mon(agent(tmp_path, *rows))
    assert r.returncode in (1, 2) and "CRASH" not in r.stdout and "Traceback" not in r.stderr


@needs_old
def test_copy_diff_is_only_the_v117_block():
    old, new = OLD.read_text().splitlines(), NEW.read_text().splitlines()
    import difflib
    removed = [l for l in difflib.unified_diff(old, new, lineterm="", n=0) if l.startswith("-") and
               not l.startswith("---")]
    assert removed == ['-activity.json own_accepts). Scope own only, like the rest of the accept/settle rule."""']
