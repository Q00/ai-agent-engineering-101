"""The decision trace: one JSON line per decision, one file per writer (logs/decisions/<role>.jsonl).

    order -> candidates -> memory used -> choice and why -> accept terms
          -> checkout -> settle or refusal -> later breakage or refund

Each row names the memory it used (memory_refs: summary keys or event ids) and the policy
version, so REPORT.md can quote which past events changed a decision.
"""
import itertools
import os
import time

from memory.store import LOGS, append_jsonl

_seq = itertools.count()


def decision(role, kind, *, policy_ver, ref=None, inputs=None, memory_refs=None, rules=None,
             choice=None, why=None, elapsed_ms=None, **extra):
    """role: seller | buyer | review | restock | planner. Returns the row's id."""
    now = time.time()
    rid = f"d-{role}-{int(now * 1000)}-{os.getpid()}-{next(_seq)}"
    row = {"id": rid, "ts": round(now, 3), "role": role, "kind": kind, "policy_ver": policy_ver,
           "ref": ref or {}, "inputs": inputs or {}, "memory_refs": memory_refs or [],
           "rules": rules or [], "choice": choice, "why": why}
    if elapsed_ms is not None:
        row["elapsed_ms"] = elapsed_ms
    row.update(extra)
    try:
        append_jsonl(LOGS / "decisions" / f"{role}.jsonl", row)
    except OSError:
        pass
    return rid
