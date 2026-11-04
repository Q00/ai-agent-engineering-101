"""icrl experiment lessons as evidence for the memo graph (icrl/DESIGN.md sec 9). Read only on the icrl state dir.

    path = lessons_path()                  # <ICRL_STATE or r3/icrl/state>/lessons.jsonl
    ends = ends_by_id(rows)                # experiments.jsonl "end" rows by experiment id
    f = facts(lesson, ends)                # {exp, verdict, policy, value_after, ts, text}
    v = view(memo_edges, at=None)          # {"exp": {arm: (verdict, from)}, "policy": {path: (value, from)}}

Subjects: "exp:<arm>" with predicate "verdict" (a retest arm "retest|<arm>" counts as <arm>, so a retest that flips
the verdict is an UPDATE of the same memo) and "policy:<path>" with predicate "value", the path's value in force
after the experiment (B's value on adopt, else A's; non-scalars as compact JSON). Evidence ids of lesson rows are
-(line number), 1-based, so they never collide with ledger seqs (> 0). memory.llm_step.lessons_step is the only
writer (through Graph.propose); icrl.loop.recall reads the same view of the graph snapshot.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("exp", "policy")
PREDICATE = {"exp": "verdict", "policy": "value"}
RETEST = "retest|"


def state_dir():
    return Path(os.environ.get("ICRL_STATE") or ROOT / "icrl" / "state")


def lessons_path():
    return (state_dir() / "lessons.jsonl").resolve()


def base_arm(arm):
    arm = str(arm or "")
    return arm[len(RETEST) :] if arm.startswith(RETEST) else arm


def scalar(v):
    if v is None or (isinstance(v, (int, float, str)) and not isinstance(v, bool)):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def ends_by_id(rows):
    return {r.get("id"): r for r in rows if r.get("event") == "end"}


def facts(lesson, ends):
    """The memo facts of one lessons.jsonl row; value_after is None when its experiment row is missing."""
    end = ends.get(lesson.get("exp")) or {}
    e0 = (end.get("edits") or [{}])[0]
    path = lesson.get("path") or e0.get("path")
    after = None
    if e0.get("path") == path and ("value" in e0 or "current" in e0):
        after = scalar(
            e0.get("value") if lesson.get("verdict") == "adopt" else e0.get("current")
        )
    return {
        "exp": "exp:" + base_arm(lesson.get("arm")),
        "verdict": lesson.get("verdict"),
        "policy": "policy:" + path if path else None,
        "value_after": after,
        "ts": lesson.get("ts"),
        "text": lesson.get("text"),
    }


def _version_at(vs, at):
    # same rule as memory.graph.Graph._version_at: open at `at` when from < at <= to; at None: the open version
    if not vs:
        return None
    if at is None:
        return vs[-1] if vs[-1].get("to") is None else None
    return next(
        (
            v
            for v in reversed(vs)
            if v["from"] < at and (v["to"] is None or at <= v["to"])
        ),
        None,
    )


def view(memo_edges, at=None):
    """exp / policy memos of a graph snapshot's edges["memo"] open at `at` (None: now)."""
    out = {"exp": {}, "policy": {}}
    for e in (memo_edges or {}).values():
        kind, _, name = str(e.get("a") or "").partition(":")
        if kind not in KINDS or e.get("b") != PREDICATE[kind]:
            continue
        v = _version_at(e.get("v") or [], at)
        if v is not None:
            out[kind][name] = ((v.get("attrs") or {}).get("value"), v["from"])
    return out
