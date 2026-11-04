# C9: pinned v120c dependency shas. verify(lock) raises DepsMismatch (the loop then latches OFF + alarm).
# deps_lock.json paths are relative to r3_rank1/ (the single MANIFEST base path).
import hashlib
import json
from pathlib import Path

from rlpo import config as C

ROOT = C.V120C.resolve().parent          # r3_rank1 (v120c read-only dependency root)
FILES = ("v120c/probe.py", "v120c/replay.py", "v120c/records.py", "v120c/buyer_state.py", "v120c/replay_metrics.py",
         "v120c/fail_rates.py", "v120c/policy.py", "v120c/policy_v120c.py", "v120c/buyer_replay.py",
         "v120c/harness/bounds.py", "v120c/harness/rollback.py", "v120c/harness/runs.py", "v120c/harness/lock.py",
         "v120c/harness/lock.json", "v120c/harness/judge.py", "v120c/experiments/prereg.py",
         "v120c/experiments/decide.py", "v120c/experiments/state/policy_base.json",
         "v120c/harness/__init__.py", "v120c/experiments/__init__.py")          # N3: __init__ pinned too


class DepsMismatch(RuntimeError):
    pass


def _h(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pin(lock, root=ROOT):
    files = {f: _h(Path(root) / f) for f in FILES}
    Path(lock).write_text(json.dumps({"root": "r3_rank1", "files": files, "v120c_path": str(C.V120C.resolve())},
                                     indent=1, sort_keys=True) + "\n")
    return files


def verify(lock, root=ROOT):
    try:
        d = json.loads(Path(lock).read_text())
        files = d["files"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise DepsMismatch(f"deps lock unreadable ({type(e).__name__})") from e
    # N3: the v120c dependency PATH is pinned too (RLPO_V120C env may only point at the pinned directory).
    # Re-pin (deps.pin) on another machine (e.g. labpc) as a reviewed step.
    pinned = d.get("v120c_path")
    if pinned is None or str(C.V120C.resolve()) != pinned:
        raise DepsMismatch(f"v120c path {C.V120C.resolve()} != pinned {pinned} (RLPO_V120C={C.V120C_FROM_ENV})")
    if not files:
        raise DepsMismatch("deps lock pins nothing")
    for rel, h in sorted(files.items()):
        try:
            got = _h(Path(root) / rel)
        except OSError as e:
            raise DepsMismatch(f"{rel} unreadable ({type(e).__name__})") from e
        if got != h:
            raise DepsMismatch(f"{rel} sha {got[:12]} != pinned {h[:12]}")
    return True
