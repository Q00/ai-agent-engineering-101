"""List a few more proven goods SKUs during a finite supply-window study.

Why: 226M cash stood over a 21M buffer while listed goods were at target when
supply opened. Breadth held sales back: our goods sold at 1.016x the market's
same SKU/grade median, but we had only 10.6% of units on 38 listed goods SKUs.
The public supply, settle and broken rows joined by unit (11/10 04:04 through
11/11 04:04, matured 6 h; .market/measure; review report section 18) give
546.9M/day matured net on those 38 and +153.8M/day on 111 unlisted SKUs.
Fifteen unlisted goods at 100k+ list each had 40+ matured sales/day, 10+ shops,
no shop or buyer over half of sales, mean net at least 0.06 of list and a
positive median: 57.4M/day together. G04ee8423 sold 97 across 23 shops at
202.6k net/unit; G6841e8a3 sold 131 across 15 shops at 48.1k; G7aac0d6f
sold 79 at 61.6k; Ga4e6abc9 sold 43 at 96.6k; Gea0cca52 sold 90 at 43.6k.
Our few unlisted units sold too: Gea0cca52 sold 28/28 bought in 96 h, median
hold 0.66 h and 44k gross/unit; Gbf98024f 5/5 and Gf3e749a1 4/4. Our own
evidence excludes Gfbf7f983 (negative matured net over 11 units) and
G2af689a3 (two units unsold after 20 h).

In breadth-1111's first hour across three supply windows, arm B bought 27
units of 10 SKUs for 6.85M; 14 settled at +578k gross after 0.01-0.58 h.
Refunds have not matured, so this is not a verdict. The next 22 unlisted
goods meet the same public-ledger filter over 11/10 04:04-11/11 04:04:
25+ matured sales/day at 6 h, 8+ shops, no shop or buyer above 0.6 of sales,
mean net at least 0.05 of list, positive median, and 100k+ list. Together
they show 42.9M/day market net. G53b150b5 sold 177 across 23 shops at 47.2k
net/unit; Ge4f8ef71 sold 99 at 58.2k; G037721ac sold 62 at 51.6k. A second
finite switch studies them without changing breadth-1111's SKUs or clock.

Only arm B's named SKUs are appended to the end of priority_skus with a target
of 1-3 units inside the switch window. Arm A and all others retain pricing.json.
guard() still applies cash buffer, stock cost, break and unit caps; no sale
price changes. Units bought in the window may sell afterwards at the usual
floors, but are not bought again. Switch: .market/restock_breadth.json.
RESTOCK_BREADTH=0 turns it off. A new salt or treat needs a new study id.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

SWITCHES = ("restock_breadth.json", "restock_breadth_t2.json")
SWITCH = SWITCHES[0]
MAX_WINDOW_HOURS = 12
UNITS = 2
TREAT = 0.5


def _num(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except (OverflowError, ValueError, TypeError):
        return False


def _valid(d, now):
    if not isinstance(d, dict):
        return None
    study, salt, skus = d.get("study"), d.get("salt"), d.get("skus")
    units, treat = d.get("units", UNITS), d.get("treat", TREAT)
    start, stop = d.get("start_at"), d.get("stop_at")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) \
            or not (isinstance(skus, list) and 1 <= len(skus) <= 40
                    and all(isinstance(s, str) and s for s in skus) and len(set(skus)) == len(skus)) \
            or not (type(units) is int and 1 <= units <= 3) \
            or not (_num(treat) and 0.3 <= treat <= 1.0) \
            or not (_num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600) \
            or not (_num(now) and start <= now < stop):
        return None
    return {"study": study, "salt": salt, "skus": list(skus), "units": units,
            "treat": float(treat), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None, name=SWITCH):
    """Return validated settings inside the window, else None; never raise."""
    try:
        if name not in SWITCHES:
            return None
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_BREADTH") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / name).read_text(encoding="utf-8"))
        return _valid(d, now) if isinstance(d, dict) and d.get("enabled") is True else None
    except Exception:  # noqa: BLE001 - an unusable switch is off
        return None


def configs(md=None, env=None, now=None):
    """Return the active, validated switches in their fixed order; never raise."""
    try:
        return [on for name in SWITCHES if (on := config(md, env, now, name)) is not None]
    except Exception:  # noqa: BLE001 - an unusable switch is off
        return []


def switch_on(md, *, study, salt, skus, start_at, hours, units=UNITS, treat=TREAT, name=SWITCH):
    """Write a validated finite switch atomically with mode 0600."""
    if name not in SWITCHES:
        raise ValueError("restock_breadth: bad switch name")
    stop_at = start_at + hours * 3600 if _num(start_at) and _num(hours) else None
    d = {"enabled": True, "study": study, "salt": salt, "skus": skus, "units": units,
         "treat": treat, "start_at": start_at, "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_breadth: bad switch")
    path = Path(md) / name
    tmp = path.with_name(f"{name}.tmp-{os.getpid()}")
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
    """Stable SKU assignment to A or B, or None without usable inputs."""
    try:
        if not isinstance(cfg, dict) or not isinstance(sku, str):
            return None
        h = hashlib.sha256(f"breadth|{cfg['salt']}|{sku}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except Exception:  # noqa: BLE001 - invalid configuration has no assignment
        return None


def listing(cfg, now=None):
    """Return (priority, targets, added); off or trouble preserves input objects."""
    priority = cfg.get("priority_skus") if isinstance(cfg, dict) else None
    targets = cfg.get("sku_target_units") if isinstance(cfg, dict) else None
    try:
        if not isinstance(priority, list) or not isinstance(targets, dict):
            return priority, targets, {}
        now = time.time() if now is None else now
        breadth = cfg.get("breadth")
        studies = breadth if isinstance(breadth, (list, tuple)) else (breadth,)
        skip = cfg.get("skip_skus") or ()
        listed = cfg.get("high_price_proven_skus") or ()
        out_priority, out_targets, added = list(priority), dict(targets), {}
        for study in studies:
            on = _valid(study, now)
            if on is None:
                continue
            for sku in on["skus"]:
                if (arm(on, sku) != "B" or sku in skip or sku in listed
                        or sku in out_priority or sku in out_targets):
                    continue
                out_priority.append(sku)
                out_targets[sku] = on["units"]
                added[sku] = on["units"]
        return (out_priority, out_targets, added) if added else (priority, targets, {})
    except Exception:  # noqa: BLE001 - preserve pricing settings on trouble
        return priority, targets, {}
