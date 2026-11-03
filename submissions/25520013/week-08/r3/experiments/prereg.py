"""Preregistration and loss caps.

  rec = register(path, {"exp_id", "hypothesis", "criterion", "metric", "min_n", "seed", "judge_hash", ...})
  rec = load(path, exp_id)      # the one registered record, hash-checked
  check(rec)                    # PreregError when any field differs from the registered content hash
  why = over_cap(loss, base_balance, concurrent=())   # None or the breached cap

path is an append-only JSONL file, one record per experiment; "hash" = sha256 of the record's canonical JSON
(sort_keys, compact, without "hash"). An exp_id registers once; a second row with the same exp_id (a rewritten
criterion) makes load() refuse. verify(path, exp_id, judge_hash) also requires the pinned judge hash to match.
Caps: one experiment's loss >= CAP_PER_EXP x base balance, or all running experiments' losses together
>= CAP_TOTAL x base balance -> roll back now (harness.rollback.enforce_cap).
"""

import hashlib
import json
from pathlib import Path

CAP_PER_EXP = 0.02
CAP_TOTAL = 0.05
REQUIRED = (
    "exp_id",
    "hypothesis",
    "criterion",
    "metric",
    "min_n",
    "seed",
    "judge_hash",
)


class PreregError(ValueError):
    pass


def content_hash(record):
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(
            body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _rows(path):
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()
    ]


def register(path, record):
    miss = [k for k in REQUIRED if record.get(k) in (None, "")]
    if miss:
        raise PreregError(f"missing fields {miss}")
    if (
        not isinstance(record["min_n"], int)
        or isinstance(record["min_n"], bool)
        or record["min_n"] < 1
    ):
        raise PreregError("min_n must be a positive int")
    if not isinstance(record["seed"], int) or isinstance(record["seed"], bool):
        raise PreregError("seed must be an int")
    if any(r.get("exp_id") == record["exp_id"] for r in _rows(path)):
        raise PreregError(
            f"{record['exp_id']} is already registered (no re-registration)"
        )
    rec = {k: v for k, v in record.items() if k != "hash"}
    rec["hash"] = content_hash(rec)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def check(record):
    if not isinstance(record, dict) or record.get("hash") != content_hash(record):
        raise PreregError(
            f"{(record or {}).get('exp_id')}: record differs from its registered hash"
        )
    return True


def load(path, exp_id):
    rows = [r for r in _rows(path) if r.get("exp_id") == exp_id]
    if not rows:
        raise PreregError(f"{exp_id} is not registered")
    if len(rows) > 1:
        raise PreregError(
            f"{exp_id} has {len(rows)} registrations (rewritten after the fact)"
        )
    check(rows[0])
    return rows[0]


def verify(path, exp_id, judge_hash=None):
    rec = load(path, exp_id)
    if judge_hash is not None and rec["judge_hash"] != judge_hash:
        raise PreregError(
            f"{exp_id}: judge hash {judge_hash[:12]} != registered {rec['judge_hash'][:12]}"
        )
    return rec


def over_cap(loss, base_balance, concurrent=()):
    if loss >= CAP_PER_EXP * base_balance:
        return f"loss {loss:.0f} >= {CAP_PER_EXP:.0%} of {base_balance:.0f} (per experiment)"
    total = loss + sum(concurrent)
    if total >= CAP_TOTAL * base_balance:
        return f"total loss {total:.0f} >= {CAP_TOTAL:.0%} of {base_balance:.0f} (all experiments)"
    return None
