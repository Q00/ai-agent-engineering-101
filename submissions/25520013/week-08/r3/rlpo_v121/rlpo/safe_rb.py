# M2r safe writers around harness.rollback (C1, C2). harness.rollback itself is unchanged (v120c read-only).
#  check_live(runs, live)            -> registered version whose bytes are live, else ExternalEdit (no write)
#  apply(runs, proposal, live, t)    -> version; harness.rollback.apply_proposal
#  rollback(runs, target, live, t)   -> version; harness.rollback.rollback
# On a failed write (e.g. ENOSPC) harness.rollback has already registered a version whose bytes never reached the
# live file (ghost). reconcile() registers the live bytes again (action rollback, parent = the version that is
# really live) so the latest registered version equals the live bytes and later writes are not refused forever.
# The caller (loop) then latches RLPO OFF and raises an alarm: the bad policy may still be live -> human.
import hashlib
from pathlib import Path

from harness import rollback as RB
from harness.runs import RunsError


class ExternalEdit(RuntimeError):
    pass


class WriteFailed(RuntimeError):
    def __init__(self, msg, info):
        super().__init__(msg)
        self.info = info


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_live(runs, live):
    raw = Path(live).read_bytes()
    n = runs.latest()
    if n is not None and runs.policy_bytes(n) == raw:
        return n
    raise ExternalEdit(f"live sha {sha(raw)[:12]} != registered v{n} sha "
                       f"{sha(runs.policy_bytes(n))[:12] if n is not None else None} (edited outside RLPO)")


def reconcile(runs, live, t):
    try:
        raw = Path(live).read_bytes()
    except OSError as e:
        return {"reconciled": False, "reason": f"live unreadable {type(e).__name__}"}
    n = runs.latest()
    if n is not None and runs.policy_bytes(n) == raw:
        return {"reconciled": False, "reason": "live already equals latest", "live_version": n}
    for k in reversed(runs.versions()):
        try:
            if runs.policy_bytes(k) == raw:
                v = runs.register(raw, t, "rollback", parent=k,
                                  note=f"rlpo reconcile: write failed, ghost v{n} never reached live; live = v{k}")
                return {"reconciled": True, "ghost": n, "live_version": k, "new_version": v}
        except RunsError:
            continue
    return {"reconciled": False, "reason": "live bytes match no registered version (external edit)", "ghost": n}


def _guarded(fn, runs, live, t, *a, **kw):
    check_live(runs, live)
    try:
        return fn(runs, *a, live, t, **kw)
    except Exception as e:  # noqa: BLE001 - any failure of the write path
        info = reconcile(runs, live, t)
        raise WriteFailed(f"{fn.__name__} failed: {type(e).__name__}: {e}"[:300], info) from e


def _live_sha(live):
    try:
        return sha(Path(live).read_bytes())
    except OSError:
        return None


def _audit(runs, live, action, t, before, ver=None, err=None):
    # v121: policy.json writer audit (writer = rlpo). One JSONL row per apply / rollback attempt; shas only.
    try:
        import json
        row = {"t": t, "writer": "rlpo", "action": action, "live": str(live), "sha_before": before,
               "sha_after": _live_sha(live), "version": ver, "error": err}
        with open(Path(runs.state) / "policy_audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 - the audit never blocks or changes the write path
        pass


def apply(runs, proposal, live, t, note=""):
    b = _live_sha(live)
    try:
        v, _ = _guarded(RB.apply_proposal, runs, live, t, proposal, note=note)
    except Exception as e:
        _audit(runs, live, "apply", t, b, err=type(e).__name__)
        raise
    _audit(runs, live, "apply", t, b, ver=v)
    return v


def rollback(runs, target, live, t, note=""):
    b = _live_sha(live)
    try:
        v, _ = _guarded(RB.rollback, runs, live, t, target, note=note)
    except Exception as e:
        _audit(runs, live, "rollback", t, b, err=type(e).__name__)
        raise
    _audit(runs, live, "rollback", t, b, ver=v)
    return v
