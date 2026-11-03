"""Operating caps for the two seller LLM roles (v1.17d fixes4, CAP_PROPOSAL_v1.md a5464e4d, 성수님 18:40 via CoS).
Code only, DEFAULT DISABLED (llm_caps_config_v4.json enabled=false) and NOT wired into the deploy package or any hook.

Per-role counters in SEPARATE files (no shared reserve, proposal 3.4-2):
  parse (interpretation helper): 1 per deal, 6 / hour, 72 / day, 1,080 / round (absolute), concurrency 1,
                                 8 s wait (async, worker thread), input <= 300 chars
  editor:                        min interval 4 h, 1 / hour, 6 / day, 90 / round (absolute), 1 edit per call, 60 s
Circuit breaker shared by both roles (proposal 3.4-1): 1 quota/rate-limit error OR 3 consecutive timeouts -> LLM off
for 1 h; a second trip on the same KST day -> off until the next KST midnight. A success resets the timeout streak.
Any cap, breaker, busy slot, timeout, error, bad format -> the RULE result (parse) / skip the cycle (editor);
the trade path never waits longer than 8 s and never stops.
Unreadable state = full / breaker open (fail closed). Single host, flock (llm.filelock)."""
import asyncio
import fcntl
import json
import os
import re
import threading
import time
from pathlib import Path

from llm.client import call_with_timeout
from llm.filelock import atomic_write_json, locked

HERE = Path(__file__).resolve().parent.parent
CONFIG = HERE / "llm_caps_config_v4.json"
H, D = 3600, 86400
KST = 9 * 3600
ROUND_DAYS = 15
PARSE = {"per_deal": 1, "per_hour": 6, "per_day": 72, "per_round": 72 * ROUND_DAYS, "min_interval_s": 0,
         "timeout_s": 8, "max_input_chars": 300, "concurrency": 1}
EDITOR = {"per_deal": None, "per_hour": 1, "per_day": 6, "per_round": 6 * ROUND_DAYS, "min_interval_s": 4 * H,
          "timeout_s": 60, "max_edits": 1, "concurrency": 1}
ROLES = {"parse": PARSE, "editor": EDITOR}
BREAKER_TIMEOUT_STREAK = 3
BREAKER_OFF_S = H
QUOTA_RX = re.compile(r"(?i)(\b429\b|rate[ _-]?limit|quota|usage[ _-]?limit|too many requests|insufficient_quota|"
                      r"plan limit)")


def load_config(path=CONFIG):
    """-> dict. Missing / unreadable / enabled not exactly true -> disabled."""
    try:
        c = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:                                                    # noqa: BLE001
        return {"enabled": False}
    c["enabled"] = c.get("enabled") is True
    return c


def _kst_day(ts):
    return int((ts + KST) // D)


def _next_kst_midnight(ts):
    return (_kst_day(ts) + 1) * D - KST


class RoleCounter:
    """One role, one state file {rows: [{ts, scope}], round_total, round_id}. Counted at reservation."""

    def __init__(self, path, role, round_id="r3"):
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        self.path, self.role, self.caps, self.round_id = Path(path), role, ROLES[role], round_id

    def _load(self):
        if not self.path.exists():
            return {"rows": [], "round_total": 0, "round_id": self.round_id}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            assert isinstance(d["rows"], list) and isinstance(d["round_total"], int)
            return d
        except Exception:                                                # noqa: BLE001
            return None

    def reserve(self, now, scope=None):
        with locked(self.path):
            d = self._load()
            if d is None:
                return False, "state_unreadable"
            if d.get("round_id") != self.round_id:
                d = {"rows": [], "round_total": 0, "round_id": self.round_id} if d.get("round_total", 0) == 0 \
                    else None
                if d is None:
                    return False, "round_id_mismatch"
            c = self.caps
            rows = [r for r in d["rows"] if now - r["ts"] < D]
            if d["round_total"] >= c["per_round"]:
                return False, "per_round"
            if c["min_interval_s"] and rows and now - max(r["ts"] for r in rows) < c["min_interval_s"]:
                return False, "min_interval"
            if sum(1 for r in rows if now - r["ts"] < H) >= c["per_hour"]:
                return False, "per_hour"
            if len(rows) >= c["per_day"]:
                return False, "per_day"
            if c["per_deal"] is not None:
                if scope is None:
                    return False, "no_deal_id"
                if sum(1 for r in d["rows"] if r.get("scope") == scope) >= c["per_deal"]:
                    return False, "per_deal"
            # keep scoped rows for the per-deal cap even after a day (bounded by per_round)
            keep = rows + [r for r in d["rows"] if now - r["ts"] >= D and r.get("scope") is not None][-2000:]
            keep.append({"ts": now, "scope": scope})
            atomic_write_json(self.path, {"rows": keep, "round_total": d["round_total"] + 1,
                                          "round_id": self.round_id})
            return True, "ok"


class Breaker:
    """Shared by both roles. State {streak, trips: [ts], off_until}."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {"streak": 0, "trips": [], "off_until": 0}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return {"streak": int(d["streak"]), "trips": list(d["trips"]), "off_until": float(d["off_until"])}
        except Exception:                                                # noqa: BLE001
            return None

    def is_open(self, now):
        with locked(self.path):
            d = self._load()
        return d is None or now < d["off_until"]

    def record(self, now, status, detail=""):
        """status: ok | timeout | error. Quota-like error text trips at once."""
        with locked(self.path):
            d = self._load() or {"streak": 0, "trips": [], "off_until": now + BREAKER_OFF_S}
            trip = False
            if status == "ok":
                d["streak"] = 0
            elif status == "timeout":
                d["streak"] += 1
                trip = d["streak"] >= BREAKER_TIMEOUT_STREAK
            elif QUOTA_RX.search(detail or ""):
                trip = True
            if trip:
                today = [t for t in d["trips"] if _kst_day(t) == _kst_day(now)]
                d["off_until"] = _next_kst_midnight(now) if today else now + BREAKER_OFF_S
                d["trips"] = today + [now]
                d["streak"] = 0
            atomic_write_json(self.path, d)
            return trip, d["off_until"]


class _Slot:
    """Concurrency 1 per role: in-process lock + non-blocking flock on <state>.busy (single host)."""

    _guards = {}
    _g = threading.Lock()

    def __init__(self, path):
        self.path = str(path) + ".busy"
        with _Slot._g:
            self.tl = _Slot._guards.setdefault(self.path, threading.Lock())
        self.fd = None

    def acquire(self):
        if not self.tl.acquire(blocking=False):
            return False
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.tl.release()
            return False

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
        self.tl.release()


class OpsCaps:
    def __init__(self, state_dir, config=None, round_id="r3", clock=time.time):
        self.cfg = load_config() if config is None else dict(config, enabled=config.get("enabled") is True)
        sd = Path(state_dir)
        self.counters = {r: RoleCounter(sd / f"caps_{r}.json", r, round_id) for r in ROLES}
        self.slots = {r: _Slot(sd / f"caps_{r}.json") for r in ROLES}
        self.breaker = Breaker(sd / "caps_breaker.json")
        self.clock = clock
        self.log_path = sd / "caps_log.jsonl"

    def _log(self, row):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def call(self, role, client, prompt, max_tokens, scope=None):
        """-> (text or None, reason). None means: use the rule result / skip the cycle."""
        now = self.clock()
        if not self.cfg.get("enabled"):
            return None, "disabled"
        if self.breaker.is_open(now):
            return None, "breaker_open"
        if not self.slots[role].acquire():
            return None, "busy"
        try:
            ok, why = self.counters[role].reserve(now, scope)
            if not ok:
                self._log({"ts": now, "role": role, "status": "cap", "reason": why})
                return None, why
            box = {}

            class _W:                                   # keeps the error text for quota detection (not logged)
                def complete(self_, p, *, max_tokens, timeout_s):
                    try:
                        return client.complete(p, max_tokens=max_tokens, timeout_s=timeout_s)
                    except BaseException as e:          # noqa: BLE001
                        box["msg"] = f"{type(e).__name__}: {e}"[:300]
                        raise
            out, st = call_with_timeout(_W(), prompt, max_tokens, ROLES[role]["timeout_s"])
            status = "ok" if st == "ok" else ("timeout" if st == "timeout" else "error")
            trip, off = self.breaker.record(self.clock(), status, box.get("msg", st))
            self._log({"ts": now, "role": role, "status": st, "breaker_tripped": trip})
            return out, st
        finally:
            self.slots[role].release()

    # ---- interpretation helper: async, 8 s, rule first
    async def aoffer(self, text, rule_fn, client, prompt_fn, parse_fn, deal_id, ask):
        rule = rule_fn(text)
        if rule is not None:
            return rule, "rule"
        if not self.cfg.get("enabled") or deal_id is None or not isinstance(text, str) \
                or len(text) > PARSE["max_input_chars"] or ask is None:
            return None, "rule_none"
        try:
            out, st = await asyncio.wait_for(
                asyncio.to_thread(self.call, "parse", client, prompt_fn(text), 20, deal_id),
                PARSE["timeout_s"] + 0.5)
        except Exception as e:                                           # noqa: BLE001
            return None, f"fallback:{type(e).__name__}"
        if out is None:
            return None, f"fallback:{st}"
        v = parse_fn(out, ask=ask)
        return (v, "llm") if v is not None else (None, "fallback:format")

    # ---- editor: one call per cycle, one edit per call
    def editor_call(self, client, prompt, max_tokens):
        out, st = self.call("editor", client, prompt, max_tokens)
        return out, st
