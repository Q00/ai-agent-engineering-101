"""Listed goods: a SKU's unit target counts the units that still sell at the market's price, not the ones that waited
(W-20261110-1346-AXFable검토; 대표님 11/10 13:46 "fable 5.2로도 검토해보고 코드 수정해서 배포해봐", 11/06 19:03
"복리를 진짜 제대로 활용해야해").

Why the cash did not go back into stock (review/W-20261110-1346-AXFable검토/report.md, kit files and ledger to 11/10
14:2x): 190.9M of cash against a 20.9M buffer, and no line was ever cut for cash. A unit's cost is drawn on arrival
(/rules: A 55-75% of list) and its reserve is its own break-even, so a dear draw (cost .70-.75, floor .74-.78) sits over
what buyers pay (.66-.71) and waits, while a cheap draw sells within the hour: of the goods of 100k+ supplied 11/09
18:00 - 11/10 08:00, grade A units costing under .65 of list sold 78-83% within 1 h and 96-97% within 6 h, those at
.70-.75 23% and 49% (28% still held). restock.fill_listed and guard count every unit held against sku_target_units, so
the waiting units fill the SKU's slots and nothing is asked (no guard line is printed when held >= target: the
14:0x count of guard lines could not see it). At 14:2x 97 of the 147 goods units held had waited over 3 h with a floor
over the SKU x grade median, in 173 slots (G8ec51935 10 of 12, G1515d951 11 of 12, G24f5f979 6 of 8, Gb5d0dde7 5 of 6).
The goods bought fell with it and the sales with them, since the shop sells what it buys: 1,970 units of 100k+ on 11/07,
1,625 a day 11/08 - 11/09 12:00, 1,010 a day 11/09 18:00 - 11/10 08:00, the cheap draws selling as fast as before.

Arm B: for a listed goods SKU with a target T, a unit held under `hours` (2) counts; of the older ones the first
floor(aged_units x T) (1.0) do not, the rest do; and a line may take at most `step` (3) units more than the old count
allowed, so a SKU fills again over a few supply windows and never holds more than T + floor(aged_units x T) units.
A waiting unit stays in stock at its cost (Book counts it at cost, /rules; storage 1% of cost a day) and sells when a
buyer pays its floor; nothing is sold cheaper for this. Not while the SKU is in a surge over MAX_SURGE (the units
would be dear ones), not food, not a SKU without a target or off the proven / priority lists. Only the target reads
this count: max_units_per_sku and the high-price and surge caps still count every unit held (so a target of 12 holds
at most 20), and the cash buffer, stock cost and break guards apply to the lines as before. A SKU that already holds
its target and is asked again only for this goes after every other line of the run, listed or not, the agent's and
the small food top-ups too (restock.fresh_extra in plan, urgent_listed and main), so today's lines keep their places
in a window's ~14-18 supply calls; a SKU under its target keeps its place and asks a larger line (independent reviews
8023a19 I1-I3, 798955a I4). Arm A counts as today.
Arms by SKU, sha256("fresh-slots|salt|sku"): the first four bytes under treat (0.7 by default, 0.3-1.0) is B, an axis
of its own (goods-margin hashes SKU x grade under its own salt).

Switch: .market/restock_fresh_slots.json {"enabled": true, "study": "<id>", "salt": "<salt>", "hours": 2,
"aged_units": 1.0, "step": 3, "treat": 0.7, "start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at),
a window of at most MAX_WINDOW_HOURS. Missing, unreadable, a bad value or time, or anything else is off, and off is
today's count exactly. RESTOCK_FRESH_SLOTS=0 in the environment is off too. Write it with switch_on(). Read at every
restock run (a new process each time): no restart either way, and the treatment stops by itself at stop_at. Never
raises. A new salt or treat needs a new study id.
"""
import collections
import hashlib
import json
import math
import os
import time
from pathlib import Path

SWITCH = "restock_fresh_slots.json"
HOURS, HOURS_LOW, HOURS_HIGH = 2.0, 0.5, 24.0
AGED, AGED_LOW, AGED_HIGH = 1.0, 0.0, 3.0
STEP, STEP_LOW, STEP_HIGH = 3, 1, 20
TREAT, TREAT_LOW, TREAT_HIGH = 0.7, 0.3, 1.0
MAX_SURGE = 1.05            # inventory.json surge factor of the SKU's next unit: over it the old count stands
MAX_WINDOW_HOURS = 12       # the experiment register's longest window (ax-experiments --max-hours 12)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _valid(d, now):
    """The switch's settings when they are all usable and now is inside its window, else None."""
    if not isinstance(d, dict):
        return None
    study, salt = d.get("study"), d.get("salt")
    hours, aged, step, treat = d.get("hours", HOURS), d.get("aged_units", AGED), d.get("step", STEP), d.get("treat", TREAT)
    start, stop = d.get("start_at"), d.get("stop_at")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) \
            or not (_num(hours) and HOURS_LOW <= hours <= HOURS_HIGH) \
            or not (_num(aged) and AGED_LOW <= aged <= AGED_HIGH) \
            or not (type(step) is int and STEP_LOW <= step <= STEP_HIGH) \
            or not (_num(treat) and TREAT_LOW <= treat <= TREAT_HIGH) \
            or not _window(start, stop) or not _num(now) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "hours": float(hours), "aged_units": float(aged), "step": step,
            "treat": float(treat), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None):
    """{"study", "salt", "hours", "aged_units", "step", "treat", "start_at", "stop_at"} with the switch on at now, else None."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_FRESH_SLOTS") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return None
        return _valid(d, now)
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None


def switch_on(md, *, study, salt, start_at, hours, fresh_hours=HOURS, aged_units=AGED, step=STEP, treat=TREAT):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "hours": fresh_hours, "aged_units": aged_units, "step": step,
         "treat": treat, "start_at": start_at, "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_fresh_slots: bad switch")
    path = Path(md) / SWITCH
    tmp = path.with_name(f"{SWITCH}.tmp-{os.getpid()}")
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
    return d


def arm(cfg, sku):
    """"A" or "B" for a SKU under the study's salt and treat share; None without a study."""
    try:
        if not isinstance(cfg, dict) or not isinstance(sku, str):
            return None
        h = hashlib.sha256(f"fresh-slots|{cfg['salt']}|{sku}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None


def _listed(cfg):
    out = set()
    for key in ("high_price_proven_skus", "priority_skus"):
        v = cfg.get(key)
        out |= {s for s in (v if isinstance(v, (list, tuple)) else ()) if isinstance(s, str)}
    return out


def counted(units, cfg, catalog, factors=None, now=None):
    """(Counter: the units of each SKU as restock's unit caps count them, {sku: {"held", "fresh", "aged", "counted"}}
    for the SKUs counted lower than held). units: the live units held (dicts with "sku" and "supplied_at"); cfg: restock's
    settings with "fresh_slots" as config() read it (absent, None or past its window: every unit counts). A unit with
    no usable arrival time counts as fresh. On any trouble inside, every unit counts (a unit whose sku cannot be
    hashed fails here as it did in restock's own count)."""
    plain = collections.Counter(u.get("sku") for u in units or () if isinstance(u, dict))
    try:
        # cfg["fresh_now"] (restock.main sets it once): one clock for a whole run, so plan(), guard() and the order
        # all see the same units as waiting and the same side of stop_at (independent review ecef27b M3: the agent's
        # turn can take tens of seconds between them)
        at = (cfg or {}).get("fresh_now")
        now = at if _num(at) else time.time() if now is None else now
        fs = _valid((cfg or {}).get("fresh_slots"), now)
        targets = (cfg or {}).get("sku_target_units")
        if fs is None or not isinstance(targets, dict):
            return plain, {}
        listed, out, changed = _listed(cfg), collections.Counter(plain), {}
        fresh = collections.Counter()
        for u in units or ():
            if isinstance(u, dict):
                at = u.get("supplied_at")
                if not _num(at) or now - at < fs["hours"] * 3600:
                    fresh[u.get("sku")] += 1
        for sku, held in plain.items():
            t = targets.get(sku) if isinstance(sku, str) else None
            f = (factors or {}).get(sku, 1.0)
            if type(t) is not int or t < 1 or sku not in listed or (catalog.get(sku) or {}).get("kind") != "goods" \
                    or not _num(f) or f > MAX_SURGE or arm(fs, sku) != "B":
                continue
            aged = held - fresh[sku]
            n = max(fresh[sku] + max(0, aged - math.floor(fs["aged_units"] * t)), min(held, t) - fs["step"])
            n = min(max(n, 0), held)
            if n < held:
                out[sku] = n
                changed[sku] = {"held": held, "fresh": fresh[sku], "aged": aged, "counted": n}
        return out, changed
    except Exception:   # noqa: BLE001 - a count that cannot be made is the old count
        return plain, {}
