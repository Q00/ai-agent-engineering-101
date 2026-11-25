"""Default-off A/B: the exact food shelf-life question goes to the harness without the Jev read (R1, 11/09).

11/09 09:40-12:47 market-kit.log: 71 seller turns were exactly shelf_life_reply.QUESTION. Each one called Jev (its
only numbers are hours, under the round's floor, so fast_classify.seller_turn could never take them), then 70 went to
the harness, which answered buyer_offer 0 every time, and 1 hit a Jev timeout and held the ask (negotiate HOLD_TURN).
All 71 answered the buyer the same line: "Please tell me the price you offer. My price: <ask>." Jev was 18 of 77 calls
in 11:25-11:55 (review/W-20261106-2225-AX극단상향/source-1225-followup/report.md).

Treatment (arm B, or every eligible turn once adopted): this turn's Jev reader is None; the harness and every price guard
run as before. Differences from control, which the A/B measures: a Jev timeout no longer holds the ask (the harness
answers), so the talk keeps negotiate's once-a-talk hold (deal["jev_held"]) for a later turn; and a Jev read of the
hours as an offer could no longer reach the jevrule counter (0 of 71 seen). seller.py asks only on turns that would call
Jev (a real Jev reader, no shelf-life answer from shelf_life_reply).

Eligible: the message is the whole QUESTION and every number in it is under max(floor, reserve), so no number in it can
be an offer the code would take. Randomization is per task (sha256 of salt|study|version|task), sticky for the talk while
the configuration keeps the same study and version. The turn row is journaled before the skip is allowed; a failed write
keeps the control path. Configuration: MARKET_DIR/shelf_jev_skip/config.json (SHELF_JEV_SKIP_CONFIG overrides), absent,
invalid, disabled or outside [start_at, stop_at): the original path and no turn rows; a talk assigned earlier still gets
its outcome row. mode "adopted": every eligible turn skips. Config and journal must be regular files (no FIFO waits).

Switch: .market/shelf_jev_skip/config.json
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
import time

import fast_classify
from shelf_life_reply import QUESTION

MAX_TEXT = 500
MAX_CONFIG_BYTES = 16 * 1024
MAX_RECORD_BYTES = 16 * 1024
# assignments of the other seller experiments a talk can carry, kept on each row for the cross table
OTHER_TRIALS = ("sol_margin", "warranty_trial", "food_opening", "deep_lowball", "at_floor_exact", "exact_accept",
                "exact_counter", "shelf_life", "grade_preference", "reader_trial", "jev_transport", "shelf_fact")
OTHER_FIELDS = ("study", "study_id", "id", "version", "epoch", "arm", "assigned_arm", "cell", "reason")


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


def _positive_int(v):
    return type(v) is int and v > 0


def eligible(text, deal, floor):
    """(eligible, reason, required hours, numbers) for this turn's message."""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return False, "not_question", None, ()
    m = QUESTION.fullmatch(text.strip())
    if not m:
        return False, "not_question", None, ()
    if not all(_positive_int(v) for v in (floor, deal.get("ask"), deal.get("reserve"))):
        return False, "deal_unknown", m[1], ()
    numbers = tuple(sorted(fast_classify.written(text)))
    if any(n >= max(floor, deal["reserve"]) for n in numbers):
        return False, "number_at_or_above_floor", m[1], numbers
    return True, "exact_shelf_question", m[1], numbers


def read_config(data):
    try:
        cfg = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    ok = (all(isinstance(cfg.get(k), str) and cfg[k].strip() for k in ("study", "salt"))
          and _positive_int(cfg.get("version"))
          and (isinstance(cfg.get("epoch"), str) and cfg["epoch"].strip() or _number(cfg.get("epoch")))
          and type(cfg.get("enabled")) is bool
          and cfg.get("mode") in ("ab", "adopted")
          and _number(cfg.get("share")) and 0 <= cfg["share"] <= 1
          and all(_number(cfg.get(k)) and cfg[k] > 0 for k in ("start_at", "stop_at"))
          and cfg["start_at"] < cfg["stop_at"])
    return cfg if ok else None


class Trial:
    def __init__(self, root=None):
        base = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")
        self.root = base / "shelf_jev_skip"
        self._lock = threading.Lock()
        self._cache = None

    def _path(self):
        return Path(os.path.abspath(os.environ.get("SHELF_JEV_SKIP_CONFIG") or self.root / "config.json"))

    def config(self, now=None):
        """The active configuration, or None (the original path)."""
        now = time.time() if now is None else now
        try:
            path = self._path()
            st = path.stat()
            if not stat.S_ISREG(st.st_mode):
                return None
            key = (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            with self._lock:
                if self._cache is None or self._cache[0] != key:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)     # a FIFO swapped in after stat never waits
                    with os.fdopen(fd, "rb") as f:
                        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                            return None
                        data = f.read(MAX_CONFIG_BYTES + 1)
                    self._cache = (key, None if len(data) > MAX_CONFIG_BYTES else read_config(data))
                cfg = self._cache[1]
        except Exception:
            return None
        if not cfg or not cfg["enabled"] or not cfg["start_at"] <= now < cfg["stop_at"]:
            return None
        return cfg

    @staticmethod
    def _arm(cfg, task):
        if cfg["mode"] == "adopted":
            return "adopted"
        key = f"{cfg['salt']}|{cfg['study']}|{cfg['version']}|{task}".encode("utf-8")
        return "B" if int.from_bytes(hashlib.sha256(key).digest(), "big") < cfg["share"] * (1 << 256) else "A"

    def _write(self, row):
        data = (json.dumps(row, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")
        if len(data) > MAX_RECORD_BYTES:
            raise ValueError("record too large")
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("journal is not a regular file")
            if os.write(fd, data) != len(data):
                raise OSError("short write")
        finally:
            os.close(fd)

    @staticmethod
    def _others(deal):
        out = {}
        for k in OTHER_TRIALS:
            v = deal.get(k)
            if isinstance(v, dict):
                out[k] = {f: v[f] for f in OTHER_FIELDS if isinstance(v.get(f), (str, int, float, bool))}
        return out

    def skip(self, task, text, deal, floor, now=None):
        """True when this turn's Jev read is left out (B or adopted, row journaled first); never raises."""
        deal.pop("shelf_jev_turn", None)
        try:
            now = time.time() if now is None else now
            ok, reason, hours, numbers = eligible(text, deal, floor)
            if reason == "not_question":
                return False
            cfg = self.config(now)
            if cfg is None:
                return False
            a = deal.get("shelf_jev")
            if not (isinstance(a, dict) and a.get("study") == cfg["study"] and a.get("version") == cfg["version"]
                    and a.get("mode") == cfg["mode"]):
                a = dict(study=cfg["study"], version=cfg["version"], epoch=cfg["epoch"], mode=cfg["mode"],
                         arm=self._arm(cfg, task), assigned_at=now, randomization_unit="task")
            treated = ok and a["arm"] in ("B", "adopted")
            row = dict(event="turn", ts=now, task=task, study=a["study"], version=a["version"], epoch=a["epoch"],
                       mode=a["mode"], arm=a["arm"], assigned_at=a["assigned_at"], randomization_unit="task",
                       eligible=ok, reason=reason, jev_skipped=treated, required_hours=hours, numbers=list(numbers),
                       round=deal.get("rounds"), ask=deal.get("ask"), floor=floor, reserve=deal.get("reserve"),
                       least=max(floor, deal["reserve"]) if reason != "deal_unknown" else None,
                       sku=deal.get("id"), unit=deal.get("unit"), kind=deal.get("kind"),
                       input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), others=self._others(deal))
            self._write(row)
            deal["shelf_jev"] = a
            deal["shelf_jev_turn"] = dict(skipped=treated, at=now, held_before=bool(deal.get("jev_held")))
            return treated
        except Exception:
            deal["shelf_jev_turn"] = dict(skipped=False, error=True)
            return False

    def decided(self, task, deal, fast, reader):
        """After the turn's decision: whether the harness answered and how many Jev calls the turn made."""
        t = deal.pop("shelf_jev_turn", None) or {}     # one decision row per skip() row, never a stale one
        a = deal.get("shelf_jev") or {}
        if "at" not in t or not a:
            return
        try:
            self._write(dict(event="decision", ts=time.time(), task=task, study=a["study"], version=a["version"],
                             arm=a["arm"], mode=a["mode"], jev_skipped=t["skipped"], harness_used=fast is None,
                             fast_why=(fast or {}).get("why"), jev_calls=getattr(reader, "jev_calls", None),
                             reader_source=getattr(reader, "source", None), jev_held=bool(deal.get("jev_held")),
                             held_this_turn=bool(deal.get("jev_held")) and not t.get("held_before")))
        except Exception:
            pass

    def event(self, task, deal, result, price=None, now=None):
        """The talk's outcome (seller.remember: deal, unpaid, canceled, ...), for talks that got an assignment."""
        a = deal.get("shelf_jev") or {}
        if not a:
            return
        try:
            self._write(dict(event=result, ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], price=price, sku=deal.get("id"),
                             unit=deal.get("unit"), kind=deal.get("kind"), rounds=deal.get("rounds"),
                             others=self._others(deal)))
        except Exception:
            pass
