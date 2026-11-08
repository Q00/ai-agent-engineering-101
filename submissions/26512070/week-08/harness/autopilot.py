"""Autopilot (user 11/8 12:3x: "from now on judge and improve everything yourself, without my approval; A/B tests
and every change are decided and run every 30 minutes"). It replaces the user's approval (R8) and the fast path.

    py -m harness.autopilot            # on/off and the autopilot experiments
    py -m harness.autopilot off|on     # the user's switch (memory/harness/autopilot.json)

Every meta request is judged by code here, at once, day or night:
- refused when it is not one playbook number, breaks the schema or an approved range, moves a number with no approved
  range more than FREE_STEP, or its metric already has a running experiment (the next cycle may propose it again);
- otherwise it starts as a test that ends at a 30-minute slot boundary (SLOT_MIN, at least MIN_RUN_MIN after the
  start). A/B and alternate tests touch half the trades; a before_after test installs the change at once, so it
  needs the audit's agreement - without it the test runs as an A/B instead.
settle() runs at each runner pass before the meta cycle, so every boundary first decides the tests that ended:
- success: an A/B or alternate change is installed into the strategy; a before_after change stays;
- no effect: nothing is installed / the before_after change is undone;
- too few trades: one more slot, at most MAX_EXTEND times, then as 'no effect'.
The success line, the metric and the verdict stay code's (R12, R14); only the user's approval step is gone.
"""
import json
import sys
import time

from core import atomic
from harness import alert, experiment, out_dir, wisdom

SLOT_MIN = 30
MIN_RUN_MIN = 25              # a test started at :07 ends at :30 of the next half hour, not 23 min later
MAX_EXTEND = 3                # 'too few trades' gets at most 3 more slots (2 h in all)
FREE_STEP = 0.3               # a number with no approved range moves at most 30% of its value per change


def _path():
    return out_dir() / "autopilot.json"


def on():
    return bool((atomic.read_json(_path(), default=None) or {}).get("on"))


def switch(state, by="user"):
    atomic.write_json(_path(), {"on": state, "by": by, "since": time.time()})


def slot_end(now):
    t = int(now + MIN_RUN_MIN * 60)
    tm = time.localtime(t)
    base = t - (tm.tm_min % SLOT_MIN) * 60 - tm.tm_sec
    return base + SLOT_MIN * 60 if base < t else base


def judge(doc, now):
    """Reasons to refuse a request (empty: it runs)."""
    why = []
    leaves = list(wisdom._leaves(doc["change"]))
    if len(leaves) != 1 or leaves[0][0][0] != "playbook":
        return ["not one playbook number"]
    path, new = leaves[0]
    for c in doc.get("checks") or []:
        if "no approved range yet" in c:
            cur = wisdom._get(wisdom.active(), path)
            if isinstance(new, bool) or isinstance(cur, bool):
                continue                            # a switch: the test itself is the guard
            if not isinstance(new, (int, float)) or not isinstance(cur, (int, float)) or not cur:
                why.append(f"{c}; no current value to move from")
            elif abs(new - cur) / abs(cur) > FREE_STEP + 1e-9:
                why.append(f"moves {abs(new - cur) / abs(cur):.0%} (> {FREE_STEP:.0%} without an approved range)")
        else:
            why.append(c)
    metric = (doc.get("expected") or {}).get("metric")
    busy = [e["id"] for e in experiment.running(now=now) if e["metric"] == metric]
    if busy:
        why.append(f"{busy[0]} already runs on {metric}")
    return why


def take(doc, now=None):
    """Start an approved-by-code request as a 30-minute test. Returns the outcome."""
    now = now or time.time()
    why = judge(doc, now)
    if why:
        doc.update(status="rejected", decision={"choice": "reject", "by": "autopilot", "ts": now, "note": "; ".join(why)})
        atomic.write_json(wisdom._dir() / f"{doc['id']}.json", doc)
        wisdom._log({"ts": now, "event": "decision", "id": doc["id"], "choice": "reject", "by": "autopilot",
                     "note": "; ".join(why)})
        return {"auto": False, "why": why}
    ev = dict(doc.get("evaluation") or {})
    agrees = isinstance(doc.get("audit"), dict) and doc["audit"].get("agrees") is True
    method = ev.get("method") if ev.get("method") in experiment.METHODS else "ab"
    if method == "before_after" and not agrees:
        method = "ab"                               # no audit agreement: half the trades, not all of them
    if method == "alternate" and experiment.ROLE_OF_METRIC.get(doc["expected"]["metric"]) == "buyer":
        method = "ab"                               # alternate runs by restock windows only
    success = wisdom.success_line(doc)
    end = slot_end(now)
    exp_id = f"a{doc['id']}"
    experiment.register(exp_id, doc["change"], doc["expected"]["metric"], success, (end - now) / 3600, method,
                        why=doc["title"], now=now)
    experiment.approve(exp_id, f"autopilot: request {doc['id']}", now=now)
    installed = None
    if method == "before_after":
        doc["evaluation"] = dict(ev, method="before_after", period_h=0)
        atomic.write_json(wisdom._dir() / f"{doc['id']}.json", doc)
        installed = wisdom.decide(doc["id"], "approve", note="autopilot: before/after test", by="autopilot",
                                  now=now).get("installed")
    else:
        doc.update(status="approved", decision={"choice": "approve", "by": "autopilot", "ts": now,
                                                "experiment": exp_id})
        atomic.write_json(wisdom._dir() / f"{doc['id']}.json", doc)
        wisdom._log({"ts": now, "event": "decision", "id": doc["id"], "choice": "approve", "by": "autopilot",
                     "experiment": exp_id})
    led = experiment.ledger()
    led["experiments"][exp_id].update(end=end, autopilot=True, request=doc["id"], installed=installed, extends=0,
                                      undo=dict(doc["base"]))
    atomic.write_json(experiment.path(), led)
    alert.send(f"[자율 시작] {doc['title']} ({method}, {time.strftime('%H:%M', time.localtime(end))} 판정)", "page", now)
    return {"auto": True, "experiment": exp_id, "method": method, "installed": installed}


def _patch(flat):
    change = {}
    for k, v in flat.items():
        d = change
        parts = k.split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = v
    return change


def _install(title, change, why, metric, now):
    req = wisdom.request({"title": title, "urgency": "page", "change": change, "why": why,
                          "expected": {"metric": metric, "direction": "up", "size": "as tested"},
                          "evaluation": {"method": "before_after", "period_h": 0,
                                         "success_if": {"direction": "up", "by": 0}},
                          "risk": "tested by code", "undo": "-"}, by="autopilot", now=now)
    return wisdom.decide(req["id"], "approve", note="autopilot: settled by code", by="autopilot", now=now)


def settle(now=None):
    """Decide the autopilot tests whose slot ended (each runner pass, before the meta cycle)."""
    now = now or time.time()
    done = []
    for e in list(experiment.ledger()["experiments"].values()):
        if not (e.get("autopilot") and e["status"] == "running" and now >= e["end"]):
            continue
        r = experiment.result(e["id"], now)
        v = r.get("verdict")
        if v in ("too few trades yet", "too few windows yet") and e.get("extends", 0) < MAX_EXTEND:
            led = experiment.ledger()
            led["experiments"][e["id"]].update(end=slot_end(now), extends=e.get("extends", 0) + 1)
            atomic.write_json(experiment.path(), led)
            done.append((e["id"], "extended"))
            continue
        line = f"{e['metric']} {r.get('A', {}).get('value', r.get('before'))} -> {r.get('B', {}).get('value', r.get('after'))}"
        try:
            if v == "success" and e["method"] != "before_after":
                out = _install(f"adopt: {e['why']}", e["change"], f"test {e['id']} success ({line})", e["metric"], now)
                experiment.stop(e["id"], f"success, adopted v{out.get('installed')}", now)
                alert.send(f"[자율 채택] {e['why']}: {line} (v{out.get('installed')})", "page", now)
                done.append((e["id"], "adopted"))
            elif v == "success":
                experiment.stop(e["id"], "success, kept", now)
                alert.send(f"[자율 유지] {e['why']}: {line}", "page", now)
                done.append((e["id"], "kept"))
            elif e["method"] == "before_after":
                out = _install(f"undo: {e['why']}", _patch(e["undo"]), f"test {e['id']}: {v} ({line})", e["metric"], now)
                experiment.stop(e["id"], f"{v}, undone v{out.get('installed')}", now)
                alert.send(f"[자율 되돌림] {e['why']}: {v} ({line})", "page", now)
                done.append((e["id"], "undone"))
            else:
                experiment.stop(e["id"], f"{v}, not adopted", now)
                done.append((e["id"], "dropped"))
        except ValueError as x:                     # the number moved since: leave the strategy as it is
            experiment.stop(e["id"], f"{v}, not applied: {x}", now)
            alert.send(f"[자율 적용 실패] {e['why']}: {x}", "now", now)
            done.append((e["id"], "left"))
    return done


if __name__ == "__main__":
    if sys.argv[1:2] in (["on"], ["off"]):
        switch(sys.argv[1] == "on")
    print(json.dumps({"on": on(), "experiments": [
        {k: e.get(k) for k in ("id", "status", "method", "metric", "change", "end", "extends", "stopped")}
        for e in experiment.ledger()["experiments"].values() if e.get("autopilot")]}, ensure_ascii=False, indent=1,
        default=str))
