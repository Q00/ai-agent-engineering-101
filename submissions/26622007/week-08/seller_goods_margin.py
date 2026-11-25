"""Goods of every grade: a reserve that keeps a share of list price after the expected warranty refund
(W-20261110-1155-AXBook근본; 대표님 11/10 11:51 "너무 변화가 없다, 근본 원인 파악해서 올리는 거 맞나").

Book (/rules, Round 3 Scoring) = balance - starting capital - bankruptcy money + what unsold stock cost, so it moves
only by sale price - unit cost - warranty refunds - storage - spoiled food - vote fees. Over the whole public ledger to
11/10 12:29 (review/W-20261110-1155-AXBook근본/report.md) our estimate of that sum is 297M against the board's 308M,
and of the top five 1,125M (yms979), 805M (25620027), 743M (moss-and-oak), 689M (seochanit) against 1,118/801/746/690M:
gross margin 693M on 5,205M sales (13.3%) less 391M of refunds. Over the last 48 h our goods kept 28.7M/day net of
refunds on 1,402 sales a day; 25620027 95.6M on 1,675, seochanit 103.3M on 726. The volume is there, the margin is not:
since 11/06 22:36 (5a1aaed) the reserve F is break-even, cost x 1.0025 / (1 - b) with min_profit 1 and
min_profit_share 0, and the first ask is F whenever the SKU median is under it, so most goods sell at F: an expected
profit of 0 a unit. The kit log shows it (11:45:45, a grade C G75f11c19 settled at its floor 71,279, b 34%).

Price is not what limits the sales: goods sell within the hour at the top shops' prices. Median hours from supply to
sale over the same 12 h buckets, from the ledger: seochanit at .76-.80 of list 0.25-0.44 h, 25620027 at .72-.74
0.3-0.7 h, we at .66-.68 0.29-0.48 h, and we at .82-.87 on 11/04-11/05 0.18-0.34 h. floor-margin-2040 (grade A,
+4% of list on half the SKU x grades) after a-open stopped (03:47-12:29): arm B 133 sales at .725 of list, net of real
cost and refunds .091 of list, 0.48 h to sell; arm A 115 at .690, .059, 0.39 h.

Arm B: reserve F_B = profit_price.floor() with min_profit_share = share (0.08 by default, 0.02-0.15) of list, the
unit's own b (grade B 12.2%, C 34.4%): ceil((C + 0.0025 C + share x L) / (1 - b)), so the expected profit of a sale
after the refund chance is share x L. About .76 of list for an A unit at cost .651 and b .035, .70 for B at .53 and
.122, .70 for C at .38 and .344: the prices seochanit and 25620027 sell at. Never over list unless today's F already
is, never under today's F; the ask rises to the new reserve when it is under it. Arm A keeps today's price. Arms by
SKU x grade, sha256("goods-margin|salt|sku|grade"): the first four bytes under treat (0.5 by default, 0.3-0.8) is B,
an axis of its own (floor-margin and a-open hash other keys). Recorded in deal["goods_margin"] -> shop_memory.

Eligible: goods of any grade with a list price and a cost, no markdown (keep 1), no ask cap for the SKU x grade, not
a SKU with a scarce ask nor a SKU x grade in the high-price trial's half (whether or not either moved this price), not in the margin study, not an a-open talk, not a floor-margin talk (either
arm: floor-margin owns its grade A units while it is on, to 11/10 15:47:26; after it stops by itself they come in here),
and not a grade_break_trial unit (either arm, to 11/10 13:00): each running study keeps its own arms clean.

Switch: .market/seller_goods_margin.json {"enabled": true, "study": "<id>", "salt": "<salt>", "share": 0.08,
"treat": 0.5, "start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most
MAX_WINDOW_HOURS. Missing, unreadable, a bad value or time, or anything else is off, and off is today's price exactly.
SELLER_GOODS_MARGIN=0 in the environment is off too. Write it with switch_on(). Read at every quote: no restart either
way, and the treatment stops by itself at stop_at. Never raises. A new salt, share or treat needs a new study id.
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import profit_price

SWITCH = "seller_goods_margin.json"
SHARE, SHARE_LOW, SHARE_HIGH = 0.08, 0.02, 0.15
TREAT, TREAT_LOW, TREAT_HIGH = 0.5, 0.3, 0.8
MAX_WINDOW_HOURS = 12       # the experiment register's longest window (ax-experiments --max-hours 12)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _share_ok(v):
    return _num(v) and SHARE_LOW <= v <= SHARE_HIGH


def _treat_ok(v):
    return _num(v) and TREAT_LOW <= v <= TREAT_HIGH


def config(md=None, env=None, now=None):
    """{"study", "salt", "share", "treat", "start_at", "stop_at"} with the switch on at now, else None."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("SELLER_GOODS_MARGIN") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return None
        study, salt = d.get("study"), d.get("salt")
        share, treat = d.get("share", SHARE), d.get("treat", TREAT)
        if not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share) \
                or not _treat_ok(treat):
            return None
        start, stop = d.get("start_at"), d.get("stop_at")
        if not _window(start, stop) or not _num(now) or not start <= now < stop:
            return None
        return {"study": study, "salt": salt, "share": float(share), "treat": float(treat), "start_at": start,
                "stop_at": stop}
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None


def switch_on(md, *, study, salt, start_at, hours, share=SHARE, treat=TREAT):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "treat": treat, "start_at": start_at,
         "stop_at": stop_at}
    if (not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share)
            or not _treat_ok(treat) or not _window(start_at, stop_at)):
        raise ValueError("seller_goods_margin: bad switch")
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
    """"A" or "B" for a SKU x grade under the study's salt and treat share; None without a study."""
    if not cfg or not isinstance(sku, str) or not sku or not isinstance(grade, str) or not grade:
        return None
    h = hashlib.sha256(f"goods-margin|{cfg['salt']}|{sku}|{grade}".encode("utf-8", "surrogatepass")).digest()
    return "B" if int.from_bytes(h[:4], "big") / 2 ** 32 < cfg.get("treat", TREAT) else "A"


def decide(u, q, *, reserve, ask, keep, margin_assignment, a_open, floor_margin, pricing=None, md=None, env=None,
           now=None):
    """(reserve, ask, record|None). u: the unit; q: profit_price.quote()'s result; reserve and ask: the talk's so far
    (after a-open and floor-margin). Today's reserve and ask unless arm B raises them."""
    try:
        cfg = config(md, env, now)
        if not cfg or not isinstance(u, dict) or not isinstance(q, dict):
            return reserve, ask, None
        pcfg = profit_price.cfg_of(pricing)
        scarce = pcfg.get("scarce_asks") if isinstance(pcfg.get("scarce_asks"), dict) else {}
        if (u.get("kind") != "goods"
                or not _num(u.get("list_price")) or u["list_price"] <= 0 or not _num(u.get("cost")) or u["cost"] < 0
                or not _num(reserve) or not _num(ask) or not _num(q.get("b")) or not 0 <= q["b"] < 1
                or not _num(keep) or keep < 1
                or margin_assignment is not None or a_open or floor_margin or q.get("b_trial") is not None
                or str(q.get("why") or "").startswith(("ask cap", "scarce ask", "trial "))
                or u.get("sku") in scarce
                or profit_price.trial_arm(u.get("sku"), u.get("grade"), pcfg.get("high_price_trial"))
                or profit_price.ask_cap(pcfg, u) is not None):
            return reserve, ask, None
        a = arm(cfg, u.get("sku"), u.get("grade"))
        if a is None:
            return reserve, ask, None
        lp = u["list_price"]
        new_reserve, new_ask = reserve, ask
        if a == "B":
            raised = profit_price.floor(u["cost"], lp, "goods", q["b"],
                                        {**pcfg, "min_profit": 1,
                                         "min_profit_share": cfg["share"]})
            new_reserve = math.ceil(max(reserve, min(raised, lp)))
            new_ask = math.ceil(max(ask, new_reserve))
        return new_reserve, new_ask, {"study": cfg["study"], "arm": a, "share": cfg["share"], "treat": cfg["treat"],
                                      "grade": u.get("grade"), "usual_reserve": reserve, "usual_ask": ask,
                                      "reserve": new_reserve, "ask": new_ask, "list": lp, "cost": u["cost"],
                                      "b": q["b"], "stop_at": cfg["stop_at"]}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale
        print(f"SELLER_GOODS_MARGIN skipped: {type(e).__name__}", file=sys.stderr)
        return reserve, ask, None
