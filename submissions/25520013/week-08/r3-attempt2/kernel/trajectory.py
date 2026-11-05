"""Append-only trajectory log (deep memory tier). One JSON line per event: model call, tool call and its
observation, decision, harness edit, and outcomes attached later. Secrets are masked before writing.
"""

import fcntl
import json
import os
import time

from kernel import common as c


class Trajectory:
    def __init__(self, path, secrets=()):
        self.path = path
        self.secrets = tuple(s for s in secrets if s)

    def append(self, agent, kind, **fields):
        row = {"ts": round(time.time(), 3), "agent": agent, "kind": kind, **fields}
        line = c.mask(json.dumps(row, ensure_ascii=False, default=str), self.secrets)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        return json.loads(line)

    def since(self, ts, max_bytes=50_000_000):
        return [
            r for r in c.tail_json(self.path, max_bytes) if (r.get("ts") or 0) >= ts
        ]
