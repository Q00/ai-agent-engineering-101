"""Write a policy change or a rollback to a live policy file, registering a version first.

  v, raw = apply_proposal(runs, proposal, live_path, t_now, note="")   # bounds.apply on the live version
  v, raw = rollback(runs, target, live_path, t_now, note="")            # restore version `target`
  out = enforce_cap(runs, version, live_path, loss, base_balance, t_now, concurrent=())

Both writers first run lock.verify() and _check_live(): the live file's bytes must equal the latest registered
version (an edit made outside the harness is refused with RunsError). The write is tmp + os.replace in the same
directory. enforce_cap rolls `version` back to its parent as soon as experiments.prereg.over_cap reports a breach.
"""

import json
import os
from pathlib import Path

from experiments import prereg

from harness import bounds, lock
from harness.runs import RunsError

__all__ = ["RunsError", "apply_proposal", "rollback", "enforce_cap"]


def _atomic(path, raw):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _check_live(runs, live_path, allow_unregistered=False):
    """-> the registered version whose bytes are live. allow_unregistered registers unknown live bytes as a new
    baseline instead of refusing."""
    try:
        raw = Path(live_path).read_bytes()
    except OSError as e:
        raise RunsError(f"live policy unreadable ({type(e).__name__})") from e
    n = runs.latest()
    if n is not None and runs.policy_bytes(n) == raw:
        return n
    if allow_unregistered:
        return runs.register(
            raw, None, "baseline", note="live bytes registered by _check_live"
        )
    raise RunsError(
        "live policy differs from the latest registered version (changed outside the harness)"
        if n is not None
        else "no registered policy version"
    )


def apply_proposal(runs, proposal, live_path, t_now, note=""):
    lock.verify()
    n = _check_live(runs, live_path, False)
    base = json.loads(runs.policy_bytes(n))
    new = bounds.dumps(bounds.apply(base, proposal))
    v = runs.register(
        new,
        t_now,
        "change",
        parent=n,
        note=note or str(proposal.get("rationale") or "")[:300],
    )
    _atomic(live_path, new)
    return v, new


def rollback(runs, target, live_path, t_now, note=""):
    lock.verify()
    n = _check_live(runs, live_path, False)
    if target not in runs.versions():
        raise RunsError(f"v{target} does not exist")
    raw = runs.policy_bytes(target)
    v = runs.register(
        raw,
        t_now,
        "rollback",
        parent=target,
        note=note or f"rollback from v{n} to v{target}",
    )
    _atomic(live_path, raw)
    return v, raw


def enforce_cap(runs, version, live_path, loss, base_balance, t_now, concurrent=()):
    """-> {status: ok | rolled_back, reason, rollback_version}. concurrent = losses of the other running
    experiments (for the total cap)."""
    why = prereg.over_cap(loss, base_balance, concurrent)
    if why is None:
        return {"status": "ok", "reason": None, "rollback_version": None}
    parent = runs.meta(version)["parent"]
    if parent is None:
        raise RunsError(f"v{version} has no parent to roll back to")
    rv, _ = rollback(runs, parent, live_path, t_now, note=f"loss cap: {why}")
    return {"status": "rolled_back", "reason": why, "rollback_version": rv}
