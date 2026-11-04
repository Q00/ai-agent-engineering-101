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
# v121 (성수님 결정 CoS 전달 17:40): RLPO proposer / approver and the memory op judge get their own roles (own counter
# file and concurrency slot). v121b (CoS 결정 18:02): auto-OFF is PER ROLE. quota / usage-limit / 429 and auth trip the
# GLOBAL breaker (one account: every role OFF); timeout / 5xx / other streaks and model-id errors trip only that role
# (own file state/caps_breaker_<role>.json). harness = the editor role. No count cap: every count key is None here and on the live path (role_caps default unlimited).
_LONG = {"per_deal": None, "per_hour": None, "per_day": None, "per_round": None, "min_interval_s": 0,
         "timeout_s": 60, "concurrency": 1}
RLPO_PROP = dict(_LONG)
RLPO_APPR = dict(_LONG)
MEMORY = dict(_LONG)
ROLES = {"parse": PARSE, "editor": EDITOR, "rlpo_prop": RLPO_PROP, "rlpo_appr": RLPO_APPR, "memory": MEMORY}
# r3 (성수님 14:38 via CoS, GPT 6.1 Terra): count caps are SETTINGS, default UNLIMITED on the live path.
# PARSE/EDITOR above stay as the v1 reference values (used only when OpsCaps(caps=None), i.e. legacy direct callers).
COUNT_KEYS = ("per_deal", "per_hour", "per_day", "per_round", "min_interval_s")


def role_caps(cfg):
    """llm_caps_config_v4.json {\"caps\": {role: {key: int>=0 | null}}} -> {role: caps}. Missing key = unlimited (None).
    Only COUNT_KEYS are settable (timeouts / input size / concurrency stay code constants). Bad value -> ValueError."""
    caps = (cfg or {}).get("caps", {})
    if not isinstance(caps, dict) or set(caps) - set(ROLES):
        raise ValueError("caps must be an object with keys " + "/".join(ROLES))
    out = {}
    for r in ROLES:
        d = dict(ROLES[r], **{k: None for k in COUNT_KEYS})
        o = caps.get(r, {})
        if not isinstance(o, dict):
            raise ValueError(f"caps.{r} must be an object")
        for k, v in o.items():
            if k not in COUNT_KEYS:
                raise ValueError(f"caps.{r}.{k} is not settable")
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise ValueError(f"caps.{r}.{k} must be an int >= 0 or null")
            d[k] = v
        out[r] = d
    return out
BREAKER_TIMEOUT_STREAK = 3
GLOBAL_KINDS = frozenset({"quota", "auth"})   # v121b: account-wide errors -> global breaker; the rest -> role breaker
BREAKER_OFF_S = H
QUOTA_RX = re.compile(r"(?i)(\b429\b|rate[ _-]?limit|quota|usage[ _-]?limit|too many requests|insufficient_quota|"
                      r"plan limit)")
AUTH_RX = re.compile(r"(?i)(\b401\b|\b403\b|unauthori[sz]ed|forbidden|authentication|not logged in|login required|"
                     r"invalid[ _-]?api[ _-]?key|token (?:has )?expired|re-?login)")


SERVER_RX = re.compile(r"(?i)(\b5\d\d\b|internal server error|bad gateway|service unavailable|gateway timeout|overloaded)")
MODEL_RX = re.compile(r"(?i)(model[_ -]?not[_ -]?found|unknown model|unsupported model|invalid model|model \S+ does not exist)")
OTHER_STREAK = 3                    # r3d C1: N consecutive other errors -> OFF (shared streak with timeout/5xx)
TOKEN_CHARS = 4                     # r3c CR(2): token ESTIMATE = ceil(chars/4); the CLI reports no usage


def est_tokens(text):
    return -(-len(text or "") // TOKEN_CHARS)


def error_kind(detail):
    """-> quota | auth | server | other (no text kept). r3c: server (5xx) counts like a timeout (streak 3 -> OFF)."""
    if QUOTA_RX.search(detail or ""):
        return "quota"
    if AUTH_RX.search(detail or ""):
        return "auth"
    if MODEL_RX.search(detail or ""):
        return "model"
    if SERVER_RX.search(detail or ""):
        return "server"
    return "other"


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

    def __init__(self, path, role, round_id="r3", caps=None):
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        self.path, self.role, self.round_id = Path(path), role, round_id
        self.caps = ROLES[role] if caps is None else caps

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
            if c["per_round"] is not None and d["round_total"] >= c["per_round"]:
                return False, "per_round"
            if c["min_interval_s"] and rows and now - max(r["ts"] for r in rows) < c["min_interval_s"]:
                return False, "min_interval"
            if c["per_hour"] is not None and sum(1 for r in rows if now - r["ts"] < H) >= c["per_hour"]:
                return False, "per_hour"
            if c["per_day"] is not None and len(rows) >= c["per_day"]:
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
            elif status == "timeout" or error_kind(detail) == "server":   # r3c: 5xx joins the timeout streak
                d["streak"] += 1
                trip = d["streak"] >= BREAKER_TIMEOUT_STREAK
            elif error_kind(detail) == "quota":                     # usage-limit / 429 -> auto-OFF
                trip = True
            elif error_kind(detail) == "auth":                      # r3: auth error -> auto-OFF until KST midnight
                trip = True
                d["trips"] = [t for t in d["trips"] if _kst_day(t) == _kst_day(now)] + [now]
            elif error_kind(detail) == "model":                     # r3d C2: wrong model id -> OFF at once
                trip = True
            else:                                                   # r3d C1: other errors count in the streak
                d["streak"] += 1
                trip = d["streak"] >= OTHER_STREAK
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
    def __init__(self, state_dir, config=None, round_id="r3", clock=time.time, caps=None):
        """caps=None -> v1 reference ROLES (legacy); caps=role_caps(cfg) -> settings (live path, default unlimited)."""
        self.cfg = load_config() if config is None else dict(config, enabled=config.get("enabled") is True)
        sd = Path(state_dir)
        self.role_caps = ROLES if caps is None else caps
        self.counters = {r: RoleCounter(sd / f"caps_{r}.json", r, round_id, caps=self.role_caps[r]) for r in ROLES}
        self.slots = {r: _Slot(sd / f"caps_{r}.json") for r in ROLES}
        self.breaker = Breaker(sd / "caps_breaker.json")                        # global (quota / auth)
        self.role_breakers = {r: Breaker(sd / f"caps_breaker_{r}.json") for r in ROLES}   # v121b: per role
        self.clock = clock
        self.log_path = sd / "caps_log.jsonl"
        self.last = {}                      # v121: {role, status, kind} of the last call (no text) for adapters

    def _log(self, row):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def is_open(self, role, now=None):
        # v121b: global breaker (quota / auth, unreadable = open) OR this role breaker (streaks / model, unreadable = open)
        now = self.clock() if now is None else now
        return self.breaker.is_open(now) or self.role_breakers[role].is_open(now)

    def _record(self, role, now, status, detail):
        # -> (tripped, off_until, scope). quota / auth -> global; ok / timeout / 5xx / other / model -> role
        if status == "error" and error_kind(detail) in GLOBAL_KINDS:
            return self.breaker.record(now, status, detail) + ("all",)
        return self.role_breakers[role].record(now, status, detail) + ("role",)

    def call(self, role, client, prompt, max_tokens, scope=None):
        """-> (text or None, reason). None means: use the rule result / skip the cycle."""
        now = self.clock()
        self.last = {"role": role, "status": None, "kind": None}
        if not self.cfg.get("enabled"):
            return None, "disabled"
        if role not in self.role_breakers:
            return None, "unknown_role"
        if self.is_open(role, now):
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
            t0 = time.monotonic()
            out, st = call_with_timeout(_W(), prompt, max_tokens, self.role_caps[role]["timeout_s"])
            lat = round(time.monotonic() - t0, 3)
            status = "ok" if st == "ok" else ("timeout" if st == "timeout" else "error")
            if status == "error" and "TimeoutExpired" in (box.get("msg", "") + st):   # r3d C1: subprocess 8 s timeout = timeout
                status = "timeout"
            kind = error_kind(box.get("msg", st)) if status == "error" else status
            if status == "timeout":
                box.pop("msg", None)
            trip, off, bscope = self._record(role, self.clock(), status, box.get("msg", st))
            pt, ot = est_tokens(prompt), est_tokens(out if isinstance(out, str) else "")
            price = self.cfg.get("usd_per_1k_tokens")
            cost = round((pt + ot) / 1000 * price, 6) if isinstance(price, (int, float)) and not isinstance(price, bool) and price >= 0 else None
            self._log({"ts": now, "role": role, "status": st, "kind": kind, "latency_s": lat, "breaker_tripped": trip,
                       "breaker_scope": bscope,
                       "prompt_tokens_est": pt, "out_tokens_est": ot, "est_cost_usd": cost})
            self.last = {"role": role, "status": status, "kind": kind, "breaker_tripped": trip, "breaker_scope": bscope}
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
