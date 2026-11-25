"""Installing a strategy version (HARNESS.md 5): only what the user approved reaches the inner runtime.

    py -m core.strategy install card/strategy_v1.json --approved "user 2026-11-03 21:40"

checks the schema, refuses a draft (approved starting with DRAFT) unless --approved is given, writes
memory/strategy/history/v<N>.json and then memory/strategy/active.json (atomic), and records a
strategy.installed event. A new version starts a new epoch.
"""
import argparse
import copy
import sys

from core import atomic, paths, schema
from core.events import EventWriter


def install(src, approved=None):
    s = atomic.read_json(src)
    if s is None:
        raise ValueError(f"{src} does not exist")
    s = copy.deepcopy(s)
    if approved:
        s["approved"] = approved
    if str(s.get("approved", "")).upper().startswith("DRAFT"):
        raise ValueError("this strategy is a draft; install it with --approved once the user approved it")
    errors = schema.validate(s, schema.STRATEGY)
    if errors:
        raise ValueError(f"the strategy does not fit the schema: {errors}")
    active = paths.memory() / "strategy" / "active.json"
    cur = atomic.read_json(active, default=None)
    if cur and s["version"] <= cur.get("version", 0):
        raise ValueError(f"version {s['version']} is not newer than the active version {cur['version']}")
    atomic.write_json(paths.memory() / "strategy" / "history" / f"v{s['version']}.json", s)
    atomic.write_json(active, s)
    w = EventWriter("strategy-install", source="outer")
    w.context(strategy_version=s["version"], epoch=s["epoch"])
    w.emit("strategy.installed", {"version": s["version"], "epoch": s["epoch"], "approved": s["approved"],
                                  "phase": s["phase"]})
    w.close()
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("src")
    i.add_argument("--approved")
    a = ap.parse_args()
    try:
        s = install(a.src, a.approved)
    except ValueError as e:
        sys.exit(f"not installed: {e}")
    print(f"installed strategy v{s['version']} ({s['epoch']}, phase {s['phase']}), approved: {s['approved']}")
