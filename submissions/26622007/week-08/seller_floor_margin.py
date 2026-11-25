"""Grade A goods: a reserve with a margin over cost, not break-even (W-20261109-2040-AX셀러전략).

Book is half of shop ALL and it is where we lose: 11/09 20:34 board, book 284M (percentile .774, 15th of 63) against
yms979 1,058M, while revenue (.919) and stars (.935) are near the top. Book grew 0.5M/h on 48M/h revenue over 24 h
(1.0%); yms979 4.5M/h on 50M/h (9.0%), 25620027 3.8M/h on 33M/h. Our 24 h sales joined to units_in.jsonl's real
cost: grade A furniture of 100만+ cost .651 of list and sold at .677 (net of refunds 2.0%), 30-100만 .658/.703 (3.5%).
The public ledger's same 24 h: yms979 sold A furniture at a median .718 of list, 25620027 .752, seochanit .789,
dae-yangkim .793; we .684. Since 11/06 22:36 (5a1aaed) the reserve F is break-even, cost x 1.0025 / (1 - b) with
min_profit 1 and min_profit_share 0, and for grade A the first ask is F itself whenever 0.97 x the SKU median is under
it, so a deal at F keeps about 3% of price before refunds.

Arm B raises the reserve to profit_price.floor() with min_profit_share = share (0.05 by default, 0.02-0.10) of list:
F_B = ceil((C + 0.0025 C + share x L) / (1 - b)), about .728 of list on a .651 cost, never over list unless today's F
already is, never under today's F. The ask rises to the new reserve when it is under it. Arm A keeps today's price.
Both arms are recorded (deal["floor_margin"] -> shop_memory), so the verdict is per arm: quotes, deals, price / list,
net of real cost and refunds. Arms by SKU x grade, sha256("floor-margin|salt|sku|grade"), first byte odd is B: our
own deals feed the SKU median.

Eligible: goods, grade A, a list price, no markdown (keep 1), no ask cap set for the SKU x grade (slow stock), no
scarce ask and no high-price trial cut (quote()'s why, as seller_a_open), not in the margin study (sol_seller_margin assignment) and not a seller_a_open talk (either arm): a-open owns 30만+ A goods
while it is on (11/09 18:47-03:47, verdict pending), so the two never price the same talk; when a-open stops by
itself, those talks come into this study.

Switch: .market/seller_floor_margin.json {"enabled": true, "study": "<id>", "salt": "<salt>", "share": 0.05,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS.
Missing, unreadable, a bad value or time, or anything else is off, and off is today's price exactly.
SELLER_FLOOR_MARGIN=0 in the environment is off too. Write it with switch_on(). Read at every quote: no restart either
way, and the treatment stops by itself at stop_at. Never raises. A new salt or share needs a new study id.
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import profit_price

SWITCH = "seller_floor_margin.json"
SHARE, SHARE_LOW, SHARE_HIGH = 0.05, 0.02, 0.10
MAX_WINDOW_HOURS = 20       # covers a-open's last hours plus a 12 h window after it stops (11/10 03:47)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _share_ok(v):
    return _num(v) and SHARE_LOW <= v <= SHARE_HIGH


def config(md=None, env=None, now=None):
    """{"study", "salt", "share", "start_at", "stop_at"} with the switch on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("SELLER_FLOOR_MARGIN") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
    try:
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, share = d.get("study"), d.get("salt"), d.get("share", SHARE)
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share):
        return None
    start, stop = d.get("start_at"), d.get("stop_at")
    if not _window(start, stop) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "share": float(share), "start_at": start, "stop_at": stop}


def switch_on(md, *, study, salt, start_at, hours, share=SHARE):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "start_at": start_at, "stop_at": stop_at}
    if (not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share)
            or not _window(start_at, stop_at)):
        raise ValueError("seller_floor_margin: bad switch")
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
    h = hashlib.sha256(f"floor-margin|{cfg['salt']}|{sku}|{grade}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def decide(u, q, *, reserve, ask, keep, margin_assignment, a_open, pricing=None, md=None, env=None, now=None):
    """(reserve, ask, record|None). u: the unit; q: profit_price.quote()'s result; reserve and ask: the talk's so far.
    Today's reserve and ask unless arm B raises them."""
    try:
        cfg = config(md, env, now)
        if (not cfg or not isinstance(u, dict) or u.get("kind") != "goods" or u.get("grade") != "A"
                or not _num(u.get("list_price")) or u["list_price"] <= 0 or not _num(u.get("cost"))
                or not _num(reserve) or not _num(ask) or not _num(q.get("b")) or keep < 1
                or margin_assignment is not None or a_open
                or str(q.get("why") or "").startswith(("ask cap", "scarce ask", "trial "))
                or profit_price.ask_cap(profit_price.cfg_of(pricing), u) is not None):
            return reserve, ask, None
        a = arm(cfg, u.get("sku"), u.get("grade"))
        if a is None:
            return reserve, ask, None
        lp = u["list_price"]
        raised = profit_price.floor(u["cost"], lp, "goods", q["b"],
                                    {**profit_price.cfg_of(pricing), "min_profit": 1, "min_profit_share": cfg["share"]})
        new_reserve, new_ask = (int(max(reserve, min(raised, lp))), None) if a == "B" else (reserve, ask)
        if a == "B":
            new_ask = int(max(ask, new_reserve))
        return new_reserve, new_ask, {"study": cfg["study"], "arm": a, "share": cfg["share"], "usual_reserve": reserve,
                                      "usual_ask": ask, "reserve": new_reserve, "ask": new_ask, "list": lp,
                                      "cost": u["cost"], "stop_at": cfg["stop_at"]}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale
        print(f"SELLER_FLOOR_MARGIN skipped: {type(e).__name__}", file=sys.stderr)
        return reserve, ask, None
