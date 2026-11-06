"""Operations (design 9, 17-4, R9, R13): ops.health, ops.emergency, model.usage.

    py -m harness.ops                        # one health pass (the runner does it every 5 min)
    py -m harness.ops emergency              # what is stopped
    py -m harness.ops clear restock_stop     # the user undoes an emergency action

Health: the measured findings of outer/health.py plus whether the parts are alive (the kit log, the inner status),
graded now / warn; one alert per finding and hour.
Emergency (17-4, approved in advance, at night too): restock_stop when the market rules change, forum_stop when the
forum turn keeps failing. An action is a flag in memory/bus/emergency.json that the role reads (restock.py, the
runner's forum turn); it never changes prices or the strategy, it alerts at once, and only the user clears it.
Model usage (R13, 17-5): calls per seat in the last hour against the budget; 80% is a warning.
"""
import json
import sys
import time

from core import atomic, paths
from harness import alert

ACTIONS = ("restock_stop", "sku_stop", "buyer_pause", "forum_stop", "models_stop")     # 17-4
NOW_FINDINGS = ("the shop sold nothing", "the buyer paid nothing")                  # the rest are warnings
# 17-5; strategy 2 -> 16 (user 11/6 10:3x): the meta-harness proposes in the tool loop, up to 8 calls a cycle
BUDGET_PER_HOUR = {"seller": 30, "buyer": 30, "analyst": 12, "strategy": 16, "audit": 2, "forum": 0}
STALE_S = 600


def _state_path():
    return paths.memory() / "harness" / "ops_state.json"


def emergency_path():
    return paths.bus() / "emergency.json"


def emergency():
    return atomic.read_json(emergency_path(), default=None) or {}


def stopped(action):
    """For the roles: is this pre-approved emergency action in force?"""
    return bool(emergency().get(action))


def trigger(action, why, now=None):
    if action not in ACTIONS:
        raise ValueError(f"not a pre-approved action: {action}")
    now = now or time.time()
    e = emergency()
    if e.get(action):
        return False
    e[action] = {"since": now, "why": why}
    atomic.write_json(emergency_path(), e)
    alert.send(f"[비상 동작] {action}: {why}\n되돌리기: py -m harness.ops clear {action}", "now", now)
    return True


def clear(action, now=None):
    e = emergency()
    was = e.pop(action, None)
    atomic.write_json(emergency_path(), e)
    if was:
        alert.send(f"[해제] {action} (사람이 되돌림)", "page", now)
    return was


def model_usage(now=None):
    """model.usage: calls per seat in the last hour, and the share of the hourly budget."""
    now = now or time.time()
    from harness.trajectory import read_day
    days = sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (now - 3600, now)})
    n = {}
    for d in days:
        for e in read_day(d):
            if e["kind"] == "model.call" and now - 3600 <= e["ts"] <= now:
                seat = (e.get("data") or {}).get("seat") or "?"
                n[seat] = n.get(seat, 0) + 1
    return {s: {"calls": c, "budget": BUDGET_PER_HOUR.get(s),
                "share": round(c / BUDGET_PER_HOUR[s], 2) if BUDGET_PER_HOUR.get(s) else None} for s, c in n.items()}


def health(now=None, rules_changed=False):
    """ops.health: findings with their level; alerts once per finding and hour; emergency actions when due."""
    now = now or time.time()
    from outer import health as h
    doc = h.check(now)
    found = [("now" if f.startswith(NOW_FINDINGS) else "warn", f) for f in doc["findings"]]
    kit = paths.root() / "logs" / "market-kit.log"
    if kit.exists() and now - kit.stat().st_mtime > STALE_S:
        found.append(("now", f"the kit log has not moved for {int((now - kit.stat().st_mtime) // 60)} min"))
    inner = paths.bus() / "status" / "inner.json"
    if inner.exists() and now - inner.stat().st_mtime > STALE_S:
        found.append(("now", f"the inner runtime's status is {int((now - inner.stat().st_mtime) // 60)} min old"))
    for seat, u in model_usage(now).items():
        if u["share"] is not None and u["share"] >= 0.8:
            found.append(("warn", f"model seat {seat}: {u['calls']} calls in 1 h, {u['share']:.0%} of the budget"))
    st = atomic.read_json(_state_path(), default=None) or {}
    sent = st.setdefault("alerted", {})
    for level, f in found:
        key = f.split(":")[0][:40]
        if now - sent.get(key, 0) > 3600:
            alert.send(f"[{'긴급' if level == 'now' else '경고'}] {f}", level, now)
            sent[key] = now
    if rules_changed:
        trigger("restock_stop", "the market's rules changed: no buying until the user reads them", now)
    atomic.write_json(_state_path(), st)
    return {"findings": found, "emergency": emergency()}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["emergency"]:
        print(json.dumps(emergency(), ensure_ascii=False, indent=1))
    elif a[:1] == ["clear"]:
        print(json.dumps(clear(a[1]), ensure_ascii=False))
    else:
        print(json.dumps(health(), ensure_ascii=False, indent=1, default=str))
