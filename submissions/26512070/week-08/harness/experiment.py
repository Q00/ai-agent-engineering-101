"""The experiment ledger (design R12, 13, 20-6 D-6): experiment.register / assign / result.

    py -m harness.experiment                 # the ledger
    py -m harness.experiment result <id>     # the verdict so far, by code

An experiment changes one thing (a playbook patch), names one fixed metric (17-2) and its success line, a period
and a comparison method, all written before it runs and fixed when the user approves it (R12: the proposer does not
set its own bar after the fact). Only one running experiment may touch a metric at a time. A/B splits by the trade
id: hash(order or task id) decides the arm, so both arms run in the same hours (13: method A). The verdict is
computed from the trajectories by code (R14), never by a model.
"""
import hashlib
import json
import sys
import time

from core import atomic
from harness import out_dir, trajectory
from harness.metrics import METRICS

METHODS = ("ab", "alternate", "before_after")      # 13: A, B, C in order of trust
ROLE_OF_METRIC = {m: side for side, ms in METRICS.items() for m in ms}


def path():
    return out_dir() / "experiments.json"


def ledger():
    return atomic.read_json(path(), default=None) or {"experiments": {}}


def register(exp_id, change, metric, success_if, period_h, method="ab", role=None, why="", now=None):
    """A new experiment, status 'proposed' until approve() (the user's approval through harness.wisdom)."""
    if metric not in ROLE_OF_METRIC:
        raise ValueError(f"{metric} is not a fixed metric (17-2): {sorted(ROLE_OF_METRIC)}")
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    if not isinstance(success_if, dict) or success_if.get("direction") not in ("up", "down") \
            or not isinstance(success_if.get("by"), (int, float)):
        raise ValueError('success_if is {"direction": "up"|"down", "by": <relative change, e.g. 0.1>}')
    led = ledger()
    if exp_id in led["experiments"]:
        raise ValueError(f"experiment {exp_id} exists")
    led["experiments"][exp_id] = {"id": exp_id, "change": change, "metric": metric,
                                  "role": role or ROLE_OF_METRIC[metric], "success_if": success_if,
                                  "period_h": period_h, "method": method, "why": why, "status": "proposed",
                                  "registered": now or time.time()}
    atomic.write_json(path(), led)
    return led["experiments"][exp_id]


def approve(exp_id, by, now=None):
    """Start it (status running). Refused when another running experiment uses the same metric (R12)."""
    now = now or time.time()
    led = ledger()
    e = led["experiments"][exp_id]
    clash = [x["id"] for x in led["experiments"].values()
             if x["status"] == "running" and now < x["end"] and x["metric"] == e["metric"]]
    if clash:
        raise ValueError(f"{clash} already runs on {e['metric']}; one experiment per metric at a time")
    e.update(status="running", approved=by, start=now, end=now + e["period_h"] * 3600)
    atomic.write_json(path(), led)
    return e


def stop(exp_id, why, now=None):
    led = ledger()
    e = led["experiments"][exp_id]
    e.update(status="stopped", stopped={"ts": now or time.time(), "why": why})
    atomic.write_json(path(), led)
    return e


def close_ended(now=None):
    """Running experiments past their end become 'ended' with the verdict of result() (11/6: one stayed
    'running' 4 h after its end and blocked the next change on its metric)."""
    now = now or time.time()
    out = []
    for e in list(ledger()["experiments"].values()):
        if e["status"] == "running" and now >= e["end"]:
            r = result(e["id"], now)
            led = ledger()
            led["experiments"][e["id"]].update(status="ended", verdict=r.get("verdict"), result=r)
            atomic.write_json(path(), led)
            out.append((e["id"], r.get("verdict")))
    return out


def arm(exp_id, trade_id):
    """'A' (as now) or 'B' (the change) for one trade: a stable hash, half and half."""
    h = hashlib.sha1(f"{exp_id}:{trade_id}".encode()).digest()[0]
    return "B" if h % 2 else "A"


def running(role=None, now=None):
    now = now or time.time()
    return [e for e in ledger()["experiments"].values()
            if e["status"] == "running" and e["start"] <= now < e["end"] and (role is None or e["role"] == role)]


def assign(role, trade_id, now=None):
    """experiment.assign: {exp_id: arm} for the running A/B experiments of this role, and the playbook patch the
    trade runs under (B arms' changes)."""
    arms, patch = {}, {}
    for e in running(role, now):
        if e["method"] != "ab":
            continue
        a = arm(e["id"], trade_id)
        arms[e["id"]] = a
        if a == "B":
            patch = _merge(patch, e["change"])
    return arms, patch


MIN_WINDOWS = 3              # an alternate verdict needs this many windows in each arm
WINDOW_GAP_MIN = 2           # restock calls within this many minutes of a window's start are the same window


def assign_window(minute, now=None):
    """experiment.assign for restock (method 'alternate', user 11/7 10:4x): each supply window gets one arm, A and B
    in turn, recorded in the ledger as {minute, arm}; ({exp_id: arm}, the B arms' playbook patch) for this window.
    The window runs from its start to the next window's start and its sales count for that arm (result)."""
    now = now or time.time()
    arms, patch = {}, {}
    if minute is None:
        return arms, patch
    led = ledger()
    changed = False
    for e in led["experiments"].values():
        if e["method"] != "alternate" or e["status"] != "running" or not (e["start"] <= now < e["end"]):
            continue
        wins = e.setdefault("windows", [])
        w = next((w for w in wins if abs(w["minute"] - minute) <= WINDOW_GAP_MIN), None)
        if w is None:
            w = {"minute": minute, "arm": "B" if (wins and wins[-1]["arm"] == "A") else "A", "ts": now}
            wins.append(w)
            changed = True
        arms[e["id"]] = w["arm"]
        if w["arm"] == "B":
            patch = _merge(patch, e["change"])
    if changed:
        atomic.write_json(path(), led)
    return arms, patch


# summed over a period, so an arm is compared per window; the rest are rates over the arm's trades
SUM_METRICS = {"revenue", "book_change", "visits", "sales", "storage", "waste", "surplus", "orders", "settled",
               "posts", "model_calls", "anomalies", "tool_failures", "tool_failures_ours"}


def _alternate(e, now):
    """Per arm: the metric over the windows it had (a window = its start to the next window's start, the last one
    to min(now, end)); a summed metric is the mean per window."""
    wins = sorted(e.get("windows") or [], key=lambda w: w["minute"])
    stop = min(now, e["end"], (e.get("stopped") or {}).get("ts") or now)
    spans = [(w["arm"], w["minute"] * 60, (wins[i + 1]["minute"] * 60 if i + 1 < len(wins) else stop))
             for i, w in enumerate(wins)]
    spans = [(a, s, min(t, stop)) for a, s, t in spans if s < stop]
    role = "buyer" if e["role"] == "buyer" else "seller"
    days = sorted({time.strftime("%Y%m%d", time.localtime(t)) for _, s, u in spans for t in (s, u)})
    trs = [t for d in days for t in trajectory.build(d).values() if t["role"] == role]
    out = {}
    for arm in ("A", "B"):
        mine = [(s, u) for a, s, u in spans if a == arm]
        pick = [t for t in trs if any(s <= t["start"] < u for s, u in mine)]
        v = _value(e["metric"], pick)
        if v is not None and e["metric"] in SUM_METRICS and mine:
            v = round(v / len(mine), 1)
        out[arm] = {"value": v, "n": len(pick), "windows": len(mine)}
    return out


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out.get(k) or {}, v) if isinstance(v, dict) else v
    return out


def _value(metric, trs):
    """The metric over a list of trajectories (the trade-level metrics of 17-2)."""
    from harness.metrics import compute
    m = compute(trs, [], 0, 1e12)
    return m[ROLE_OF_METRIC[metric]].get(metric)


def result(exp_id, now=None):
    """experiment.result: per arm the metric, n, and the verdict by the success line fixed at approval."""
    now = now or time.time()
    e = ledger()["experiments"][exp_id]
    if e.get("start") is None:
        return {"id": exp_id, "status": e["status"], "verdict": "not started"}
    if e.get("stopped"):                    # after a stop every trade runs one way: those trades are not A vs B
        now = min(now, e["stopped"]["ts"])
    days = sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in
                   range(int(e["start"]), int(min(now, e["end"])) + 86400, 86400)})
    role = "buyer" if e["role"] == "buyer" else "seller"
    trs = [t for d in days for t in trajectory.build(d).values()
           if t["role"] == role and e["start"] <= t["start"] < min(now, e["end"])]
    if e["method"] == "before_after":
        # the fast path (harness.fastpath): the same length of time just before the change is the baseline
        span = min(now, e["end"]) - e["start"]
        bdays = sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (e["start"] - span, e["start"])})
        before = [t for d in bdays for t in trajectory.build(d).values()
                  if t["role"] == role and e["start"] - span <= t["start"] < e["start"]]
        vb, va = _value(e["metric"], before), _value(e["metric"], trs)
        out = {"id": exp_id, "status": e["status"], "metric": e["metric"], "before": vb, "after": va,
               "n_before": len(before), "n_after": len(trs), "success_if": e["success_if"]}
        if vb in (None, 0) or va is None or min(len(before), len(trs)) < 30:
            out["verdict"] = "too few trades yet"
            return out
        change = (va - vb) / abs(vb)
        want = e["success_if"]["by"] * (1 if e["success_if"]["direction"] == "up" else -1)
        ok = change >= want if want >= 0 else change <= want
        out["change"] = round(change, 3)
        out["verdict"] = ("success" if ok else "no effect") if now >= e["end"] else ("on track" if ok else "not yet")
        return out
    if e["method"] == "alternate":
        arms = _alternate(e, now)
        out = {"id": exp_id, "status": e["status"], "metric": e["metric"], "A": arms["A"], "B": arms["B"],
               "success_if": e["success_if"]}
        va, vb = arms["A"]["value"], arms["B"]["value"]
        if va in (None, 0) or vb is None or min(arms["A"]["windows"], arms["B"]["windows"]) < MIN_WINDOWS:
            out["verdict"] = "too few windows yet"
            return out
        change = (vb - va) / abs(va)
        want = e["success_if"]["by"] * (1 if e["success_if"]["direction"] == "up" else -1)
        ok = change >= want if want >= 0 else change <= want
        out["change"] = round(change, 3)
        out["verdict"] = ("success" if ok else "no effect") if now >= e["end"] else ("on track" if ok else "not yet")
        return out
    if e["method"] != "ab":
        return {"id": exp_id, "status": e["status"], "n": len(trs), "verdict": "unknown method"}
    by = {"A": [t for t in trs if arm(exp_id, t["id"]) == "A"], "B": [t for t in trs if arm(exp_id, t["id"]) == "B"]}
    va, vb = _value(e["metric"], by["A"]), _value(e["metric"], by["B"])
    out = {"id": exp_id, "status": e["status"], "metric": e["metric"], "A": {"value": va, "n": len(by["A"])},
           "B": {"value": vb, "n": len(by["B"])}, "success_if": e["success_if"]}
    if va in (None, 0) or vb is None or min(len(by["A"]), len(by["B"])) < 30:
        out["verdict"] = "too few trades yet"
        return out
    change = (vb - va) / abs(va)
    out["change"] = round(change, 3)
    want = e["success_if"]["by"] * (1 if e["success_if"]["direction"] == "up" else -1)
    done = now >= e["end"]
    ok = change >= want if want >= 0 else change <= want
    out["verdict"] = ("success" if ok else "no effect") if done else ("on track" if ok else "not yet")
    return out


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "result":
        print(json.dumps(result(sys.argv[2]), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(ledger(), ensure_ascii=False, indent=1))
