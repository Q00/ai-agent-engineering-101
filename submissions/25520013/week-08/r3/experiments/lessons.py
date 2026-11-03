"""Layer 5 lessons: append-only, every lesson carries evidence ledger seqs, n and a CI that are checked on add.

  rec = add(path, domain=..., claim=..., evidence_seqs=[...], n=..., ci=[lo, hi], known_seqs=_seqs(ledger_path),
            exp_id=None, verdict=None, change=None, condition=None, delta=None, ts=None)
  read(path, domain=None) -> [records];  for_editor(path, domain, before_ts) -> same-domain lessons written before
  _seqs(ledger_path) -> set of the seq ints in a ledger events.jsonl (missing file -> empty set)

Record (one JSON line; the shape llm/editor.py lesson_view reads):
  {ts, domain, exp_id, claim, verdict, change, condition, evidence_seq, result: {delta, ci, n}}
add raises LessonError when an evidence seq is not in known_seqs (a set, or a ledger path), when there is no
evidence, when n is not a positive int (or a dict of them) or ci is not two finite numbers lo <= hi.
"""

import json
import math
from pathlib import Path


class LessonError(ValueError):
    pass


def _seqs(ledger_path):
    p = Path(ledger_path)
    out = set()
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                s = json.loads(line).get("seq")
            except (ValueError, AttributeError):
                continue
            if isinstance(s, int) and not isinstance(s, bool):
                out.add(s)
    return out


def _pos_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def add(
    path,
    *,
    domain,
    claim,
    evidence_seqs,
    n,
    ci,
    known_seqs,
    exp_id=None,
    verdict=None,
    change=None,
    condition=None,
    delta=None,
    ts=None,
):
    if not isinstance(domain, str) or not domain:
        raise LessonError("domain must be a non-empty string")
    if not isinstance(claim, str) or not claim.strip():
        raise LessonError("claim must be a non-empty string")
    ev = list(evidence_seqs or [])
    if not ev or not all(isinstance(s, int) and not isinstance(s, bool) for s in ev):
        raise LessonError("evidence_seqs must be a non-empty list of ints")
    known = (
        _seqs(known_seqs) if isinstance(known_seqs, (str, Path)) else set(known_seqs)
    )
    miss = sorted(set(ev) - known)
    if miss:
        raise LessonError(f"evidence seqs not in the ledger: {miss[:5]}")
    if not (
        _pos_int(n)
        or (isinstance(n, dict) and n and all(_pos_int(v) for v in n.values()))
    ):
        raise LessonError("n must be a positive int or a dict of positive ints")
    if (
        not isinstance(ci, (list, tuple))
        or len(ci) != 2
        or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            for x in ci
        )
        or ci[0] > ci[1]
    ):
        raise LessonError("ci must be [lo, hi], finite, lo <= hi")
    rec = {
        "ts": ts,
        "domain": domain,
        "exp_id": exp_id,
        "claim": claim,
        "verdict": verdict,
        "change": change,
        "condition": condition,
        "evidence_seq": ev,
        "result": {"delta": delta, "ci": list(ci), "n": n},
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def read(path, domain=None):
    p = Path(path)
    if not p.exists():
        return []
    rows = [
        json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    return [r for r in rows if domain is None or r.get("domain") == domain]


def for_editor(path, domain, before_ts=None):
    return [
        r
        for r in read(path, domain)
        if before_ts is None
        or (isinstance(r.get("ts"), (int, float)) and r["ts"] < before_ts)
    ]
