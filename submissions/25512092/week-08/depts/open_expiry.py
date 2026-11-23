"""Open negotiations that went quiet (person's decision 11/09 ~11:30). A buyer that leaves without cancelling kept
its negotiation in seller_r3.Seller.open until the next restart, and the unit in it counted as taken, so other
buyers got "not in stock" (11/09 11:06: 322 of 578 held units, 12.4M at cost, locked for a median 6.7 h; 87 of
the 97 one-star stock reviews of 11/08). A negotiation with no message for EXPIRE_S (10 min; the longest normal one
of the last 24 h took 164 s) is closed as "expired" on the next message the seller gets; its unit is free again.
A buyer writing to an expired task later starts a new negotiation (the receipt hold of sold units is unchanged).
The seller writes STATE (open negotiations and the units they hold, at most every STATE_S) and one line per expired
negotiation to EXPIRED; tools/lock_watch.py reads both every hour.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "logs", "seller_open.json")
EXPIRED = os.path.join(HERE, "logs", "expired.jsonl")
EXPIRE_S = 600
RULES = os.path.join(HERE, "open_expiry.json")     # 11/09 ~15:05 (person): {"expire_s": 300} from the 18:07 restart
STATE_S = 30
_last = {"state": 0.0}


def touch(deal, now=None):
    if deal is not None:
        deal["t"] = now or time.time()


def expire_s():
    """The quiet limit in seconds: open_expiry.json "expire_s" (read on every call), else EXPIRE_S."""
    try:
        with open(RULES, encoding="utf-8") as f:
            return float(json.load(f).get("expire_s") or EXPIRE_S)
    except (OSError, ValueError, TypeError, AttributeError):
        return EXPIRE_S


def stale(open_, now=None, expire_s=None):
    """Task ids of negotiations quiet for more than expire_s. A negotiation seen for the first time gets now."""
    now = now or time.time()
    expire_s = expire_s if expire_s is not None else globals()["expire_s"]()
    out = []
    for tid, d in open_.items():
        t = d.setdefault("t", now)
        if now - t > expire_s:
            out.append(tid)
    return out


def unit_of(d):
    u = d.get("unit") or {}
    return u.get("unit"), u.get("sku") or d.get("x")


def log_expired(task, d, now=None):
    now = now or time.time()
    unit, sku = unit_of(d)
    try:
        with open(EXPIRED, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(now, 1), "task": task, "unit": unit, "sku": sku,
                                "quiet_s": round(now - d.get("t", now)), "subst": bool(d.get("subst"))}) + "\n")
    except OSError:
        pass


def write_state(open_, now=None, force=False):
    now = now or time.time()
    if not force and now - _last["state"] < STATE_S:
        return
    _last["state"] = now
    units = [u for u, _ in (unit_of(d) for d in open_.values()) if u]
    try:
        with open(STATE + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"ts": round(now), "open": len(open_), "units": units}, f)
        os.replace(STATE + ".tmp", STATE)
    except OSError:
        pass
