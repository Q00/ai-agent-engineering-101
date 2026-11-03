"""Policy versions on disk (Meta-Harness style: every candidate's policy, score and trace kept as files).

  runs = PolicyRuns(root, state)            # defaults: experiments/runs, experiments/state
  v = runs.register(raw_bytes, t, "baseline" | "change" | "rollback", parent=None, note="", code_hash=None)
  runs.close(v, events, t_to, shop_names)   # judge score of the version's window [t_from, t_to)

<root>/v0001/policy.json  the exact policy bytes
<root>/v0001/meta.json    {version, parent, action, t_from, note, code_hash, sha256}; written last (= committed)
<root>/v0001/score.json   {version, t_from, t_to, shop_names, judge_sha256, score}
<root>/v0001/trace.json   {seqs}: the ledger seqs the judge counted
register() takes an flock on <state>/policy_runs.lock so version numbers never collide.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ACTIONS = ("baseline", "change", "rollback")
_V = re.compile(r"^v(\d{4,})$")


class RunsError(RuntimeError):
    pass


def _write(path, raw):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def _jdump(obj):
    return (
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    ).encode("utf-8")


class PolicyRuns:
    def __init__(self, root=None, state=None):
        self.root = Path(root or HERE / "experiments" / "runs")
        self.state = Path(state or HERE / "experiments" / "state")
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self):
        with open(self.state / "policy_runs.lock", "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _dir(self, n):
        return self.root / f"v{n:04d}"

    def versions(self):
        out = []
        for p in self.root.iterdir():
            m = _V.match(p.name)
            if m and (p / "meta.json").is_file():
                out.append(int(m.group(1)))
        return sorted(out)

    def latest(self):
        v = self.versions()
        return v[-1] if v else None

    def meta(self, n):
        try:
            return json.loads((self._dir(n) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            raise RunsError(f"v{n}: no meta ({type(e).__name__})") from e

    def policy_bytes(self, n):
        try:
            raw = (self._dir(n) / "policy.json").read_bytes()
        except (OSError, TypeError) as e:
            raise RunsError(f"v{n}: no policy ({type(e).__name__})") from e
        if hashlib.sha256(raw).hexdigest() != self.meta(n)["sha256"]:
            raise RunsError(f"v{n}: policy.json differs from its registered sha256")
        return raw

    def register(self, raw, t, action, parent=None, note="", code_hash=None):
        if action not in ACTIONS:
            raise RunsError(f"action {action!r} not in {ACTIONS}")
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        with self._locked():
            if parent is not None and parent not in self.versions():
                raise RunsError(f"parent v{parent} does not exist")
            n = (self.latest() or 0) + 1
            d = self._dir(n)
            d.mkdir()
            _write(d / "policy.json", raw)
            meta = {
                "version": n,
                "parent": parent,
                "action": action,
                "t_from": t,
                "note": str(note)[:500],
                "code_hash": code_hash,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            _write(d / "meta.json", _jdump(meta))
        return n

    def close(self, n, events, t_to, shop_names=()):
        from harness import lock

        m = self.meta(n)
        s = lock.score_locked(
            events, shop_names=shop_names, t_from=m["t_from"], t_to=t_to
        )
        seqs = s.pop("seqs")
        row = {
            "version": n,
            "t_from": m["t_from"],
            "t_to": t_to,
            "shop_names": list(shop_names),
            "judge_sha256": lock.file_hash(HERE / "harness" / "judge.py"),
            "score": s,
        }
        _write(self._dir(n) / "trace.json", _jdump({"seqs": seqs}))
        _write(self._dir(n) / "score.json", _jdump(row))
        return row

    def score(self, n):
        p = self._dir(n) / "score.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def trace(self, n):
        p = self._dir(n) / "trace.json"
        return (
            json.loads(p.read_text(encoding="utf-8"))["seqs"] if p.is_file() else None
        )
