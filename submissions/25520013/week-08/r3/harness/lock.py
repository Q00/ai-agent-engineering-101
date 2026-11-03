"""Hash lock for the fixed judge and the range checker.

  python -m harness.lock          verify every pinned file against harness/lock.json (exit 1 on mismatch)
  python -m harness.lock pin      rewrite harness/lock.json from the current files (a reviewed, deliberate step)

lock.json: {"files": {"<path relative to the r3 root>": "<sha256 hex>"}}. verify() with no argument checks all of
them; verify(path, expected) checks one file. Any mismatch or missing file raises JudgeTampered.
score_locked(...) = verify() then harness.judge.score(...), so a score is only produced by the pinned judge.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = ROOT / "harness" / "lock.json"
PINNED = ("harness/judge.py", "harness/bounds.py", "experiments/decide.py", "taste/judge.py")


class JudgeTampered(RuntimeError):
    pass


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(path=None, expected=None, *, lock_file=None):
    """-> True. One file (path, expected) or every file in lock_file (default harness/lock.json)."""
    if path is not None:
        try:
            got = file_hash(path)
        except OSError as e:
            raise JudgeTampered(f"{path}: unreadable ({type(e).__name__})") from e
        if got != expected:
            raise JudgeTampered(
                f"{Path(path).name} sha256 {got[:12]} != pinned {str(expected)[:12]}"
            )
        return True
    lf = Path(lock_file or LOCK_FILE)
    try:
        files = json.loads(lf.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise JudgeTampered(
            f"lock file {lf.name} unreadable ({type(e).__name__})"
        ) from e
    if not files:
        raise JudgeTampered("lock file pins nothing")
    for rel, h in sorted(files.items()):
        verify(lf.parent.parent / rel, h)
    return True


def pin(lock_file=None, root=None):
    root = Path(root or ROOT)
    lf = Path(lock_file or root / "harness" / "lock.json")
    files = {rel: file_hash(root / rel) for rel in PINNED}
    lf.write_text(
        json.dumps({"files": files}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return files


def score_locked(events, shop_names=(), t_from=None, t_to=None):
    verify()
    from harness import judge

    return judge.score(events, shop_names=shop_names, t_from=t_from, t_to=t_to)


if __name__ == "__main__":
    if sys.argv[1:] == ["pin"]:
        print(json.dumps(pin(), indent=1))
    else:
        try:
            verify()
        except JudgeTampered as e:
            sys.exit(f"TAMPERED: {e}")
        print("lock OK")
