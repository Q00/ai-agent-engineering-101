"""The limit keeps my current Saved, not the fixed 0.30 (W-20261109-1638-AXPro주기, review/W-20261109-1638-AXPro주기).

Saved is the sum over deals of surplus / list, divided by deals + 100 (agent.md 228). A deal that keeps r of its list
moves Saved by (r - S) / (deals + 101): it lowers Saved whenever r < S, my Saved now. market_buyer.saved_cap kept a
fixed SAVED_MIN_SHARE of 0.30 and the accept band (plan A) takes up to 105% of that limit, so deals at 0.69-0.73 of
list were bought while S was 0.3096: 361 of my 1,841 deals in the 24 h to 11/09 16:41 kept r < S (goods 216, food 145),
and leaving the 288 whose price alone was over (1 - S) of list would have put Saved at .3105 instead of .3096
(ana/saved_floor.py), where ddolcom .3097 and 25512088 .3099 sit just above (one place = .0089 of buyer ALL).
The short-warranty path (buyer_short_warranty.p_max) already holds deals to S; this does the same for the 6 h path.

On, an order's share is max(SAVED_MIN_SHARE, S + margin), at most MAX_SHARE, S the board's (board_history.jsonl,
not older than buyer_short_warranty.BOARD_MAX_AGE); with band "cap" (the default) the accept band is not used, so
nothing over the limit is taken. A board that cannot be read leaves the order as before.

Switch: .market/buyer_saved_floor.json {"enabled": true, "floor": true|false, "margin": 0.0, "band": "cap"|"keep",
"ab": {"salt": "...", "share": 0.5}, "kinds": ["food", "goods"], "start_at": <epoch>, "stop_at": <epoch>}; on only in
[start_at, stop_at), a window of at most MAX_WINDOW_HOURS: the study ends at stop_at by this code, not by the
experiments registry or a reminder (W-20261109-1638 지시 3). Missing, unreadable or anything else (no window, a time that
is not a finite number, reversed, too long) is off, and off is the old behaviour exactly. switch_on writes it.
Arm B's rows stay out of the learning after stop_at too (they were bought without the band). The arm is set once
per buyer run at its clock: a run resumed after stop_at is off, so the judgement counts an order by its first
assignment, sha1(salt|order) over every order in the window (review M-4). With "ab", only the orders whose sha1(salt|order) falls under share are arm B (the floor); arm A is the
old behaviour and is still returned so the judgement can count both. BUYER_SAVED_FLOOR=0 in the environment is off
too. "floor": false keeps SAVED_MIN_SHARE and only turns the band off (GPT Pro 1638 §2.4: the first A/B). Arm B's
trades and orders rows are left out of the price, rank and shop-profile learning both arms read. "kinds" (a list) limits it to those order kinds, so it can start on food when the food study ends and on goods
when the goods studies end; an order of another or unknown kind is off. Read at every order (a buyer run is one
order), so no restart either way. On, it moves what other buyer studies see (goods-grade's limit_a, quote fit, the
shop profile's band records, food talks without the band): start it on a kind only after that kind's studies end, and
every order it touched carries "saved_floor" in its measure record (review I3).

Each record names the study (the ab salt), VERSION, the board snapshot it read (board_ts) and the floor flag, and the
early exit with no shop in stock writes it too, so the judgement recounts both arms by sha1(salt|order) over every order
(GPT Pro 1638 P2). With the floor on (arm B, "floor" true), a receipt for another SKU than the one asked is held to
that SKU's break chance, not b = 0 (recheck; market_buyer.receipt_chance). The band-only first A/B ("floor": false)
keeps b = 0 there, so arm B differs from arm A by the band alone.
"""
import hashlib
import json
import math
import os
import time

import buyer_short_warranty

SWITCH = "buyer_saved_floor.json"
BASE_SHARE = 0.33          # market_buyer.SAVED_MIN_SHARE
MAX_SHARE = 0.40           # a board S above this is not trusted: off
MAX_MARGIN = 0.05
MAX_WINDOW_HOURS = 12      # the experiments' max (ax-pro-cycle 5: min 9 h, max 12 h)
KEYS = {"enabled", "floor", "margin", "band", "ab", "kinds", "start_at", "stop_at", "by", "since", "why", "source"}
VERSION = "sf-2w"          # sf-2: records carry study/v/board_ts/floor, the floor rechecks a shipped SKU's b; w: the window


def _number(v):
    """A finite int or float, not bool; an int out of float range (JSON can hold 10**400) is not one, never a crash."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _window(start, stop):
    return _number(start) and _number(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _good(d):
    """Whether a switch dict is a good, enabled one with a finite window (any now)."""
    if not isinstance(d, dict) or d.get("enabled") is not True or set(d) - KEYS:
        return False
    if not _window(d.get("start_at"), d.get("stop_at")):
        return False
    if "margin" in d and not (_number(d["margin"]) and 0 <= d["margin"] <= MAX_MARGIN):
        return False
    if "band" in d and d["band"] not in ("cap", "keep"):
        return False
    if "floor" in d and not isinstance(d["floor"], bool):
        return False
    if d.get("floor") is False and d.get("band", "cap") == "keep":     # neither the share nor the band: nothing
        return False
    if "ab" in d:
        ab = d["ab"]
        if not isinstance(ab, dict) or not isinstance(ab.get("salt"), str) or not ab["salt"] \
                or not _number(ab.get("share")) or not 0 < ab["share"] <= 1:
            return False
    if "kinds" in d and not (isinstance(d["kinds"], list) and d["kinds"]
                             and all(k in ("food", "goods") for k in d["kinds"])):
        return False
    return True


def _read(md):
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def config(md, env=None, now=None):
    """The switch file as a dict when it is on at now (in [start_at, stop_at)) and every setting in it is good, else
    None (review I2: a broken setting never turns the floor on)."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("BUYER_SAVED_FLOOR") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    d = _read(md)
    if not _good(d) or not _number(now) or not d["start_at"] <= now < d["stop_at"]:
        return None
    return d


def board_saved(md, now=None):
    """My Saved on the newest board snapshot, or None when it is missing, stale, over MAX_SHARE or unreadable."""
    row = board_row(md, now)
    return row[0] if row else None


def board_row(md, now=None):
    """(my Saved, the snapshot's ts) on the newest board snapshot, or None as board_saved."""
    now = now or time.time()
    if not _number(now):                  # delta review 2 M-2: never an OverflowError in now - ts
        return None
    try:
        with open(os.path.join(md, "board_history.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - buyer_short_warranty.BOARD_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or not _number(d.get("ts")) \
                or not -300 <= now - d["ts"] <= buyer_short_warranty.BOARD_MAX_AGE \
                or not isinstance(d.get("buyers"), dict):
            return None
        for name, row in d["buyers"].items():
            if str(name).lower() in buyer_short_warranty.ME and isinstance(row, list) and row \
                    and _number(row[0]) and 0 < row[0] <= MAX_SHARE:
                return float(row[0]), float(d["ts"])
        return None
    return None


def arm(salt, order_id, share):
    h = int(hashlib.sha1(f"{salt}|{order_id}".encode()).hexdigest()[:8], 16) / 0x100000000
    return "B" if h < share else "A"


def state(md, order_id, now=None, env=None, base=BASE_SHARE, kind=None):
    """None when off; else {"arm", "share", "band_off", "board_saved", "board_ts", "floor", "study", "v", "start_at",
    "stop_at"}: arm A keeps base and the band. Never raises into the buyer run (review I1): anything unexpected is off."""
    try:
        now = time.time() if now is None else now
        c = config(md, env, now)
        if c is None or ("kinds" in c and kind not in c["kinds"]):
            return None
        row = board_row(md, now)
        if row is None:
            return None
        S, ts = row
        floor = c.get("floor", True)
        meta = {"board_saved": S, "board_ts": ts, "floor": floor,
                "study": c["ab"]["salt"] if "ab" in c else None, "v": VERSION,
                "start_at": c["start_at"], "stop_at": c["stop_at"]}
        if "ab" in c:
            if not isinstance(order_id, str) or not order_id:
                return None
            if arm(c["ab"]["salt"], order_id, c["ab"]["share"]) == "A":
                return {"arm": "A", "share": base, "band_off": False, **meta}
        share = min(MAX_SHARE, max(base, S + c.get("margin", 0))) if floor else base
        return {"arm": "B", "share": share, "band_off": c.get("band", "cap") == "cap", **meta}
    except Exception:   # noqa: BLE001 - the floor is optional; the order goes on as before
        return None


def recheck(st):
    """Whether a receipt for another SKU is held to that SKU's break chance: arm B with the floor on only. The band-only
    A/B (floor false), arm A and records without the flag keep b = 0 as before."""
    return isinstance(st, dict) and st.get("arm") == "B" and st.get("floor") is True


def trade_arm(order):
    """The arm market_buyer writes into the order's trade: "A", "B" or None (not in the study)."""
    g = (order or {}).get("_saved_floor")
    return g.get("arm") if isinstance(g, dict) else None


def floored(row):
    """Whether an orders.jsonl row is an arm B talk: the shop learning both arms read leaves it out."""
    g = row.get("saved_floor") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"


def priced(trades):
    """The trades whose prices both arms may learn from: without arm B's (every trade carries its arm from the start)."""
    return [t for t in trades if t.get("saved_floor") != "B"]


def switch_on(md, *, salt, start_at, hours, share=0.5, floor=False, band="cap", kinds=None, by=None, why=None,
              now=None):
    """Write the switch for [start_at, start_at + hours) atomically (0600) and return it. ValueError, the old file left
    as it was, when a value is bad (the same check config makes; by/why not text), the file there cannot be read, or it is
    another study that is enabled and has not ended at now (or has no good window): never over someone else's open study
    (W-20261109-1638 지시 3, review M-1/M-3)."""
    now = time.time() if now is None else now
    d = {"enabled": True, "floor": floor, "band": band, "ab": {"salt": salt, "share": share}, "start_at": start_at,
         "stop_at": start_at + hours * 3600 if _number(start_at) and _number(hours) and 0 < hours <= MAX_WINDOW_HOURS
         else None,                        # a huge hours never reaches the sum: no OverflowError (delta review 2 M-1)
         **({"kinds": kinds} if kinds is not None else {}),
         **({"by": by} if by is not None else {}), **({"why": why} if why is not None else {})}
    if not _good(d) or not all(v is None or isinstance(v, str) for v in (by, why)):
        raise ValueError("buyer_saved_floor: bad switch")
    path = os.path.join(md, SWITCH)
    try:                                  # a file there that cannot be read is not shown safe to replace (review M-1)
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except FileNotFoundError:
        old = None
    except (OSError, ValueError) as e:
        raise ValueError("buyer_saved_floor: the switch there cannot be read; not overwritten") from e
    if isinstance(old, dict) and old.get("enabled") is True \
            and not (_window(old.get("start_at"), old.get("stop_at")) and _number(now) and old["stop_at"] <= now):
        raise ValueError("buyer_saved_floor: another study is on or has no window; not overwritten")
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return d
