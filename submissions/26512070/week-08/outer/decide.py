"""The user's answer to a brief (HARNESS.md 5): install the chosen option as the next strategy version, or reject.

    py -m outer.decide 2026-11-05T10 B
    py -m outer.decide 2026-11-05T10 reject --note "keep exposure one more day"

The chosen option's strategy (built and schema-checked by rec.brief) gets the user's approval, is kept in
logs/strategy/strategies/v<N>.json (committed) and installed with core.strategy.install. Every answer is a
line in logs/strategy/decisions.jsonl and a strategy.decision event.
"""
import argparse
import json
import sys
import time

from core import atomic, paths, strategy
from core.events import EventWriter
from outer import brief


def decide(pid, choice, note="", now=None, by="user", stale_ok=False):
    now = now or time.time()
    path = paths.memory() / "strategy" / "proposals" / f"{pid}.json"
    doc = atomic.read_json(path)
    if doc is None:
        raise ValueError(f"no brief {pid}")
    if doc.get("status") != "open":
        raise ValueError(f"brief {pid} is {doc.get('status')}, not waiting for a decision")
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    w = EventWriter("decide", source="outer")
    try:
        if choice == "reject":
            doc["status"], doc["decision"] = "rejected", {"choice": "reject", "note": note, "ts": now}
            w.emit("strategy.decision", {"proposal_id": pid, "accepted": False, "note": note})
        else:
            s = (doc.get("strategies") or {}).get(choice)
            if s is None:
                raise ValueError(f"no option {choice!r}; options: {[o['id'] for o in doc['options']]}")
            active = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
            if doc.get("version_from") is not None and active.get("version") != doc["version_from"] and not stale_ok:
                # 11/4 review: the option holds the whole strategy as of the brief; installing it now would undo
                # every version installed since (night mode, delegation) without a word
                raise ValueError(f"brief {pid} was written on v{doc['version_from']}, v{active.get('version')} is in "
                                 "force now; installing it would undo the versions since - wait for a new brief, or "
                                 "pass --stale-ok to install it anyway")
            if active.get("version", 0) >= s["version"]:     # another decision came first: rebase the version
                s = dict(s, version=active["version"] + 1, epoch=f"e{active['version'] + 1}")
            s = dict(s, approved=f"{by} {stamp}: brief {pid} option {choice}" + (f" ({note})" if note else ""))
            out = paths.root() / "logs" / "strategy" / "strategies" / f"v{s['version']}.json"
            atomic.write_json(out, s)
            strategy.install(out)
            doc["status"] = "accepted"
            doc["decision"] = {"choice": choice, "note": note, "ts": now, "installed": s["version"]}
            w.emit("strategy.decision", {"proposal_id": pid, "accepted": True, "option": choice, "version": s["version"]})
    finally:
        w.close()
    atomic.write_json(path, doc)
    atomic.append_line(paths.root() / "logs" / "strategy" / "decisions.jsonl",
                       json.dumps({"ts": now, "proposal_id": pid, **doc["decision"]}, ensure_ascii=False))
    pub = brief.briefs_dir() / f"{pid}.json"
    p = atomic.read_json(pub, default=None)
    if p is not None:
        p.update(status=doc["status"], decision=doc["decision"])
        atomic.write_json(pub, p)
        brief.write_markdown(p)
    brief.refresh_pending()
    return doc["decision"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("proposal_id")
    ap.add_argument("choice", help="an option id, or reject")
    ap.add_argument("--note", default="")
    ap.add_argument("--stale-ok", action="store_true", help="install a brief written on an older strategy version")
    a = ap.parse_args()
    try:
        print(json.dumps(decide(a.proposal_id, a.choice, a.note, stale_ok=a.stale_ok), ensure_ascii=False))
    except ValueError as e:
        sys.exit(f"not decided: {e}")
