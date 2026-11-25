"""Raise listed goods targets for a short study when fast sales leave cash idle.

Why: over the 24 h to 11/11 08:34, 230M cash stood above a 24M buffer while
listed goods rarely showed zero stock (zero-stock share 0.00-0.09). Waiting
units held their slots, although units that sold went in minutes. G021aae64
(target 11) bought 109/day, 83% sold within an hour, held a median 0.15 h,
and made 35.6k matured net/unit (94 units); Gf6b70da8 (target 5) bought 28
and made 243.9k/unit (15); G00327e80 (target 3) bought 20 and made
230.3k/unit (18), only 4% of the market's 576/day; G3e0d6c67 (target 2)
bought 36 and made 137.3k/unit (26); Gb5d0dde7 (target 6) bought 42 and made
111.6k/unit (25); G85a1a856 (target 4) bought 57 and made 77.6k/unit (35).
Eleven listed goods SKUs with matured net above 15k/unit, at least 54% sold
within an hour and at least 15 matured units, brought 33.0M of the day's
28.4M matured goods net; the rest lost money. Yet each supply window refilled
them by just 1-2 units because target minus held was small (market measure
units_in/units_out and public ledger broken rows; review report section 17).

Arm B's named SKU target becomes ceil(target x factor), capped at 20, only
inside the switch window. Arm A and unnamed SKUs retain pricing.json's target.
guard() still applies cash buffer, stock cost, break, and unit caps to every
larger line. A unit is not sold cheaper; an unsold unit remains in stock at
cost (Book counts it at cost; storage is 1% a day).

Switch: .market/restock_turnover_targets.json {"enabled": true, "study":
"<id>", "salt": "<salt>", "skus": ["G..."], "factor": 2.0, "treat": 0.5,
"start_at": <epoch>, "stop_at": <epoch>}. It is on only in [start_at,
stop_at), at most 12 hours, and RESTOCK_TURNOVER_TARGETS=0 turns it off.
Missing, unreadable, invalid, or expired switches are off. Write with
switch_on(); restock reads it each run, and target application checks time
again. A new salt or treat needs a new study id.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

SWITCH = "restock_turnover_targets.json"
FACTOR, FACTOR_LOW, FACTOR_HIGH = 2.0, 1.25, 3.0
TREAT, TREAT_LOW, TREAT_HIGH = 0.5, 0.3, 1.0
CAP = 20
MAX_WINDOW_HOURS = 12


def _num(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except (OverflowError, ValueError, TypeError):
        return False


def _valid(d, now):
    if not isinstance(d, dict):
        return None
    study, salt, skus = d.get("study"), d.get("salt"), d.get("skus")
    factor, treat = d.get("factor", FACTOR), d.get("treat", TREAT)
    start, stop = d.get("start_at"), d.get("stop_at")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) \
            or not (isinstance(skus, list) and 1 <= len(skus) <= 40
                    and all(isinstance(s, str) and s for s in skus) and len(set(skus)) == len(skus)) \
            or not (_num(factor) and FACTOR_LOW <= factor <= FACTOR_HIGH) \
            or not (_num(treat) and TREAT_LOW <= treat <= TREAT_HIGH) \
            or not (_num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600) \
            or not (_num(now) and start <= now < stop):
        return None
    return {"study": study, "salt": salt, "skus": list(skus), "factor": float(factor),
            "treat": float(treat), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None):
    """Return validated settings inside the window, else None; never raise."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_TURNOVER_TARGETS") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        return _valid(d, now) if isinstance(d, dict) and d.get("enabled") is True else None
    except Exception:  # noqa: BLE001 - an unusable switch is off
        return None


def switch_on(md, *, study, salt, skus, start_at, hours, factor=FACTOR, treat=TREAT):
    """Write a validated finite switch atomically with mode 0600."""
    stop_at = start_at + hours * 3600 if _num(start_at) and _num(hours) else None
    d = {"enabled": True, "study": study, "salt": salt, "skus": skus, "factor": factor,
         "treat": treat, "start_at": start_at, "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_turnover_targets: bad switch")
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
    """Stable SKU assignment to A or B, or None without usable inputs."""
    try:
        if not isinstance(cfg, dict) or not isinstance(sku, str):
            return None
        h = hashlib.sha256(f"turnover-targets|{cfg['salt']}|{sku}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except Exception:  # noqa: BLE001 - invalid configuration has no assignment
        return None


def targets(cfg, sku_target_units, now=None):
    """Return (new targets, raised targets); off or on trouble returns the input and {}."""
    try:
        if not isinstance(sku_target_units, dict):
            return sku_target_units, {}
        now = time.time() if now is None else now
        on = _valid(cfg.get("turnover_targets"), now) if isinstance(cfg, dict) else None
        if on is None:
            return sku_target_units, {}
        out, changed = dict(sku_target_units), {}
        for sku in on["skus"]:
            old = sku_target_units.get(sku)
            if type(old) is not int or old < 1 or arm(on, sku) != "B":
                continue
            new = max(old, min(CAP, math.ceil(old * on["factor"])))      # never under pricing.json's own target
            out[sku] = new
            if new > old:
                changed[sku] = {"target": old, "boosted": new}
        return out, changed
    except Exception:  # noqa: BLE001 - preserve the original target settings
        return sku_target_units, {}
