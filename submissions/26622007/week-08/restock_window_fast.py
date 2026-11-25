"""A supply window's second restock run hands the kit the lines the first run already planned, without building the
demand model again: the ledger is still synced and its tail read for the cash buffer, the 296 MB rows file is not
loaded (W-20261110-1346-AXFable검토, 메인 지시 2, 11/10 16:2x: "공급 창당 호출을 goods에 더 쓰기").

/rules (Round 3 rulebook, Supply): "Restock windows: 3 minutes in every 20-minute slot, starting 0 to 5 minutes in.
Up to 20 units per call, one call per 10 seconds." So a window has room for about 18 calls. The kit
(scripts/market_kit.py, read-only) decides how they are spent: sync_loop looks every SYNC_EVERY (10 s) and starts the
restock command at most once per RESTOCK_EVERY (60 s) and never while the last run's supply thread is alive (:913-918);
restock() waits for the command to exit, reads its last JSON line and sends the first 10 lines one after another (:955),
sleeping the market's "wait" after each 429 (:960-961), about 11.6 s a line. Nothing here changes that pace or sends a
call the rules do not allow; only how soon the second run of a window answers.

What the kit log shows (11/10 14:44-16:24, 6 windows): the first run printed 9 lines, never 10 (the held next_window
SKU G1465c678 was planned in the first ten and dropped after the cut, restock.main "if hold and not trial"); they went
out in 93-111 s; the second run began 10-16 s after the last of them (the kit's 10 s look), took 12-19 s to print
(8-12 s to the demand model: the ledger sync and the 296 MB rows file read beside the buyer; 4-7 s more), and its
first line went out 8 s after that (measure.tick's 45 MB seller memory and the rows file once more, then the exit of a
process holding 1.37M rows). 36-40 s of an open window without a call, 12-15 lines a window (13.4 on 11/10 PM); it was
16.4 on 11/07-11/08 when the rows file was half the size. The goods of 100k+ in a call fell 2.44 -> 1.35-2.03 units
over the same days (restock_fresh_slots). Net per call over the calls of 11/09 00:00-11/10 04:00 (unit cost and price
from .market/measure, refunds from the ledger): goods of 1M+ 49.9k, 100k-1M 19.2k, food 15.8k (31% of the calls, 19% of
the net), so a call more is worth 15-50k and the food lines are not what to cut: an empty food SKU is 31 of the last
32 "not in stock" refusals.

Arm B, by window (sha256("window-fast|salt|opens_at"), the first four bytes under treat):
- the run that sends a window's first lines plans without the held next_window SKUs, so they take no place among the
  ten, and leaves the plan's next lines (CARRY_LINES, the same plan() and guard, the lines sent and the held SKUs left
  out) in .market/restock_carry.json when the agent's queue is empty;
- the next run, while that window is open, takes them once (restock.window_fast): the ledger sync as always, then the
  cash buffer from the ledger's tail exactly as main() computes it, the lines sent in the last PENDING_SECONDS counted
  in full as still on their way (never fewer than pending() counts: every unit and cash cap is at least as tight),
  restock.guard over today's inventory.json, late_lines, hard_limits.final_buy, note_sent. No demand model, loss
  watch, price log, agent, forum or tool run: the run after it does them, as every run does now. Anything missing,
  stale, unreadable, another window's, a next_window plan that is not done, an opening phase, or no line left: the
  usual run, as if this module were not there.
Arm A and off: today's run exactly.

Switch: .market/restock_window_fast.json {"enabled": true, "study": "<id>", "salt": "<salt>", "treat": 0.7,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS.
Missing, unreadable, a bad value or time, or anything else is off. RESTOCK_WINDOW_FAST=0 in the environment is off too.
Write it with switch_on(). Read at every restock run: no restart either way, and it stops by itself at stop_at.
Never raises.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

SWITCH = "restock_window_fast.json"
CARRY = "restock_carry.json"
TREAT, TREAT_LOW, TREAT_HIGH = 0.7, 0.3, 1.0
CARRY_LINES = 10            # the kit sends ten lines a run
CARRY_SECONDS = 240         # a carry older than this is not used (restock.PENDING_SECONDS: the lines sent with it)
MAX_LOT = 20
MAX_WINDOW_HOURS = 12       # the experiment register's longest window (ax-experiments --max-hours 12)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _valid(d, now):
    if not isinstance(d, dict):
        return None
    study, salt, treat = d.get("study"), d.get("salt"), d.get("treat", TREAT)
    start, stop = d.get("start_at"), d.get("stop_at")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) \
            or not (_num(treat) and TREAT_LOW <= treat <= TREAT_HIGH) \
            or not _window(start, stop) or not _num(now) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "treat": float(treat), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None):
    """{"study", "salt", "treat", "start_at", "stop_at"} with the switch on at now, else None."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_WINDOW_FAST") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return None
        return _valid(d, now)
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None


def _write(path, d):
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def switch_on(md, *, study, salt, start_at, hours, treat=TREAT):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "treat": treat, "start_at": start_at, "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_window_fast: bad switch")
    _write(Path(md) / SWITCH, d)
    return d


def arm(cfg, opens_at):
    """"A" or "B" for the supply window that opens at opens_at; None without a study or a window."""
    try:
        if not isinstance(cfg, dict) or not _num(opens_at):
            return None
        h = hashlib.sha256(f"window-fast|{cfg['salt']}|{int(opens_at)}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
        return None


def _lines(v):
    """The carried lines when every one is a {"sku": str, "qty": 1..MAX_LOT} and there are 1..CARRY_LINES, else None."""
    if not isinstance(v, list) or not 1 <= len(v) <= CARRY_LINES:
        return None
    out = []
    for b in v:
        if not isinstance(b, dict) or not isinstance(b.get("sku"), str) or not b["sku"] \
                or type(b.get("qty")) is not int or not 1 <= b["qty"] <= MAX_LOT:
            return None
        out.append({"sku": b["sku"], "qty": b["qty"]})
    return out


def save_carry(md, inv, lines, now=None):
    """Leave lines for the next run of the window inventory.json names (open, or about to open). True when written;
    with no usable line or window the old file is removed, so a carry never outlives the run that could not renew it."""
    try:
        now = time.time() if now is None else now
        path = Path(md) / CARRY
        r = inv.get("restock") if isinstance(inv, dict) else None
        keep = _lines(list(lines)[:CARRY_LINES]) if isinstance(lines, (list, tuple)) and lines else None
        if keep is None or not isinstance(r, dict) or not _num(r.get("opens_at")) or not _num(now):
            path.unlink(missing_ok=True)
            return False
        _write(path, {"at": now, "opens_at": r["opens_at"], "lines": keep})
        return True
    except (OSError, ValueError, TypeError):
        return False


def take_carry(md, inv, now=None, mark=True):
    """The carried lines, once: only while inventory.json says the window they were planned for is open and not past
    its close, and the carry is at most CARRY_SECONDS old. The file is marked used before the lines are returned (a
    mark that cannot be written returns None), so no line is ever carried twice. (lines, at) or None. mark False only
    looks: the same answer, the file untouched."""
    try:
        now = time.time() if now is None else now
        r = inv.get("restock") if isinstance(inv, dict) else None
        if not isinstance(r, dict) or r.get("open") is not True or not _num(r.get("opens_at")) \
                or not _num(r.get("closes_at")) or not _num(now) or r["closes_at"] <= now:
            return None
        path = Path(md) / CARRY
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "used" in d or not _num(d.get("at")) or not _num(d.get("opens_at")) \
                or d["opens_at"] != r["opens_at"] or not 0 <= now - d["at"] <= CARRY_SECONDS:
            return None
        lines = _lines(d.get("lines"))
        if lines is None:
            return None
        if mark:
            _write(path, {**d, "used": now})
        return lines, d["at"]
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None
