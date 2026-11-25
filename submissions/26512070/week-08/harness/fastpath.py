"""The fast path (user 11/6 11:0x, an exception to R8 the user set): a change with sure evidence is installed at once
and judged by code against the hours before it; if it misses its success line it is undone by itself.

Eligible only when ALL hold (checked by code, R14):
- one playbook number, moved at most MAX_STEP of its current value (a number with no current value is not eligible);
- the evidence is observed: the request's 'why' names a trade id (o-..., a seller task) or an observed knowledge item
  that exists in our records;
- the audit (another vendor's model) agreed;
- no running experiment on the same metric;
- daytime (DAY_HOURS, R9: nothing changes the wisdom at night);
- the evaluation is before_after with a success line.
Then: install now (a new strategy version, approved "auto"), an alert, and a 'before_after' experiment that compares
the metric over the same length of time before and after. settle() (the runner, every pass) closes it when its period
ends: success keeps it, anything else installs the old value back and alerts.
"""
import re
import time

from harness import alert, experiment, knowledge, trajectory, wisdom

MAX_STEP = 0.2
DAY_HOURS = range(8, 24)
PERIOD_H = 3                                  # the default and the cap for an auto change's test


def _evidence_ok(why, now):
    day = time.strftime("%Y%m%d", time.localtime(now))
    for tid in re.findall(r"\bo-[0-9a-f]{12}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", why or ""):
        if trajectory.get_one(tid, day) is not None:
            return True
    for kid in re.findall(r"\b[\w.-]+:[\w.:-]+\b", why or ""):
        for it in knowledge.get(about=kid, now=now) + [i for i in knowledge.get(now=now) if i["id"] == kid]:
            if it["grade"] == "observed":
                return True
    return False


def eligible(doc, now=None):
    """(ok, reasons why not)."""
    now = now or time.time()
    why = []
    leaves = list(wisdom._leaves(doc["change"]))
    if len(leaves) != 1 or leaves[0][0][0] != "playbook":
        why.append("not one playbook number")
    else:
        path, new = leaves[0]
        cur = wisdom._get(wisdom.active(), path)
        if isinstance(cur, bool) or not isinstance(cur, (int, float)) or not cur or not isinstance(new, (int, float)):
            why.append("no current numeric value to move from")
        elif abs(new - cur) / abs(cur) > MAX_STEP + 1e-9:
            why.append(f"moves {abs(new - cur) / abs(cur):.0%} (> {MAX_STEP:.0%})")
    if not _evidence_ok(doc.get("why"), now):
        why.append("no observed evidence named (a trade id or an observed knowledge id)")
    a = doc.get("audit")
    if not (isinstance(a, dict) and a.get("agrees") is True):
        why.append("the audit did not agree")
    metric = (doc.get("expected") or {}).get("metric")
    if any(e["metric"] == metric for e in experiment.running(now=now)):
        why.append(f"an experiment already runs on {metric}")
    if time.localtime(now).tm_hour not in DAY_HOURS:
        why.append("night (R9)")
    ev = doc.get("evaluation") or {}
    if ev.get("method") != "before_after" or not isinstance(ev.get("success_if"), dict):
        why.append("evaluation is not before_after with a success line")
    return not why, why


def try_apply(doc, now=None):
    """Install an eligible request at once. Returns the outcome, or the reasons it waits for the user."""
    now = now or time.time()
    ok, why = eligible(doc, now)
    if not ok:
        return {"auto": False, "why": why}
    ev = doc["evaluation"]
    period = min(float(ev.get("period_h") or PERIOD_H), PERIOD_H)
    out = wisdom.decide(doc["id"], "approve", note="fast path: sure evidence, before/after test", by="auto", now=now)
    exp_id = f"f{doc['id']}"
    experiment.register(exp_id, doc["change"], doc["expected"]["metric"], ev["success_if"], period,
                        "before_after", why=doc["title"], now=now)
    experiment.approve(exp_id, f"auto: request {doc['id']}", now=now)
    led = experiment.ledger()
    led["experiments"][exp_id].update(auto=True, request=doc["id"], installed=out.get("installed"),
                                      undo={".".join(p): v for p, v in
                                            ((tuple(k.split(".")), v) for k, v in doc["base"].items())})
    from core import atomic
    atomic.write_json(experiment.path(), led)
    alert.send(f"[자동 적용] {doc['title']} (v{out.get('installed')}), {period:g}시간 뒤 전후 비교, 미달이면 되돌림", "now", now)
    return {"auto": True, "installed": out.get("installed"), "experiment": exp_id}


def settle(now=None):
    """Close the auto changes whose period ended: keep on success, undo otherwise."""
    now = now or time.time()
    done = []
    for e in list(experiment.ledger()["experiments"].values()):
        if not (e.get("auto") and e["status"] == "running" and now >= e["end"]):
            continue
        r = experiment.result(e["id"], now)
        if r.get("verdict") == "success":
            experiment.stop(e["id"], f"success: {r.get('change')}", now)
            alert.send(f"[자동 적용 유지] {e['why']}: {e['metric']} {r.get('before')} -> {r.get('after')}", "page", now)
            done.append((e["id"], "kept"))
            continue
        change = {}
        for k, v in e["undo"].items():
            d = change
            parts = k.split(".")
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = v
        try:
            req = wisdom.request({"title": f"undo: {e['why']}", "urgency": "page", "change": change,
                                  "why": f"fast-path test {e['id']}: {r.get('verdict')} ({r.get('before')} -> "
                                         f"{r.get('after')})",
                                  "expected": {"metric": e["metric"], "direction": "back", "size": "as before"},
                                  "evaluation": {"method": "before_after", "period_h": 0,
                                                 "success_if": {"direction": "up", "by": 0}},
                                  "risk": "none: the value in force before", "undo": "-"}, by="auto", now=now)
            wisdom.decide(req["id"], "approve", note="fast path: missed its success line", by="auto-undo", now=now)
            experiment.stop(e["id"], f"undone: {r.get('verdict')}", now)
            alert.send(f"[자동 되돌림] {e['why']}: {r.get('verdict')} ({r.get('before')} -> {r.get('after')})", "now", now)
            done.append((e["id"], "undone"))
        except ValueError as x:                     # someone changed the number since: leave it to the user
            experiment.stop(e["id"], f"not undone: {x}", now)
            alert.send(f"[되돌림 실패] {e['why']}: {x}", "now", now)
            done.append((e["id"], "left"))
    return done
