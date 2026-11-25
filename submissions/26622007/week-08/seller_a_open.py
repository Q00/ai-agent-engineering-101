"""Grade A dear goods: open at a share of list price when the usual first ask is under it
(review/W-20261109-1033-AX취약점루프/weakness-01-seller.md §S3).

Grade A carries the shop's revenue and almost none of its profit. 11/09 11:50 cut, 72 h, units sold 6 h before (the
warranty window closed), real unit cost from units_in.jsonl: grade A 3,043 units, 2,303M revenue, 2,149M cost, 47M
refunds, 106.8M net (4.6%); B 8.9%, C 26%. An A unit of 30만+ furniture sells at .69 of list on a .65 cost: the first
ask is 0.97 x the SKU median (price.high_median_up), the median is our own past sales, and buyers take it at .997 of the
ask. yms979 sold the same A furniture at .722-.728 of list, seochanit at .768-.791 (48 h), so the ask, not the buyer,
holds the price down.

Arm B opens at max(usual first ask, F, ratio x list), ratio 0.76 by default (0.70-0.82). This is deal["open"],
before seller_opening_default's 3% cut, so the posted first ask is about 0.737 x list (shop_memory "asks"[0] holds
what was posted). The reserve F stays; the round floors (shop_memory.concession_floor) are shares of the open, so they
rise with it: the treatment is "higher open with proportional round floors", and the verdict reports deal rate by
round. Arm A keeps the usual ask. Arms by SKU x grade, sha256(salt|sku|grade), first byte odd is B: our own deals feed the median, so
a per-talk split would lift arm A's ask too.

Eligible: goods, grade A, list price at or above min_list (300,000), no markdown (keep 1), no ask cap set for the
SKU x grade (price.ask_caps, whether or not it bound), no scarce ask,
no high-price trial cut (quote()'s why), and not in the margin study (sol_seller_margin assignment), so the two never
price the same talk. Everything else is untouched and unrecorded.

Switch: .market/seller_a_open.json {"enabled": true, "study": "<id>", "salt": "<salt>", "ratio": 0.76,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS. Missing,
unreadable, a missing or non-finite time, an empty or too long window, or anything else is off, and off is today's ask
exactly. SELLER_A_OPEN=0 in the environment is off too. Write it with switch_on(), which puts the times in. Read at
every quote, so no restart either way, and the treatment stops by itself at stop_at (11/09 18:12: a switch without
times kept arm B on until someone removed it; the experiment registry's expiry stops nothing). Never raises. A new
salt or ratio needs a new study id: the arms are a function of the salt, so changing it under one id would mix them.
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import profit_price

SWITCH = "seller_a_open.json"
RATIO, RATIO_LOW, RATIO_HIGH = 0.76, 0.70, 0.82
MIN_LIST = 300000
MAX_WINDOW_HOURS = 12


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def config(md=None, env=None, now=None):
    """{"study", "salt", "ratio", "start_at", "stop_at"} with the switch on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("SELLER_A_OPEN") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
    try:
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, ratio = d.get("study"), d.get("salt"), d.get("ratio", RATIO)
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    if not _num(ratio) or not RATIO_LOW <= ratio <= RATIO_HIGH:
        return None
    start, stop = d.get("start_at"), d.get("stop_at")
    if not _window(start, stop) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "ratio": float(ratio), "start_at": start, "stop_at": stop}


def switch_on(md, *, study, salt, start_at, hours, ratio=RATIO):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "ratio": ratio, "start_at": start_at, "stop_at": stop_at}
    if (not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _num(ratio)
            or not RATIO_LOW <= ratio <= RATIO_HIGH or not _window(start_at, stop_at)):
        raise ValueError("seller_a_open: bad switch")
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


def arm(cfg, sku, grade):
    """"A" or "B" for a SKU x grade under the study's salt; None without a study."""
    if not cfg or not isinstance(sku, str) or not sku:
        return None
    h = hashlib.sha256(f"{cfg['salt']}|{sku}|{grade}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def decide(u, q, *, keep, margin_assignment, pricing=None, md=None, env=None, now=None):
    """(first ask, record|None). u: the unit; q: profit_price.quote()'s result. Today's q["open"] unless arm B moves it."""
    try:
        p0 = q["open"]
        cfg = config(md, env, now)
        lp = u.get("list_price")
        why = str(q.get("why") or "")
        if (not cfg or u.get("kind") != "goods" or u.get("grade") != "A" or not _num(lp) or lp < MIN_LIST
                or not _num(p0) or not _num(q.get("floor")) or keep < 1 or margin_assignment is not None
                or why.startswith(("ask cap", "scarce ask", "trial "))
                or profit_price.ask_cap(profit_price.cfg_of(pricing), u) is not None):
            return p0, None
        a = arm(cfg, u.get("sku"), u.get("grade"))
        if a is None:
            return p0, None
        target = int(max(p0, q["floor"], math.ceil(cfg["ratio"] * lp)))     # never under today's ask or F; ratio < 1
        new = target if a == "B" else p0
        return new, {"study": cfg["study"], "arm": a, "ratio": cfg["ratio"], "usual": p0, "open": new,
                     "list": lp, "floor": q["floor"], "stop_at": cfg["stop_at"]}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale; the caller already read q["open"]
        print(f"SELLER_A_OPEN skipped: {type(e).__name__}", file=sys.stderr)
        return q.get("open") if isinstance(q, dict) else None, None
