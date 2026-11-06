"""Wisdom and the approval path (design R8, 17-3): wisdom.get, approval.request, approval.status, decide.

    py -m harness.wisdom                         # what waits for a decision
    py -m harness.wisdom show <id>
    py -m harness.wisdom decide <id> approve|reject|revise [--note "..."]

A request holds ONE change as a patch of the strategy (e.g. {"playbook": {"buyer": {"ceiling_share": 0.85}}}), not a
whole strategy: the old briefs held the strategy as of their writing, and installing one late undid every version
since (outer/decide.py --stale-ok). On approval the patch goes onto the strategy in force at that moment; if a value
it changes was changed by someone else after the request, the decision is refused (decide again on fresh numbers).
A request whose evaluation is an A/B experiment is not installed: the approval starts the experiment (harness.
experiment) and the trading side runs the B arm on half of the trades.

Format (17-3): title, urgency now|page, change, why (knowledge / trajectory ids), expected (metric, direction, size),
evaluation (method, period_h, success_if), risk, undo, dispute (both sides, when the audit disagreed), audit.
Every request and decision is a line in memory/harness/decisions.jsonl; urgency 'now' also goes to the phone.
"""
import copy
import json
import sys
import time

from core import atomic, paths, schema, strategy
from core.events import EventWriter
from harness import capability, experiment, out_dir

REQUIRED = ("title", "urgency", "change", "why", "expected", "evaluation", "risk", "undo")


def _dir():
    d = out_dir() / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(line):
    atomic.append_line(out_dir() / "decisions.jsonl", json.dumps(line, ensure_ascii=False))


def active():
    return atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}


def wisdom_get():
    """wisdom.get: the strategy in force, the approved ranges, the running experiments and the last decisions."""
    led = out_dir() / "decisions.jsonl"
    last = led.read_text(encoding="utf-8").splitlines()[-10:] if led.exists() else []
    s = active()
    return {"strategy_version": s.get("version"), "approved": s.get("approved"), "playbook": s.get("playbook"),
            "limits": s.get("limits"), "allowed_ranges": capability.allowed_ranges(),
            "experiments": experiment.running(), "last_decisions": [json.loads(x) for x in last]}


def _leaves(patch, prefix=()):
    for k, v in patch.items():
        if isinstance(v, dict) and v:
            yield from _leaves(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _get(doc, path):
    for k in path:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(k)
    return doc


def _apply(doc, patch):
    out = copy.deepcopy(doc)
    for path, v in _leaves(patch):
        d = out
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = v
    return out


def check_change(change):
    """What the capability list says about a change: numbers the code does not read, or outside an approved range."""
    cap = capability.capability_list()["numbers"]
    notes = []
    for path, v in _leaves(change):
        if path[0] != "playbook":
            notes.append(f"{'.'.join(path)}: not a playbook number (outside the capability list)")
            continue
        row = ((cap.get(path[1]) or {}).get("rows") or {}).get(path[2]) if len(path) > 2 else None
        if row is None:
            notes.append(f"{'.'.join(path)}: not in the schema")
            continue
        if not row["read_by_code"]:
            notes.append(f"{'.'.join(path)}: the code does not read it")
        rng = row["allowed"]
        if isinstance(rng, dict) and isinstance(v, (int, float)) and not (rng.get("min", v) <= v <= rng.get("max", v)):
            notes.append(f"{'.'.join(path)}={v}: outside the approved range {rng}")
        elif not isinstance(rng, dict):
            notes.append(f"{'.'.join(path)}: no approved range yet")
    return notes


def request(req, by="meta", now=None):
    """approval.request: write one request (17-3). Returns it with its id, the base values and the checks."""
    missing = [k for k in REQUIRED if k not in req]
    if missing:
        raise ValueError(f"approval request without {missing}")
    if req["urgency"] not in ("now", "page"):
        raise ValueError("urgency is now or page")
    now = now or time.time()
    cur = active()
    test = _apply(cur, req["change"])
    errors = schema.validate(test, schema.STRATEGY)
    if errors:
        raise ValueError(f"the change breaks the strategy schema: {errors[:3]}")
    rid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    n = 1
    while (_dir() / f"{rid}.json").exists():          # two requests of one cycle share the second
        n += 1
        rid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{n}"
    doc = {**req, "id": rid, "by": by, "ts": now, "status": "open", "version_from": cur.get("version"),
           "base": {".".join(p): _get(cur, p) for p, _ in _leaves(req["change"])},
           "checks": check_change(req["change"])}
    atomic.write_json(_dir() / f"{rid}.json", doc)
    _log({"ts": now, "event": "request", "id": rid, "title": req["title"], "urgency": req["urgency"]})
    for old in status():                       # an open request on the same numbers waits no longer (user 11/6)
        if old["id"] != rid and set(old.get("base") or {}) & set(doc["base"]):
            old.update(status="superseded", decision={"by": rid, "ts": now})
            atomic.write_json(_dir() / f"{old['id']}.json", old)
            _log({"ts": now, "event": "superseded", "id": old["id"], "by": rid})
    if req["urgency"] == "now":
        from harness import alert
        alert.send(f"[승인 요청] {req['title']}\n{rid} - py -m harness.wisdom show {rid}", level="now")
    return doc


def status(rid=None):
    """approval.status: one request, or every open one (newest first)."""
    if rid:
        return atomic.read_json(_dir() / f"{rid}.json", default=None)
    docs = [atomic.read_json(f, default={}) for f in _dir().glob("*.json")]
    return sorted((d for d in docs if d.get("status") == "open"), key=lambda d: (d["urgency"] != "now", -d["ts"]))


def decide(rid, choice, note="", by="user", now=None):
    """The user's answer. approve: install the patch on the strategy in force (or start the experiment)."""
    now = now or time.time()
    doc = status(rid)
    if doc is None:
        raise ValueError(f"no request {rid}")
    if doc["status"] != "open":
        raise ValueError(f"request {rid} is {doc['status']}")
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    out = {"choice": choice, "note": note, "by": by, "ts": now}
    if choice == "approve":
        cur = active()
        moved = {k: (v, _get(cur, tuple(k.split(".")))) for k, v in doc["base"].items()
                 if _get(cur, tuple(k.split("."))) != v}
        if moved:
            raise ValueError(f"changed since the request (was, now): {moved}; ask again on the current numbers")
        ev = doc["evaluation"]
        if ev.get("method") == "ab":
            exp_id = f"x{rid}"
            experiment.register(exp_id, doc["change"], doc["expected"]["metric"], ev["success_if"],
                                ev["period_h"], "ab", why=doc["title"], now=now)
            experiment.approve(exp_id, f"{by} {stamp}: request {rid}", now=now)
            out["experiment"] = exp_id
        else:
            s = _apply(cur, doc["change"])
            s.update(version=cur["version"] + 1, epoch=f"e{cur['version'] + 1}",
                     approved=f"{by} {stamp}: request {rid} ({doc['title']})" + (f"; {note}" if note else ""))
            src = paths.root() / "logs" / "strategy" / "strategies" / f"v{s['version']}.json"
            atomic.write_json(src, s)
            strategy.install(src)
            out["installed"] = s["version"]
    elif choice not in ("reject", "revise"):
        raise ValueError("choice is approve, reject or revise")
    doc.update(status={"approve": "approved", "reject": "rejected", "revise": "revise"}[choice], decision=out)
    atomic.write_json(_dir() / f"{rid}.json", doc)
    _log({"ts": now, "event": "decision", "id": rid, **out})
    w = EventWriter("wisdom", source="outer")
    w.emit("strategy.decision", {"proposal_id": rid, "accepted": choice == "approve", "note": note, **out})
    w.close()
    return out


def render(doc):
    """One request as the user reads it (status page and the CLI)."""
    e, x = doc["evaluation"], doc["expected"]
    lines = [f"[{'즉시' if doc['urgency'] == 'now' else '페이지'}] {doc['title']}  ({doc['id']}, {doc['status']})",
             f"- 바꾸는 것: {json.dumps(doc['change'], ensure_ascii=False)}  (지금: {doc['base']})",
             f"- 왜: {doc['why']}",
             f"- 기대: {x.get('metric')} {x.get('direction')} {x.get('size')}",
             f"- 평가: {e.get('method')}, {e.get('period_h')}시간, 성공 기준 {e.get('success_if')}",
             f"- 위험: {doc['risk']} / 되돌리기: {doc['undo']}"]
    if doc.get("checks"):
        lines.append(f"- 능력 목록 검사: {'; '.join(doc['checks'])}")
    if doc.get("dispute"):
        lines.append(f"- 의견 갈림: {doc['dispute']}")
    if doc.get("audit"):
        lines.append(f"- 감사: {json.dumps(doc['audit'], ensure_ascii=False)[:400]}")
    return "\n".join(lines)


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a[:1] == ["show"]:
            print(render(status(a[1])))
        elif a[:1] == ["decide"]:
            note = a[a.index("--note") + 1] if "--note" in a else ""
            print(json.dumps(decide(a[1], a[2], note), ensure_ascii=False))
        else:
            rows = status()
            print("\n\n".join(render(d) for d in rows) or "nothing waits for a decision")
    except ValueError as e:
        sys.exit(f"not done: {e}")
