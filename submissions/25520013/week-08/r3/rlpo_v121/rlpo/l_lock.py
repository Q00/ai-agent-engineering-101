# B3 (CR REVIEW_M2r2_L 485bb16c): sha lock of the files that switch or shape L mode (config.py, approver.py, guard.py).
# The pinned values live in C.L_LOCK (l_lock.json). Re-pinning = part of the L CR gate (cr_L_pass). Checked by
# approver.run / approver._apply and by Loop.__init__ when L mode is on. Integrity check only (box agents can edit).
import hashlib
import json
from pathlib import Path

from rlpo import config as C


def body(root=None):
    r = Path(C.HERE if root is None else root)
    return {f: hashlib.sha256((r / f).read_bytes()).hexdigest() for f in C.L_LOCK_FILES}


def violations(path=None, root=None):
    p = Path(C.L_LOCK if path is None else path)
    try:
        pinned = json.loads(p.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"B3 lock unreadable ({type(e).__name__}): {p.name}"]
    cur = body(root)
    return [f"B3 lock: {f} sha differs from {p.name}" for f in C.L_LOCK_FILES if pinned.get(f) != cur[f]]


def pin(path=None, root=None, note=""):
    p = Path(C.L_LOCK if path is None else path)
    p.write_text(json.dumps({"files": body(root), "note": note}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
