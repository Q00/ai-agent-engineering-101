"""Call / token budget shared by the LLM helpers (subscription limits). A call is COUNTED WHEN RESERVED (before the
request goes out), so a timeout or a garbage reply still uses budget; a refused reservation means the caller takes
the rule path (no LLM). State: one small JSON file, rewritten atomically (tmp + os.replace); rows older than 24 h are
dropped. v1.17 fix M10-1: reserve() reads, checks and writes under llm.filelock.locked (thread lock + fcntl.flock
LOCK_EX on "<file>.lock"), tmp names carry pid / thread id / uuid. Single-host file-lock premise. An unreadable state file is treated as FULL (fail closed: no LLM until it is fixed or removed by a person).

Budget(path, per_hour, per_day, tokens_per_day, min_interval_s, per_scope=None)
  reserve(now, tokens_est, scope=None) -> (True, "ok") or (False, reason)
  scope: e.g. a round id for the parse assist ("per round" cap = per_scope)."""
import json
from pathlib import Path

from llm.filelock import atomic_write_json, locked

H, D = 3600, 86400


class Budget:
    def __init__(self, path, per_hour, per_day, tokens_per_day, min_interval_s=0, per_scope=None):
        self.path = Path(path)
        self.per_hour, self.per_day = int(per_hour), int(per_day)
        self.tokens_per_day, self.min_interval_s = int(tokens_per_day), float(min_interval_s)
        self.per_scope = per_scope

    def _load(self):
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))["rows"]
            assert isinstance(rows, list)
            return rows
        except Exception:                      # noqa: BLE001
            return None

    def _save(self, rows):
        atomic_write_json(self.path, {"rows": rows})

    def usage(self, now):
        with locked(self.path):
            rows = self._load()
        if rows is None:
            return None
        day = [r for r in rows if now - r["ts"] < D]
        return {"calls_hour": sum(1 for r in day if now - r["ts"] < H), "calls_day": len(day),
                "tokens_day": sum(r["tokens"] for r in day), "last_ts": max((r["ts"] for r in day), default=None)}

    def reserve(self, now, tokens_est, scope=None):
        with locked(self.path):
            return self._reserve(now, tokens_est, scope)

    def _reserve(self, now, tokens_est, scope):
        rows = self._load()
        if rows is None:
            return False, "budget_state_unreadable"
        rows = [r for r in rows if now - r["ts"] < D]
        if rows and self.min_interval_s and now - max(r["ts"] for r in rows) < self.min_interval_s:
            return False, "min_interval"
        if sum(1 for r in rows if now - r["ts"] < H) >= self.per_hour:
            return False, "per_hour"
        if len(rows) >= self.per_day:
            return False, "per_day"
        if sum(r["tokens"] for r in rows) + tokens_est > self.tokens_per_day:
            return False, "tokens_per_day"
        if scope is not None and self.per_scope is not None:
            if sum(1 for r in rows if r.get("scope") == scope) >= self.per_scope:
                return False, "per_scope"
        rows.append({"ts": now, "tokens": int(tokens_est), "scope": scope})
        self._save(rows)
        return True, "ok"
