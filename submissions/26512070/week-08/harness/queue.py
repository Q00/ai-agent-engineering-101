"""Approved changes that wait their turn (user 11/6 12:4x: start the food test when the shop's price test ends, so
two changes never move one metric at once - R12).

    py -m harness.queue            # what waits

An entry is a request the user already approved, with the time it may start and the experiment it waits for. The
runner calls tick() every pass: once both are past, the request is installed and, for a before_after evaluation,
a before/after experiment is opened so code compares the hours before and after (it is not undone by itself: the
user approved it; the result is reported).
"""
import json
import time

from core import atomic
from harness import alert, experiment, out_dir, wisdom


def path():
    return out_dir() / "queue.json"


def entries():
    return atomic.read_json(path(), default=None) or []


def add(rid, after=None, after_experiment=None, by="user"):
    doc = wisdom.status(rid)
    if doc and doc.get("status") == "open":       # approved already: not waiting for the user any more
        doc["status"] = "queued"
        atomic.write_json(wisdom._dir() / f"{rid}.json", doc)
    q = entries() + [{"request": rid, "after": after, "after_experiment": after_experiment, "by": by,
                      "added": time.time()}]
    atomic.write_json(path(), q)
    return q[-1]


def tick(now=None):
    # 11/6 22:3x: a runner's 'now' from the start of its loop was older than an experiment started meanwhile, so
    # the experiment looked not started and top_n went in on top of food_top (one change per metric, R12) - the
    # clock is read here, and an entry waits while its experiment, or any on its metric, is still running
    now = max(now or 0, time.time())
    left, done = [], []
    live = [e for e in experiment.ledger()["experiments"].values() if e["status"] == "running" and now < e["end"]]
    for q in entries():
        doc = wisdom.status(q["request"])
        metric = ((doc or {}).get("expected") or {}).get("metric")
        if ((q.get("after") and now < q["after"]) or any(e["id"] == q.get("after_experiment") for e in live)
                or any(e["metric"] == metric for e in live)):
            left.append(q)
            continue
        if not doc or doc.get("status") not in ("open", "queued"):
            done.append((q["request"], f"skipped: {doc and doc.get('status')}"))
            continue
        try:
            out = wisdom.decide(q["request"], "approve", note="queued approval, its turn came", by=q["by"], now=now)
            ev = doc["evaluation"]
            if ev.get("method") == "before_after" and not out.get("experiment"):
                exp_id = f"q{q['request']}"
                experiment.register(exp_id, doc["change"], doc["expected"]["metric"], ev["success_if"],
                                    ev.get("period_h") or 6, "before_after", why=doc["title"], now=now)
                experiment.approve(exp_id, f"{q['by']} (queued): request {q['request']}", now=now)
            alert.send(f"[예약 적용] {doc['title']} (v{out.get('installed')})", "now", now)
            done.append((q["request"], "installed"))
        except ValueError as e:
            alert.send(f"[예약 실패] {doc['title']}: {e}", "now", now)
            done.append((q["request"], f"failed: {e}"))
    # re-read before writing: an entry added while this tick ran is kept (11/6: the food_top entry vanished)
    handled = {r for r, _ in done}
    seen = {q["request"] for q in left} | handled
    left += [q for q in entries() if q["request"] not in seen]
    atomic.write_json(path(), left)
    return done


if __name__ == "__main__":
    print(json.dumps(entries(), ensure_ascii=False, indent=1))
