"""Goods: a unit is not sold under what buyers pay for its SKU and grade, whatever it cost
(W-20261110-1346-AXFable검토; 대표님 11/10 16:27 "그럼 fable로 수정해": 판가 = max(단위 손익분기, 시세 하한)).

/rules (Round 3 rulebook, Supply): "Each unit's grade and cost are drawn on arrival: A 55 to 75%, B 40 to 60%, C 25 to
45% of list price." The reserve is each unit's own break-even (profit_price.floor, min_profit_share 0 since 11/06
22:36), so the sale price follows the draw, not the market: of the goods of 100k+ supplied 11/08 00:00-11/09 12:00
(unit cost and price from .market/measure, refunds from the ledger; review/W-20261110-1346-AXFable검토/report.md) a
grade A unit drawn under .60 of list sold at .633, at .60-.65 at .658, at .65-.70 at .703, at .70-.75 at .754: about 3%
of list over its cost every time, and the refunds took 83% of that (gross 201.1M, refunds 167.6M, net 33.5M over 2,437
units). The same buyers paid more for the same SKU and grade the same hours: against the median of the 3 h before
each sale (every shop's settles), 51% of our grade A sales of the last 36 h, 62% of B and 42% of C were under it, and
price is not what sold them: at .95-1.00 of that median 82% sold within the hour, at 1.00-1.05 69% (median 0.54 h).
Lifting each of those sales to k x the median with none lost would have kept 0.28M/h more at k .95, 0.50M/h at .97,
0.96M/h at 1.0 (an upper bound: the A/B measures what is lost).

Arm B: reserve = max(today's reserve, ceil(share x median)), never over the list price; the ask rises to the reserve
when it is under it. median: the seller's own (seller.sku_median: briefing.json's SKU x grade ratio of the last
restock.price_hours, or the SKU's). A unit whose break-even is already over share x median (a dear draw) keeps today's
price: nothing is ever lowered, nothing is sold under cost + expected refund. Arm A keeps today's price. Arms by
SKU x grade, sha256("market-floor|salt|sku|grade"): the first four bytes under treat is B, an axis of its own
(goods-margin hashes "goods-margin|...").

Eligible: goods with a list price, a cost and a grade, a median between MEDIAN_LOW and 1.0 of list, no markdown
(keep 1), not a unit of another price study: the margin study, an a-open or floor-margin talk, a goods-margin talk
(either arm: while goods-margin-1110 runs, to 11/11 00:52, every unit it owns stays its own), a grade_break_trial unit,
a SKU x grade with an ask cap, a SKU with a scarce ask, the high-price trial's half.

Switch: .market/seller_market_floor.json {"enabled": true, "study": "<id>", "salt": "<salt>", "share": 1.0,
"treat": 0.5, "start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most
MAX_WINDOW_HOURS. Missing, unreadable, a bad value or time, or anything else is off, and off is today's price exactly.
SELLER_MARKET_FLOOR=0 in the environment is off too. Write it with switch_on(). Read at every quote: no restart either
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

SWITCH = "seller_market_floor.json"
SHARE, SHARE_LOW, SHARE_HIGH = 1.0, 0.9, 1.05
TREAT, TREAT_LOW, TREAT_HIGH = 0.5, 0.3, 1.0
MEDIAN_LOW = 0.3            # a median under this share of list (or over list) is not a price to hold a unit at
MAX_WINDOW_HOURS = 12       # the experiment register's longest window (ax-experiments --max-hours 12)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _valid(d, now):
    if not isinstance(d, dict):
        return None
    study, salt = d.get("study"), d.get("salt")
    share, treat, start, stop = d.get("share", SHARE), d.get("treat", TREAT), d.get("start_at"), d.get("stop_at")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) \
            or not (_num(share) and SHARE_LOW <= share <= SHARE_HIGH) \
            or not (_num(treat) and TREAT_LOW <= treat <= TREAT_HIGH) \
            or not _window(start, stop) or not _num(now) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "share": float(share), "treat": float(treat), "start_at": start,
            "stop_at": stop}


def config(md=None, env=None, now=None):
    """{"study", "salt", "share", "treat", "start_at", "stop_at"} with the switch on at now, else None."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("SELLER_MARKET_FLOOR") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return None
        return _valid(d, now)
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None


def switch_on(md, *, study, salt, start_at, hours, share=SHARE, treat=TREAT):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "treat": treat, "start_at": start_at,
         "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("seller_market_floor: bad switch")
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
    try:
        if not isinstance(cfg, dict) or not isinstance(sku, str) or not sku or not isinstance(grade, str) or not grade:
            return None
        h = hashlib.sha256(f"market-floor|{cfg['salt']}|{sku}|{grade}".encode("utf-8")).digest()
        return "B" if int.from_bytes(h[:4], "big") < cfg["treat"] * (1 << 32) else "A"
    except (KeyError, TypeError, ValueError, UnicodeError):
        return None


def decide(u, q, *, reserve, ask, keep, median, margin_assignment=None, a_open=None, floor_margin=None,
           goods_margin=None, pricing=None, md=None, env=None, now=None):
    """(reserve, ask, record|None). u: the unit; q: profit_price.quote()'s result; reserve and ask: the talk's so far
    (after a-open, floor-margin and goods-margin); median: seller.sku_median for the unit. Today's reserve and ask
    unless arm B raises them."""
    try:
        cfg = config(md, env, now)
        if not cfg or not isinstance(u, dict) or not isinstance(q, dict):
            return reserve, ask, None
        pcfg = profit_price.cfg_of(pricing)
        scarce = pcfg.get("scarce_asks") if isinstance(pcfg.get("scarce_asks"), dict) else {}
        lp = u.get("list_price")
        if (u.get("kind") != "goods"
                or not _num(lp) or lp <= 0 or not _num(u.get("cost")) or u["cost"] < 0
                or not _num(reserve) or not _num(ask) or not _num(keep) or keep < 1
                or not _num(median) or not MEDIAN_LOW * lp <= median <= lp
                or margin_assignment is not None or a_open or floor_margin or goods_margin
                or q.get("b_trial") is not None
                or str(q.get("why") or "").startswith(("ask cap", "scarce ask", "trial "))
                or u.get("sku") in scarce
                or profit_price.trial_arm(u.get("sku"), u.get("grade"), pcfg.get("high_price_trial"))
                or profit_price.ask_cap(pcfg, u) is not None):
            return reserve, ask, None
        a = arm(cfg, u.get("sku"), u.get("grade"))
        if a is None:
            return reserve, ask, None
        new_reserve, new_ask = reserve, ask
        if a == "B":
            new_reserve = math.ceil(max(reserve, min(math.ceil(cfg["share"] * median), lp)))
            new_ask = math.ceil(max(ask, new_reserve))
        return new_reserve, new_ask, {"study": cfg["study"], "arm": a, "share": cfg["share"], "treat": cfg["treat"],
                                      "grade": u.get("grade"), "median": round(median), "usual_reserve": reserve,
                                      "usual_ask": ask, "reserve": new_reserve, "ask": new_ask,
                                      "lifted": new_reserve > reserve, "list": lp, "cost": u["cost"], "b": q.get("b"),
                                      "stop_at": cfg["stop_at"]}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale
        print(f"SELLER_MARKET_FLOOR skipped: {type(e).__name__}", file=sys.stderr)
        return reserve, ask, None
