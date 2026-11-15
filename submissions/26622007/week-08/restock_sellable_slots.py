"""Listed goods: a SKU's unit target counts only the units that can still sell at a profit, and the listed refills go
by what one supply call is expected to net (W-20261115-1605-AX매입칸; 대표님 11/15 16:01 "수정해 최대한 순위 높이자").

Why (review/W-20261115-1430-AX근본분석 fable-report.md 원인 2, astra-report.md 원인 ②; inventory.json 11/15 14:40):
3.76억 of cash sat idle while restock.fill_listed (need = target - held) and guard (cap = target) counted every unit
held against sku_target_units. 47 of the 58 targeted SKUs asked nothing: their slots held A draws costing 0.70+ of list
(136 of 163 A units), whose floor (cost + refund reserve + the A 8% margin) sits over what buyers pay (0.62-0.69). Over
the 72 h to 11/15 16:0x (measure units_in/units_out, A goods of 100k+ supplied 12-72 h before): A units costing under
0.62 of list sold 51% within 6 h, 0.62-0.70 35-47%, 0.70-0.73 22%, 0.73+ 19%, and 9% of the 0.70+ ones within their
first hour; a sale within 6 h kept 0.107 of list over cost, one after 6 h 0.028-0.037 (profit_price drops the A margin
at 6 h) and nothing after the refund (Astra: 293 A sales under 6 h +19.35M, 108 after 6 h -0.23M). The seller offers
the cheapest-to-sell unit of a SKU first (shop_stock.pick), so a new cheap draw is quoted ahead of the stuck ones.
11/10's restock_fresh_slots (age only, 2 h) raised goods sold 54 -> 64/h and gross margin +24% over 3 h and was stopped
at 21:01 when buys outran sales (stock +42M); stock counts at cost in Book (/rules) and costs 1% a day, and cash now
stands at ~3.7억, so this study holds the extra stock to 3 x target and a 1억 cash floor instead of a buys-vs-sales stop.

The rule (on: every listed goods SKU with a target, no arms; the trial is read against the 24 h before it):
- stuck(u): held `hours` (6) or more, or cost >= `dear_share` (0.70) of list and held `dear_hours` (1) or more.
  A unit without an arrival time is not stuck; one without a cost share is read by its age alone.
- counted = max(held - stuck, held - (hold_times - 1) x target), so held + new <= hold_times (3) x target. Not food, not
  an unlisted or untargeted SKU, not one in a surge over MAX_SURGE: every unit counts there.
- the units a line gets over today's count (cap - every unit held) need cash over `cash_floor` (1억) after every line
  before it (restock.guard); today's room keeps today's cash checks only.
- the listed refills (urgent_listed after the empty SKUs) go by need x unit_net, highest first, so the window's ~17
  calls go to the lines that net most: unit_net = list x (market price ratio x (1 - break) - expected cost share) x
  fresh_sell (0.39: the share of A draws sold within 6 h above) for goods, list x (ratio - 0.50) for food (sold within
  its shelf, none expired). A refill the model has no row for goes after them in today's order.
Only the target reads the count: max_units_per_sku and the high-price and surge caps still count every unit held, and
the cash buffer, stock cost and break guards apply as before. Prices and floors are not touched: nothing sells cheaper.

Switch: .market/restock_sellable_slots.json {"enabled": true, "study": "<id>", "hours": 6, "dear_share": 0.70,
"dear_hours": 1, "hold_times": 3, "cash_floor": 100000000, "fresh_sell": 0.39, "start_at": <epoch>, "stop_at": <epoch>};
on only in [start_at, stop_at), at most MAX_WINDOW_HOURS. Missing, unreadable, a bad value or time is off, and off is
today's count exactly. RESTOCK_SELLABLE_SLOTS=0 in the environment is off too. Write it with switch_on(). Read at
every restock run (a new process each time): no restart either way. With it on, restock_fresh_slots is not read.
Never raises.
"""
import collections
import json
import math
import os
import time
from pathlib import Path

SWITCH = "restock_sellable_slots.json"
HOURS, HOURS_LOW, HOURS_HIGH = 6.0, 1.0, 48.0
DEAR, DEAR_LOW, DEAR_HIGH = 0.70, 0.60, 1.0
DEAR_HOURS, DEAR_HOURS_LOW, DEAR_HOURS_HIGH = 1.0, 0.0, 24.0
HOLD_TIMES, HOLD_LOW, HOLD_HIGH = 3, 1, 3               # 3 x target at most (the safety line of this study)
CASH_FLOOR, CASH_FLOOR_LOW = 100_000_000, 100_000_000   # never under 1억
FRESH_SELL, FRESH_LOW, FRESH_HIGH = 0.39, 0.05, 1.0
EXPECTED_COST = {"food": 0.50, "goods": 0.59}           # restock.EXPECTED_COST
MAX_SURGE = 1.05
FAST_FOOD_HOURS = 0.5      # a held food lasting less goes before the goods refills (urgent_listed)
MAX_WINDOW_HOURS = 12


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valid(d, now):
    if not isinstance(d, dict):
        return None
    g = lambda k, dflt: d.get(k, dflt)
    study, start, stop = d.get("study"), d.get("start_at"), d.get("stop_at")
    hours, dear, dear_h = g("hours", HOURS), g("dear_share", DEAR), g("dear_hours", DEAR_HOURS)
    hold, floor_, sell = g("hold_times", HOLD_TIMES), g("cash_floor", CASH_FLOOR), g("fresh_sell", FRESH_SELL)
    if not (isinstance(study, str) and study) \
            or not (_num(hours) and HOURS_LOW <= hours <= HOURS_HIGH) \
            or not (_num(dear) and DEAR_LOW <= dear <= DEAR_HIGH) \
            or not (_num(dear_h) and DEAR_HOURS_LOW <= dear_h <= DEAR_HOURS_HIGH) \
            or not (type(hold) is int and HOLD_LOW <= hold <= HOLD_HIGH) \
            or not (_num(floor_) and floor_ >= CASH_FLOOR_LOW) \
            or not (_num(sell) and FRESH_LOW <= sell <= FRESH_HIGH) \
            or not (_num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600) \
            or not _num(now) or not start <= now < stop:
        return None
    return {"study": study, "hours": float(hours), "dear_share": float(dear), "dear_hours": float(dear_h),
            "hold_times": hold, "cash_floor": float(floor_), "fresh_sell": float(sell), "start_at": start, "stop_at": stop}


def config(md=None, env=None, now=None):
    """The switch's settings when it is on at now, else None."""
    try:
        env = os.environ if env is None else env
        now = time.time() if now is None else now
        if str(env.get("RESTOCK_SELLABLE_SLOTS") or "").strip().lower() in ("0", "false", "no", "off"):
            return None
        md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict) or d.get("enabled") is not True:
            return None
        return _valid(d, now)
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return None


def switch_on(md, *, study, start_at, hours, stuck_hours=HOURS, dear_share=DEAR, dear_hours=DEAR_HOURS,
              hold_times=HOLD_TIMES, cash_floor=CASH_FLOOR, fresh_sell=FRESH_SELL):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "hours": stuck_hours, "dear_share": dear_share, "dear_hours": dear_hours,
         "hold_times": hold_times, "cash_floor": cash_floor, "fresh_sell": fresh_sell, "start_at": start_at,
         "stop_at": stop_at}
    if _valid(d, start_at) is None:
        raise ValueError("restock_sellable_slots: bad switch")
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


def on(cfg):
    """The settings when the run's cfg has the switch on at the run's clock (cfg["fresh_now"]), else None."""
    cfg = cfg if isinstance(cfg, dict) else {}
    at = cfg.get("fresh_now")
    return _valid(cfg.get("sellable_slots"), at if _num(at) else time.time())


def stuck(u, s, now):
    """True for a unit held s["hours"] or more, or a dear draw (cost >= dear_share of list) held dear_hours or more."""
    at = u.get("supplied_at")
    if not _num(at):
        return False
    age = (now - at) / 3600
    if age >= s["hours"]:
        return True
    cost, lp = u.get("cost"), u.get("list_price")
    return _num(cost) and _num(lp) and lp > 0 and cost / lp >= s["dear_share"] and age >= s["dear_hours"]


def _listed(cfg):
    out = set()
    for key in ("high_price_proven_skus", "priority_skus"):
        v = cfg.get(key)
        out |= {x for x in (v if isinstance(v, (list, tuple)) else ()) if isinstance(x, str)}
    return out


def counted(units, cfg, catalog, factors=None, now=None):
    """(Counter: the units of each SKU as the target counts them, {sku: {"held", "stuck", "counted"}} for those counted
    lower than held). Off, outside the window or on any trouble: every unit counts."""
    plain = collections.Counter(u.get("sku") for u in units or () if isinstance(u, dict))
    try:
        at = (cfg or {}).get("fresh_now")
        now = at if _num(at) else time.time() if now is None else now
        s = _valid((cfg or {}).get("sellable_slots"), now)
        targets = (cfg or {}).get("sku_target_units")
        if s is None or not isinstance(targets, dict):
            return plain, {}
        listed, out, changed = _listed(cfg), collections.Counter(plain), {}
        n_stuck = collections.Counter()
        for u in units or ():
            if isinstance(u, dict) and stuck(u, s, now):
                n_stuck[u.get("sku")] += 1
        for sku, held in plain.items():
            t = targets.get(sku) if isinstance(sku, str) else None
            f = (factors or {}).get(sku, 1.0)
            if type(t) is not int or t < 1 or sku not in listed or (catalog.get(sku) or {}).get("kind") != "goods" \
                    or not _num(f) or f > MAX_SURGE:
                continue
            n = min(held, max(held - n_stuck[sku], held - (s["hold_times"] - 1) * t, 0))
            if n < held:
                out[sku] = n
                changed[sku] = {"held": held, "stuck": n_stuck[sku], "counted": n}
        return out, changed
    except Exception:   # noqa: BLE001 - a count that cannot be made is the old count
        return plain, {}


def unit_net(sku, catalog, model, s, prior_break=0.0, factor=1.0):
    """Expected net of one more unit of the SKU before the close: goods list x (ratio x (1 - break) - 0.59 x surge)
    x fresh_sell, food list x (ratio - 0.50 x surge). prior_break: the SKU's break chance (restock passes the measured
    ledger rate as fill_listed does, else prior_break). None without a list price or a model price ratio."""
    f = factor if _num(factor) and factor > 0 else 1.0
    x = catalog.get(sku) or {}
    m = ((model or {}).get("skus") or {}).get(sku) if isinstance(model, dict) else None
    lp, ratio = x.get("list_price"), (m or {}).get("price_ratio") if isinstance(m, dict) else None
    if not (_num(lp) and lp > 0 and _num(ratio)):
        return None
    if x.get("kind") == "food":
        return lp * (ratio - EXPECTED_COST["food"] * f)
    b = prior_break if _num(prior_break) and 0 <= prior_break < 1 else 0.0
    return lp * (ratio * (1 - b) - EXPECTED_COST["goods"] * f) * s["fresh_sell"]


def call_value(sku, cfg, catalog, model, have, counts, brk=None, factor=1.0):
    """need x unit_net for a listed SKU with a target (need: target - counted units, held and on their way, at most
    20); None when the model gives no net. have / counts: Counters of every unit and of the counted ones."""
    s = on(cfg)
    if s is None:
        return None
    net = unit_net(sku, catalog, model, s, cfg.get("prior_break") if brk is None else brk, factor)
    t = (cfg.get("sku_target_units") or {}).get(sku) if isinstance(cfg.get("sku_target_units"), dict) else None
    if net is None or type(t) is not int:
        return None
    top = cfg.get("max_units_per_sku", 20)
    top = top if _num(top) else 20      # the guard's unit cap on every unit held (review ad9cee9 M2)
    need = max(0, min(20, t - counts[sku], s["hold_times"] * t - have[sku], top - have[sku]))
    return need * net
