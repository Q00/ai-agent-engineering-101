"""Request-based buying (person's decision 11/08 ~13:45). depts/purchasing.make_plan calls candidates() once a run.

A SKU buyers asked for by name at least MIN_REQUESTS times in the last 24 h while I had no unit of it (substitute
offers in market.db shop_subst, and "not in stock" answers of the substitute experiment in logs/subst_exp.jsonl)
becomes a buy candidate whatever its place on the hot list, when it passes the basic filters:
  margin   the market average (market_db.sku_avg) >= expected cost x (1 + purchasing.min_margin); expected cost =
           list x purchasing.COST_SHARE x live surge
  food     shelf life >= FOOD_SHELF_H (72 h), and not perishable (부패주의)
  price    expected cost <= qc.HIGH_MIN (high-price units keep their own rules)
Target: TARGET units held per SKU, FAST_TARGET (8; 5 until 11/08 ~20:35) for a SKU whose request-based units of the last 24 h went from
supply to sale in FAST_H (1 h) or less at the median (person's decision 11/08 ~16:50; an unsold unit counts with its
age so far); at most MAX_LINES lines a run (the run has 10 lines in all).
From 11/09 ~23:10 (person): MAX_LINES 2 and FAST_ONLY: only SKUs with a measured median of FAST_H or less stay.
From 11/10 ~14:15 (person, normal goods ran short): MAX_LINES 4 again = 3 regular lines + TRIAL_LINES (1) trial; the
return time in request_lines.json (shown in the 15:00 and the deep-high 3 h checks).
From 11/09 ~23:40 (person): one of the two lines (TRIAL_LINES) tries a SKU with no turnover record, target
TRIAL_TARGET (2); TRIAL_H after its first trial buy it becomes regular (trial_ok) if its median is <= FAST_H, else
it is dropped (dropped_why trial). With no trial candidate the regular SKUs may use both lines.
Drop: a SKU first bought this way that sold none of its units in the 24 h after (my settles) leaves the list for good
(logs/request_buy.json, decision request_buy_drop).
"""
import json
import math
import os
import sqlite3
import statistics
import time
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "logs", "request_buy.json")
MIN_REQUESTS = 50
TARGET = 2
FAST_TARGET = 8                 # 11/08 ~20:35 person: 5 -> 8
FAST_H = 1.0
FOOD_SHELF_H = 72
MAX_LINES = 4                   # 11/10 ~14:15 person: 2 -> 4 (3 regular + 1 trial); 11/09 ~23:10: 4 -> 2; 11/08: 3 -> 4
FAST_ONLY = True                # 11/09 ~23:10 person: only SKUs whose supply -> sale median is <= FAST_H
TRIAL_LINES = 1                 # 11/09 ~23:40 person: 1 of the 2 lines tries a SKU with no turnover record yet
TRIAL_TARGET = 2
TRIAL_H = 6                     # median <= FAST_H within 6 h of the first trial buy -> regular, else dropped
DROP_AFTER_S = 86400
ME = "choo-chu"
LINES = os.path.join(HERE, "request_lines.json")     # 11/15 ~14:20 (person): the line counts are read from here


def lines_cfg(path=None):
    """(lines, trial lines) from request_lines.json ("lines", "trial"); MAX_LINES / TRIAL_LINES without it."""
    try:
        with open(path or LINES, encoding="utf-8") as f:
            r = json.load(f)
        return int(r.get("lines", MAX_LINES)), int(r.get("trial", TRIAL_LINES))
    except (OSError, ValueError, TypeError):
        return MAX_LINES, TRIAL_LINES


def _state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def requests(db, now):
    """{sku: requests} named while I had no unit, last 24 h."""
    t0 = now - 86400
    c = Counter(x for (x,) in db.execute("SELECT x FROM shop_subst WHERE ts>=?", (t0,)))
    try:
        with open(os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if '"no_offer"' in ln:
                    e = json.loads(ln)
                    if e["ts"] >= t0 and e.get("x"):
                        c[e["x"]] += 1
    except OSError:
        pass
    return c


def turnover(db, st, now):
    """{sku: median hours supply -> sale} of the units of request-based SKUs supplied in the last 24 h (after the
    SKU's first request-based buy); an unsold unit counts with its age so far."""
    out = {}
    for sku, s in st.items():
        if not s.get("first_buy"):
            continue
        rows = db.execute("SELECT m.supplied_at, (SELECT MIN(x.ts) FROM settles x WHERE x.unit=m.unit AND x.shop=? AND "
                          "x.ts>=m.supplied_at) FROM my_stock m WHERE m.sku=? AND m.supplied_at>=? AND m.supplied_at>=?",
                          (ME, sku, now - 86400, s["first_buy"] - 600)).fetchall()
        h = sorted(((sold or now) - sup) / 3600 for sup, sold in rows if sup)
        if h:
            out[sku] = statistics.median(h)
    return out


def target(sku, turn):
    t = turn.get(sku)
    return FAST_TARGET if t is not None and t <= FAST_H else TARGET


def candidates(catalog, held, surge, log=print, now=None):
    """[(sku, qty, avg, why)] for this run, best-requested first, at most MAX_LINES. Drops and records SKUs that sold
    nothing in the 24 h after their first request-based buy."""
    import decisions
    import policy
    from depts.qc import HIGH_MIN
    from market_db import sku_avg
    from depts.purchasing import COST_SHARE
    now = now or time.time()
    ML, TL = lines_cfg()                               # 11/15 ~14:20: 4 -> 6 (5 + 1 trial)
    st = _state()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        req = requests(db, now)
        mm = policy.get("purchasing", "min_margin")
        for sku, s in list(st.items()):                # the 24 h no-sale drop
            if s.get("dropped") or not s.get("first_buy") or now - s["first_buy"] < DROP_AFTER_S:
                continue
            sold = db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND sku=? AND ts>=? AND ts<?",
                              (ME, sku, s["first_buy"], s["first_buy"] + DROP_AFTER_S)).fetchone()[0]
            if sold == 0:
                s["dropped"] = round(now)
                decisions.record("purchasing", "request_buy_drop", sku, 0,
                                 f"request-based SKU sold none in the 24 h after its first buy "
                                 f"({time.strftime('%m-%d %H:%M', time.localtime(s['first_buy']))}): no longer a candidate")
                log(f"[request] {sku}: no sale in 24 h after the first buy -> dropped")
        turn = turnover(db, st, now)
        for sku, s in st.items():                       # 11/09 ~23:40: trial verdicts, TRIAL_H after the first trial buy
            if s.get("trial") and not s.get("trial_ok") and not s.get("dropped") and now - s["trial"] >= TRIAL_H * 3600:
                ok = sku in turn and turn[sku] <= FAST_H
                if ok:
                    s["trial_ok"] = round(now)
                else:
                    s.update(dropped=round(now), dropped_why="trial")
                med = "-" if sku not in turn else f"{turn[sku]:.2f} h"
                decisions.record("purchasing", "request_trial_ok" if ok else "request_trial_drop", sku, 0,
                                 f"request trial {TRIAL_H} h: supply->sale median {med} (<= {FAST_H} h: regular)")
                log(f"[request] trial {sku}: median {med} -> {'regular' if ok else 'dropped'}")
        out, trial, trials = [], [], 0
        for sku, n in req.most_common():
            if n < MIN_REQUESTS:
                break
            if (st.get(sku) or {}).get("dropped"):
                continue
            c = catalog.get(sku) or {}
            lp = c.get("list_price") or 0
            if not lp:
                continue
            kind = c.get("kind", "goods")
            if kind == "food" and ((c.get("shelf_hours") or 0) < FOOD_SHELF_H or "부패주의" in (c.get("category") or "")):
                continue
            cost = lp * COST_SHARE.get(kind, 0.6) * surge.get(sku, 1.0)
            if cost > HIGH_MIN:
                continue
            avg, k = sku_avg(db, sku, now)
            if avg is None or avg < cost * (1 + mm):
                continue
            s = st.get(sku) or {}
            in_trial = bool(s.get("trial")) and not s.get("trial_ok")
            new = sku not in turn and not s.get("trial")
            if in_trial or (new and FAST_ONLY):               # a trial line (running, or a new SKU to try)
                if trials >= TL:
                    continue
                trials += 1                                   # a running trial keeps its slot, bought up or not
                qty = TRIAL_TARGET - held.get(sku, 0)
                if qty > 0:
                    st.setdefault(sku, {})["trial_pending"] = True
                    trial.append((sku, qty, round(avg), f"{n} requests in 24 h, avg {round(avg):,} (n {k}), est. cost "
                                                       f"{int(cost):,}, trial (target {TRIAL_TARGET}, {TRIAL_H} h)"))
                continue
            if FAST_ONLY and not (sku in turn and turn[sku] <= FAST_H):
                continue
            tgt = target(sku, turn)
            qty = tgt - held.get(sku, 0)
            if qty <= 0:
                continue
            tt = f", target {tgt} (supply->sale median {turn[sku]:.2f} h)" if sku in turn else ""
            out.append((sku, qty, round(avg), f"{n} requests in 24 h, avg {round(avg):,} (n {k}), est. cost {int(cost):,}{tt}"))
        out = out[:ML - min(trials, TL)] + trial[:TL]
    finally:
        db.close()
    _save(st)
    return out


def bought(skus, now=None):
    """make_plan planned these: the first request-based buy time is kept for the 24 h drop."""
    if not skus:
        return
    st = _state()
    now = now or time.time()
    for sku in skus:
        s = st.setdefault(sku, {})
        s.setdefault("first_buy", round(now))
        s["last_buy"] = round(now)
        if s.pop("trial_pending", None) and not s.get("trial"):    # 11/09 ~23:40: the trial clock starts
            s["trial"] = round(now)
    _save(st)
