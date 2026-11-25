"""List unlisted food SKUs that sell out between supply windows, during a finite study.

Why: over 11/10 14:19-11/11 14:19 (our units_in/units_out and stock counts, .market/measure; review
W-20261111-1415-AXPro주기 verdict-kit/cand-24h.json) six unlisted food SKUs with 168 h shelf life sat at zero stock
62-92% of the time and sold within 0.25-0.8 h when held: G7e48d0d9 bought 202, sold 201, 2.5k net/unit,
zero 62%; Gcbadc27c 88/87, 2.0k, zero 82%; Gc23914fc 18/18, 2.2k, 90%; G2968d8bd 22/21, 1.9k, 92%;
G69ee6a63 18/18, 1.6k, 92%; Geb6684be 32/32, 1.5k, 86% (net is sale price less unit cost less the cost
of the one spoiled unit each had). They are in the demand model, so wanted() asks for them, but an
unlisted SKU's line sorts after every listed SKU's (plan, urgent_listed) and the window's 14-15 supply
calls close before it (review W-20261110-1346-AXFable검토 section 20-3). Listed, an empty one goes in
urgent_listed's first group with the listed food that has none held, as G200e39a3 and Gb2392fa9 do.

Only arm B's named SKUs, food in the catalog with at least MIN_SHELF_HOURS of shelf life, are appended to
the end of priority_skus with a target of `units` (1-20) inside the switch window. Arm A and all others
keep pricing.json. guard() still applies food_max_qty, food_max_stock_cost, the cash buffer and the line
cap; prices do not change. The default target is 20 (the demand model asks up to 20 for G7e48d0d9, 19 for
Gcbadc27c), so listing does not hold an SKU below what wanted() would buy. Listed, a SKU leaves the
unlisted-only surge halving and exclusion rules in restock.py like any listed SKU. Units bought in the window may sell afterwards as usual but are not bought
again. Switch: .market/restock_food_stockout.json. RESTOCK_FOOD_STOCKOUT=0 turns it off. A new salt or
treat needs a new study id.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

SWITCH = "restock_food_stockout.json"
MAX_WINDOW_HOURS = 12
UNITS, UNITS_LOW, UNITS_HIGH = 20, 1, 20
TREAT, TREAT_LOW, TREAT_HIGH = 0.5, 0.3, 1.0
MIN_SHELF_HOURS = 72        # the 48 h food (G56827374, G9f10544a) waits: a 10-unit line could spoil


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
            or not (type(units) is int and UNITS_LOW <= units <= UNITS_HIGH) \
            or not (_num(treat) and TREAT_LOW <= treat <= TREAT_HIGH) \
            or not (_num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600) \
            or not (_num(now) and start <= now < stop):
        return None
    return {"study": study, "salt": salt, "skus": list(skus), "units": units,
            "treat": float(treat), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None):
    """Return validated settings inside the window, else None; never raise."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_FOOD_STOCKOUT") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        return _valid(d, now) if isinstance(d, dict) and d.get("enabled") is True else None
    except Exception:  # noqa: BLE001 - an unusable switch is off
        return None


def switch_on(md, *, study, salt, skus, start_at, hours, units=UNITS, treat=TREAT):
    """Write a validated finite switch atomically with mode 0600."""
    stop_at = start_at + hours * 3600 if _num(start_at) and _num(hours) else None
    d = {"enabled": True, "study": study, "salt": salt, "skus": skus, "units": units,
         "treat": treat, "start_at": start_at, "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_food_stockout: bad switch")
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
        h = hashlib.sha256(f"food-stockout|{cfg['salt']}|{sku}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except Exception:  # noqa: BLE001 - invalid configuration has no assignment
        return None


def _food(x):
    shelf = x.get("shelf_hours") if isinstance(x, dict) else None
    return isinstance(x, dict) and x.get("kind") == "food" and _num(shelf) and shelf >= MIN_SHELF_HOURS


def listing(cfg, catalog, now=None):
    """Return (priority, targets, added); off or trouble preserves input objects."""
    priority = cfg.get("priority_skus") if isinstance(cfg, dict) else None
    targets = cfg.get("sku_target_units") if isinstance(cfg, dict) else None
    try:
        if not isinstance(priority, list) or not isinstance(targets, dict) or not isinstance(catalog, dict):
            return priority, targets, {}
        now = time.time() if now is None else now
        on = _valid(cfg.get("food_stockout"), now)
        if on is None:
            return priority, targets, {}
        skip = cfg.get("skip_skus") or ()
        listed = cfg.get("high_price_proven_skus") or ()
        out_priority, out_targets, added = list(priority), dict(targets), {}
        for sku in on["skus"]:
            if (arm(on, sku) != "B" or not _food(catalog.get(sku)) or sku in skip or sku in listed
                    or sku in out_priority or sku in out_targets):
                continue
            out_priority.append(sku)
            out_targets[sku] = on["units"]
            added[sku] = on["units"]
        return (out_priority, out_targets, added) if added else (priority, targets, {})
    except Exception:  # noqa: BLE001 - preserve pricing settings on trouble
        return priority, targets, {}
