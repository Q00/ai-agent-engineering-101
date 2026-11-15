"""Purchasing: what the shop buys in a restock window, inside the CEO's purchasing policy and the hard limits
(limits.py: STOCK_CAP, CASH_RESERVE, MAX_QTY, MAX_LINES). restock.py (the kit's restock hook) calls make_plan()."""
import json
import math
import os
import time
from datetime import datetime

import decisions
from depts import sofa as sofa_rules
from depts import top_high  # 11/11 ~20:10: over 1.5M holding limits (top_high.json)
import policy
import pricing
import sales_report
import strategy
from limits import FOOD_TO_END_FROM, MAX_LINES, MAX_QTY, ROUND_END  # noqa: F401
from market_db import ME, connect, load_stats, price_of
from sales_report import HIGH_MAX, HIGH_MIN


def log(msg):
    print(f"[restock] {msg}", flush=True)


COST_SHARE = {"goods": 0.60, "food": 0.50}   # expected cost / list price, averaged over the grade draw
MIN_MARGIN = 0.10      # market average must beat the expected cost by 10%
MIN_SOLD_30M = 3       # market-wide units sold in 30 min to count as "selling"
MY_SHARE = 0.25        # share of the market's sales I plan stock for, until the next window (20 min)
SPEND_SHARE = 0.4      # at most this share of the spendable balance per run
CUSHION = strategy.CASH_RESERVE    # 1.5M: restock spends only (cash - CUSHION); under it the case is cash_low
FOOD_MIN_SHELF_H = 24  # food must last at least this long on the shelf
MAX_BREAK = 0.15       # skip goods that break this often after a sale
WANTED_QTY = 3         # units of a SKU buyers asked for on the forum, even when it is not selling fast yet
TRIAL_MARKET_30M, TRIAL_QTY = 3, 2   # '부패주의' with none held and none of my sales: trial buy when the market sold >= 3
MAX_STOCK_COST = strategy.STOCK_CAP  # 4.0M (limits.py): at or over it the case is overstock, no restock


def spend_share():
    return policy.get("purchasing", "spend_share", SPEND_SHARE)


C_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "restock_c.json")


def c_rules():
    """Bundle 2 restock rules (person's decision 11/07 ~02:00, on from 11/07 10:00 if the volume-pricing guard
    passed; written by tools/bundle2.py). {} when off. Read every run, no restart:
      breadth_cats  categories where the market sells a lot and my share is low: SKUs I do not hold first,
                    1-2 units each, at most BREADTH_MAX held per SKU in these categories
      surge_min     SKUs whose supply surge factor is >= this go to the end of the buy order
      max_first_run the first plan of each restock window spends all cash above the reserve (restock.py)"""
    try:
        with open(os.environ.get("RESTOCK_C_PATH") or C_RULES, encoding="utf-8") as f:   # env: tests only
            r = json.load(f)
        return r if r.get("enabled") else {}
    except (OSError, ValueError):
        return {}


BREADTH_MAX = 2
BREADTH_LINES = 5          # 11/07: at most this many breadth lines per run (restock_c.json breadth_max_lines)


GUARD_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guard_rules.json")
_surge_live = {}


def live_surge(inv, now=None):
    """{sku: factor} from GET /api/supply-prices, cached 60 s; inventory.json's surge if the call fails."""
    now = now or time.time()
    if now - _surge_live.get("ts", 0) > 60:
        try:
            import httpx
            from market_db import MARKET
            j = httpx.get(f"{MARKET}/api/supply-prices", timeout=5).json()
            _surge_live.update(ts=now, f={x["sku"]: x.get("factor", 1.0) for x in (j.get("surge") or [])}, src="live")
        except Exception:
            _surge_live.update(ts=now, f={x["sku"]: x.get("factor", 1.0) for x in (inv.get("surge") or [])
                                          if isinstance(x, dict)}, src="inventory")
    return _surge_live.get("f") or {}


def guard_check(sku, c, avg, inv):
    """High-price buy guard (person's decision 11/07 ~18:30, guard_rules.json): the market draws a unit's cost when it
    arrives (goods A 55-75%, B 40-60%, C 25-45% of list; food similar), so the guard uses the expected cost: list x
    share_goods (0.59) or share_food (0.5) x the live surge factor. The expected margin (market average / expected cost
    - 1) under min_margin (5%) -> a reason string (skip); else None. SKUs without a market average never get here
    (make_plan skips them before), so their estimate is unchanged."""
    try:
        r = json.load(open(GUARD_RULES, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not r.get("enabled") or not avg or not c.get("list_price"):
        return None
    share = r.get("share_food", 0.5) if c.get("kind") == "food" else r.get("share_goods", 0.59)
    f = live_surge(inv).get(sku, 1.0)
    est = c["list_price"] * share * f
    m = avg / est - 1
    if m < r.get("min_margin", 0.05):
        return (f"expected cost {int(est):,} (list {c['list_price']:,} x {share} x surge {f} [{_surge_live.get('src')}]), "
                f"market average {int(avg):,}, expected margin {m:.1%} < {r.get('min_margin', 0.05):.0%}")
    return None


BUY_EXCLUDE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "buy_exclude.json")


def _hce():
    """high_cost_exclude SKUs (the last list, logs/high_cost_exclude.json); empty on failure."""
    try:
        from depts import high_cost_exclude
        return json.load(open(high_cost_exclude.OUT, encoding="utf-8")).get("skus") or {}
    except Exception:
        return {}


def excluded():
    """{sku: why} never bought (buy_exclude.json, read every run; person's decision 11/09 ~11:10: G5e3784e6)."""
    try:
        with open(BUY_EXCLUDE, encoding="utf-8") as f:
            return json.load(f).get("skus") or {}
    except (OSError, ValueError):
        return {}


def make_plan(inv, units, catalog, budget, log, record=True):
    """The buy lines for this budget (unchanged rules; moved out of main so cap_record can run it without the cap)."""
    held, held_high = {}, 0
    stats0 = load_stats()
    try:                                          # 11/09 ~14:50: over-cost high units are out of the high_max count
        from depts import overcost
        oc = overcost.mark(units, lambda k: price_of(k, stats0)[0], log=log)
    except Exception as e:
        log(f"[overcost] failed: {type(e).__name__}: {e}")
        oc = set()
    from depts import deep_high                   # 11/10 ~12:30: fast high-price SKUs held up to 2-7 (else 1)
    deep = deep_high.current(log=log)
    free = deep_high.outside_high_max(deep)       # 11/13 ~15:40 (person): deep SKUs out of the high_max count / room
    n_free = 0
    for u in units:
        if u.get("grade") != "expired":
            held[u["sku"]] = held.get(u["sku"], 0) + 1
            hi = u.get("cost", 0) > HIGH_MIN and u.get("unit") not in oc
            n_free += hi and u["sku"] in free
            held_high += hi and u["sku"] not in free
    if oc:
        log(f"[overcost] {len(oc)} high-price units with cost above the market average: not counted in high_max")
    if free:
        log(f"[deep high] {len(free)} deep SKUs ({n_free} high units held) out of high_max: filled to their deep limit")
    from depts import wide_trial                  # 11/12 ~14:20 (person): B / C on, off by its guard (wide_trial.json)
    try:
        wide_trial.guard(units, log=log)
        wide_trial.re_on(units, log=log)            # 11/14 ~16:10 (person): on again after 6 h under 45M
    except Exception as e:
        log(f"[wide trial] guard failed: {type(e).__name__}: {e}")
    surge = {s["sku"]: s.get("factor", 1.0) for s in (inv.get("surge") or []) if isinstance(s, dict)}
    (MIN_MARGIN, MY_SHARE, WANTED_QTY, MIN_SOLD_30M, FOOD_MIN_SHELF_H, MAX_BREAK, TRIAL_MARKET_30M,
     TRIAL_QTY) = policy.values("purchasing", "min_margin", "my_share", "wanted_qty", "min_sold_30m",
                                "food_min_shelf_h", "max_break", "perishable_trial_market_30m", "perishable_trial_qty")
    stats = load_stats()
    pol = sales_report.policy()                   # the daily low/high split, until the CEO's policy sets it
    for k in ("high_share", "high_max"):
        v = (policy.load().get("purchasing") or {}).get(k)
        if v is not None:
            pol[k] = policy.get("purchasing", k)
    left = {"high": budget * pol["high_share"], "low": budget * (1 - pol["high_share"])}
    high_room = max(0, pol["high_max"] - held_high)
    hot_cats = busy_categories(catalog)
    log(f"budget {int(budget):,}: low {int(left['low']):,} / high {int(left['high']):,} "
        f"({pol['high_share']:.0%}), high-price units {held_high}/{pol['high_max']}, busy categories {hot_cats}")

    wanted_cats = set(stats.get("wanted_cats", []))
    wanted = set(stats.get("wanted", [])) | {k for k in stats["skus"]
                                            if (catalog.get(k) or {}).get("category", "").split(">")[-1] in wanted_cats}
    hot = list(stats.get("hot", []))
    rest = sorted(set(stats["skus"]) - set(hot), key=lambda k: -stats["skus"][k]["sold_30m"])
    plan, line_of = [], {}                       # line_of: sku -> (index in plan, the list it came from)
    order = [(k, "hot") for k in hot] + [(k, "wanted") for k in sorted(wanted - set(hot))] + [(k, "rest") for k in rest]
    C = c_rules()
    n_breadth = 0
    leaf_of = lambda k: ((catalog.get(k) or {}).get("category") or "").split(">")[-1]
    breadth = set(C.get("breadth_cats") or [])
    if breadth:                                  # bundle 2 (13): SKUs I do not hold in the low-share categories first
        new = sorted((k for k in stats["skus"] if leaf_of(k) in breadth and not held.get(k)
                      and stats["skus"][k].get("sold_30m", 0) >= 1), key=lambda k: -stats["skus"][k]["sold_30m"])
        first = set(new)
        order = [(k, "breadth") for k in new] + [o for o in order if o[0] not in first]
        log(f"[breadth] {len(new)} SKUs I do not hold in {sorted(breadth)} go first, at most {BREADTH_MAX} per SKU")
    if C.get("surge_min"):                       # bundle 2 (15): surging SKUs last
        late = [o for o in order if surge.get(o[0], 1.0) >= C["surge_min"]]
        if late:
            order = [o for o in order if surge.get(o[0], 1.0) < C["surge_min"]] + late
            log(f"[surge] {len(late)} SKUs with surge >= {C['surge_min']} moved to the end")
    try:                                         # 11/08 ~13:45: request-based candidates (depts/request_buy.py)
        from depts import request_buy
        reqc = request_buy.candidates(catalog, held, surge, log=log)
    except Exception as e:
        log(f"[request] failed: {type(e).__name__}: {e}")
        reqc = []
    for sku, qty, ravg, why in reqc:
        c = catalog.get(sku) or {}
        if sku in excluded():
            continue
        cost = c["list_price"] * COST_SHARE.get(c.get("kind", "goods"), 0.6) * surge.get(sku, 1.0)
        qty = min(qty, int(left["low"] // cost))
        if qty <= 0:
            log(f"[low] {(c.get('name') or sku)[:40]}: request-based ({why}), low budget left {int(left['low']):,} -> skip")
            continue
        left["low"] -= qty * cost
        line_of[sku] = (len(plan), "request")
        plan.append({"sku": sku, "qty": qty})
        log(f"[low] {(c.get('name') or sku)[:40]}: request-based ({why}), held {held.get(sku, 0)} -> buy {qty}")
    if reqc:
        request_buy.bought([s for s, _, _, _ in reqc if s in line_of])
    if record:                                   # 11/15 ~14:20 (person): 6 request lines, guard 10M + 6 h report
        from depts import request_six
        request_six.check(units, log=log)
    try:                                         # 11/08 ~20:35: hot SKUs that turn in <= 1 h get my_share 0.8
        from depts import hot_fast
        fast_share = hot_fast.shares(hot, MY_SHARE, log=log)
    except Exception as e:
        log(f"[hot fast] failed: {type(e).__name__}: {e}")
        fast_share = {}
    base_share, c_extra, b_skus = dict(fast_share), {}, set()
    try:                                         # 11/12 ~14:20 (person) C: normal goods on it 0.8 -> 1.2 (wide_trial.json)
        fast_share = wide_trial.c_shares(fast_share, lambda k: (catalog.get(k) or {}).get("list_price", 0) * COST_SHARE.get(
            (catalog.get(k) or {}).get("kind", "goods"), 0.6) * surge.get(k, 1.0) > HIGH_MIN)
    except Exception as e:
        log(f"[wide trial] C failed: {type(e).__name__}: {e}")
    no_buy = excluded()
    from depts import high_cost_exclude           # 11/09 ~23:40: our 48 h cost > 95% of the market average
    no_buy = dict(no_buy, **{k: 'high_cost_exclude' for k in high_cost_exclude.current(log=log)})
    actual =top_high.last_costs()                # 11/12 ~09:50: "over 1.5M" on max(estimate, last actual supply cost)
    over_max = {}                                 # 11/14 ~02:30: SKUs out by HIGH_MAX on the actual cost (logged once)
    from depts import line_rank                   # 11/09 ~14:35: lines after the request lines by expected profit
    rank = line_rank.on()
    if rank:
        k0, real_left, real_room = len(plan), dict(left), high_room
        left, high_room = {"high": 1e15, "low": 1e15}, 10 ** 6
        m24, cands = line_rank.market_24h(), {}
    band = []                                    # high SKUs with an expected margin of 8-10% (high_margin.json)
    for sku, src in order:                       # a wanted SKU is also in rest: one line per SKU (see [dedup])
        s = stats["skus"].get(sku)
        c = catalog.get(sku) or {}
        if not s or not c.get("list_price") or sku in no_buy:
            continue
        forum = sku in wanted
        avg, n = price_of(sku, stats)
        if avg is None or stats.get("sku_break", {}).get(sku, 0) > MAX_BREAK:
            continue
        kind = c.get("kind", "goods")
        if kind == "food" and (c.get("shelf_hours") or 0) < FOOD_MIN_SHELF_H:
            continue
        if kind == "food" and time.time() >= FOOD_TO_END_FROM                 and time.time() + (c.get("shelf_hours") or 0) * 3600 < ROUND_END:
            continue                              # 11/15 23:59 on: food that would expire before the round ends
        cost = c["list_price"] * COST_SHARE.get(kind, 0.6) * surge.get(sku, 1.0)
        # 11/11 ~14:50 (person): high price (cost > HIGH_MIN) needs 8% (high_margin.json), the rest MIN_MARGIN (10%)
        mm = high_margin(MIN_MARGIN) if cost > HIGH_MIN else MIN_MARGIN
        if cost > HIGH_MIN:                       # 11/12 ~14:20 (person) B: 6% for a SKU my shop has sold
            mm0, mm = mm, wide_trial.b_margin(sku, mm)
        if cost <= HIGH_MAX < top_high.judge_cost(sku, cost, actual):   # 11/14 ~02:30 (person): HIGH_MAX on
            over_max[sku] = int(top_high.judge_cost(sku, cost, actual))   # max(estimate, last actual supply cost),
            continue                                                       # deep SKUs too
        if cost > HIGH_MAX or avg < cost * (1 + mm):
            continue
        if cost > HIGH_MIN and avg < cost * (1 + mm0):
            b_skus.add(sku)
        if sofa_rules.on() and sofa_rules.is_sofa(sku) and kind != "food" and cost >= sofa_rules.MIN_COST \
                and not sofa_rules.buy_ok(sku, cost)[0]:   # sofa buying rule E
            continue
        wide = leaf_of(sku) in breadth
        if cost > HIGH_MIN:                       # high price: a busy category, one unit, within the cap
            hold_max = deep_high.limit(sku, deep)  # 11/10 ~12:30: 1, or 2-5 for a fast-selling SKU
            if top_high.is_top(top_high.judge_cost(sku, cost, actual)):   # 11/11 ~20:10: over 1.5M per-SKU cap
                hold_max = min(hold_max, top_high.per_sku(deep=sku in deep))   # 11/13 ~16:05: deep 5
            if (leaf_of(sku) not in hot_cats and not wide) or s["sold_30m"] < 1 or held.get(sku, 0) >= hold_max:
                continue
            try:                                  # 11/08 ~17:00: expected first ask above the market average
                from depts import high_ask
                ok, eask, efl = high_ask.buy_ok(sku, c, cost, avg, strategy.current()[0])
            except Exception as e:
                log(f"[high] {sku}: first-ask check failed ({type(e).__name__}: {e}) -> skip")
                continue
            if not ok:
                if high_room > 0:
                    log(f"[high] {s['name'] or sku}: expected first ask {eask:,} (floor {efl:,}) > avg {int(avg):,} -> skip")
                    decisions.record("purchasing", "high_price_above_avg", sku, [eask, efl, int(avg), int(cost)],
                                     f"expected first ask {eask:,} (floor {efl:,}, est. cost {int(cost):,}) > market avg "
                                     f"{int(avg):,}: not bought")
                continue
            g = guard_check(sku, c, avg, inv)
            if g:                                 # 11/07 ~18:30: expected margin under the guard's minimum
                if high_room > 0:
                    log(f"[guard] {s['name'] or sku}: {g} -> skip")
                continue
            tier, qty = "high", hold_max - held.get(sku, 0)
            if sku not in free:                   # 11/13 ~15:40: a deep SKU is not limited by high_max
                qty = min(qty, high_room)
            if avg < cost * (1 + MIN_MARGIN):     # 11/11 ~14:50: in the 8-10% band only thanks to high_margin.json
                band.append(sku)
        elif src == "breadth":                    # bundle 2 (13): a SKU I do not hold, 1-2 units
            if n_breadth >= int(C.get("breadth_max_lines", BREADTH_LINES)):
                continue
            tier, qty = "low", 1 if s["sold_30m"] < MIN_SOLD_30M else BREADTH_MAX
        else:                                     # low price: as before, hot or wanted on the forum
            if sku not in hot and not forum or (s["sold_30m"] < MIN_SOLD_30M and not forum):
                continue
            target = max(math.ceil(s["sold_30m"] * (20 / 30) * fast_share.get(sku, MY_SHARE)), WANTED_QTY if forum else 0)
            if fast_share.get(sku) != base_share.get(sku):   # C: the units over the 0.8 target
                c_extra[sku] = target - max(math.ceil(s["sold_30m"] * (20 / 30) * base_share.get(sku, MY_SHARE)),
                                            WANTED_QTY if forum else 0)
            tier, qty = "low", min(MAX_QTY, target - held.get(sku, 0))
        if wide and tier == "low":                # bundle 2 (13): spread, at most BREADTH_MAX held per SKU
            qty = min(qty, BREADTH_MAX - held.get(sku, 0))
        qty = min(qty, int(left[tier] // cost))
        rot = ""
        if sales_report.perishable(c):            # '부패주의': at most what my shop sold of it in the last 30 min
            mine, planned = my_sold_30m(sku), qty
            if mine == 0 and held.get(sku, 0) == 0 and s["sold_30m"] >= TRIAL_MARKET_30M:
                rule, qty = "trial", min(TRIAL_QTY, int(left[tier] // cost))   # none held, the market sells it
            else:
                rule, qty = "my sales", min(qty, mine)
            rot = (f", perishable rule '{rule}' (my sales 30m {mine}, held {held.get(sku, 0)}, "
                   f"market 30m {s['sold_30m']}, planned {planned} -> {max(qty, 0)})")
            if qty <= 0:
                log(f"[{tier}] {s['name'] or sku}: est. cost {int(cost)}{rot} -> skip")
        if qty <= 0:
            continue
        if rank:                                  # collect; the ranking below decides the lines and the qty
            c0 = cands.get(sku)
            if c0 is None or qty > c0["qty"]:
                sc = line_rank.score(avg, cost, qty, m24.get(sku, 0), fast_share.get(sku, MY_SHARE))
                cands[sku] = {"sku": sku, "qty": qty, "tier": tier, "cost": cost, "score": sc, "src": src,
                              "free": sku in free}
            continue
        if sku in line_of:                        # second time this run: keep one line, the larger qty
            i, first = line_of[sku]
            q1 = plan[i]["qty"]
            q = max(q1, qty)
            log(f"[dedup] {sku} {first} {q1} / {src} {qty} -> {q}")
            left[tier] -= (q - q1) * cost
            if tier == "high" and sku not in free:
                high_room -= q - q1
            plan[i]["qty"] = q
            continue
        line_of[sku] = (len(plan), src)
        n_breadth += src == "breadth"
        left[tier] -= qty * cost
        if tier == "high" and sku not in free:
            high_room -= qty
        plan.append({"sku": sku, "qty": qty})
        log(f"[{tier}] {s['name'] or sku}: sold {s['sold_5m']}/5m {s['sold_30m']}/30m, avg {avg}, est. cost {int(cost)}, "
            f"held {held.get(sku, 0)}{' (wanted on forum)' if forum else ''}{rot} -> buy {qty}")
        if len(plan) == MAX_LINES:
            break
    if over_max:
        log(f"[high max] {len(over_max)} SKUs out: last actual supply cost > HIGH_MAX {HIGH_MAX:,} (estimate under it): "
            + ", ".join(f"{k} {v:,}" for k, v in over_max.items()))
    est = lambda k: (catalog.get(k) or {}).get("list_price", 0) * COST_SHARE.get((catalog.get(k) or {}).get("kind", "goods"),
                                                                                  0.6) * surge.get(k, 1.0)
    from depts import normal_min                  # 11/11 ~20:50: at least 4 normal lines a run (normal_min.json)
    import limits as LM
    nm = normal_min.active(units, log=log)
    is_low = (lambda k: est(k) <= nm["max_cost"]) if nm else None
    runs = max(1, MAX_LINES // LM.MAX_LINES)      # 2 for a window's first plan (both halves)
    if rank:
        req = {p["sku"] for p in plan[:k0]}
        left, high_room = real_left, real_room
        min_low = max(0, nm["min_lines"] * runs - sum(1 for p in plan[:k0] if is_low(p["sku"]))) if nm else 0
        plan += line_rank.pick([c for c in cands.values() if c["sku"] not in req], left, high_room, MAX_LINES - k0, log=log,
                               min_low=min_low, is_low=is_low)
    if band:
        note_band([p for p in plan if p["sku"] in band], log)
    try:
        sofa = sofa_trial(inv, units, catalog, stats, held, surge, plan, log)
    except Exception as e:                         # the trial must never cost the run its plan
        log(f"[sofa trial] failed: {type(e).__name__}: {e}")
        sofa = None
    if sofa:                                       # 11/09: a list (1, or 2 with sofa x2)
        sk = {x["sku"] for x in sofa}
        plan = sofa + [p for p in plan if p["sku"] not in sk][:MAX_LINES - len(sofa)]
    # 11/11 ~20:10 (person): over 1.5M at most 10 units held at once (stock + this plan, both halves of a window plan)
    plan = top_high.cap_plan(plan, units, est, log=log, actual=actual, deep=set(deep))
    if nm:                                         # 11/11 ~20:50: >= 4 normal lines in each run-sized chunk, first
        plan = normal_min.arrange(plan, is_low, LM.MAX_LINES, nm["min_lines"])
        log(f"[normal min] normal lines per run: " + ", ".join(
            str(sum(1 for p in plan[i:i + LM.MAX_LINES] if is_low(p["sku"]))) for i in range(0, len(plan), LM.MAX_LINES)))
    if record:                                    # not for cap_record's plan without the cap
        wide_trial.note([{"opt": "B", "sku": p["sku"], "qty": p["qty"]} for p in plan if p["sku"] in b_skus]
                        + [{"opt": "C", "sku": p["sku"], "qty": min(p["qty"], c_extra[p["sku"]])} for p in plan
                           if c_extra.get(p["sku"], 0) > 0], log)
    return plan


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIGH_MARGIN = os.path.join(HERE, "high_margin.json")
BAND_LOG = os.path.join(HERE, "logs", "high_margin_band.jsonl")


def high_margin(default):
    """11/11 ~14:50 (person): the high-price buying margin filter (high_margin.json min_margin while enabled)."""
    try:
        with open(HIGH_MARGIN, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return default
    return float(r.get("min_margin", default)) if r.get("enabled") else default


def note_band(lines, log=print):
    """Planned high lines with an expected margin of 8-10% (bought only because of the 8% filter): one row each."""
    try:
        with open(BAND_LOG, "a", encoding="utf-8") as f:
            for p in lines:
                f.write(json.dumps({"ts": round(time.time()), "sku": p["sku"], "qty": p["qty"]}) + "\n")
        log(f"[high margin] {len(lines)} lines in the 8-10% band: {', '.join(p['sku'] for p in lines)}")
    except OSError:
        pass


SOFA_CAT = "소파/의자/스툴"
SOFA_MAX_COST = 1_000_000
WINDOW_S = 1200
SOFA_UNSOLD_MAX = 10                               # trial sofas held (bought since the trial start) -> no more buys


def sofa_trial(inv, units, catalog, stats, held, surge, plan, log):
    """Sofa trial (person's decision 11/06, option C): while policy purchasing.sofa_trial_start <= now <
    sofa_trial_end, one unit of the sofa/chair/stool category per 20-min restock window, on top of the high-price
    budget, est. cost <= SOFA_MAX_COST, the usual margin and break filters; never past STOCK_CAP or into
    CASH_RESERVE (limits.py). The SKU sold most in the last 30 min that I do not hold. None otherwise."""
    from limits import CASH_RESERVE, STOCK_CAP
    now = time.time()
    t0, t1 = policy.get("purchasing", "sofa_trial_start"), policy.get("purchasing", "sofa_trial_end")
    if not (t0 and t0 <= now < t1):
        return None
    leaf = lambda k: ((catalog.get(k) or {}).get("category") or "").split(">")[-1]
    per_win, unsold_max, n_lines, _ = sofa_rules.limits_now()     # 11/09 ~14:35: x2 -> 2 / 20 / 2 / 1.0M
    unsold = [u for u in units if leaf(u["sku"]) == SOFA_CAT and (u.get("supplied_at") or 0) >= t0
              and u.get("kind") != "food" and (u.get("cost") or 0) >= sofa_rules.MIN_COST]
    if len(unsold) >= unsold_max:                   # person's decision 11/07: stop adding while trial sofas pile up
        log(f"[sofa trial] skip: {len(unsold)} trial sofas unsold (>= {unsold_max}), no more buying")
        return None
    recent = [u for u in units if leaf(u["sku"]) == SOFA_CAT and now - (u.get("supplied_at") or 0) < WINDOW_S]
    if len(recent) >= per_win:
        log(f"[sofa trial] skip: bought {len(recent)} this window ({recent[0]['name'][:30]}; at most {per_win})")
        return None
    room = min(n_lines, per_win - len(recent), unsold_max - len(unsold))
    held_cost = sum(u.get("cost", 0) for u in units if u.get("grade") != "expired")
    cash = inv.get("balance", 0)
    min_margin, max_break = policy.values("purchasing", "min_margin", "max_break")
    found = []
    from depts import deep_high                     # 11/10 ~12:30: a fast-selling sofa SKU held up to 2-5 (else 1)
    deep = deep_high.current(write=False)
    actual = top_high.last_costs()                  # 11/14 ~02:30: HIGH_MAX on max(estimate, last actual supply cost)
    for sku, s in stats["skus"].items():
        c = catalog.get(sku) or {}
        if leaf(sku) != SOFA_CAT or not c.get("list_price") or held.get(sku, 0) >= deep_high.limit(sku, deep) \
                or s.get("sold_30m", 0) < 1 or sku in excluded() or sku in _hce() \
                or c.get("kind") == "food" or c["list_price"] * COST_SHARE["goods"] < sofa_rules.MIN_COST:
            continue
        avg, n = price_of(sku, stats)
        if avg is None or stats.get("sku_break", {}).get(sku, 0) > max_break:
            continue
        cost = c["list_price"] * COST_SHARE["goods"] * surge.get(sku, 1.0)
        if cost > max(SOFA_MAX_COST, sofa_rules.limits_now()[3]) or avg < cost * (1 + min_margin):   # 11/09 ~21:20: x3 1.5M
            continue
        if top_high.judge_cost(sku, cost, actual) > HIGH_MAX:   # 11/14 ~02:30 (person): HIGH_MAX on max(estimate, last
            log(f"[sofa trial] {sku}: last actual supply cost {int(actual.get(sku) or 0):,} > HIGH_MAX {HIGH_MAX:,} -> skip")
            continue                                             # actual supply cost), deep SKUs too
        if sofa_rules.on() and not sofa_rules.buy_ok(sku, cost)[0]:   # E: >= 30 market sales/48 h, >= 2/h, cost <= 500k
            continue
        found.append((sku, cost, s["sold_30m"], avg, s.get("name") or c.get("name")))
    found.sort(key=lambda b: -b[2])
    if not found:
        log(f"[sofa trial] skip: no sofa SKU fits (cost <= {max(SOFA_MAX_COST, sofa_rules.limits_now()[3]):,}, margin, selling, not held; rule E: >= 30 sales/48 h, "
            f">= 2/h, cost <= {sofa_rules.limits_now()[3]:,})")
        return None
    out = []
    units_left = min(per_win - len(recent), unsold_max - len(unsold))   # the sofa unit limits stay
    for sku, cost, sold, avg, name in found[:room]:
        import limits
        q = 0
        while q < deep_high.limit(sku, deep) - held.get(sku, 0) and q < units_left:
            if held_cost + cost > limits.stock_cap_now(cash, held_cost)[0] or cash - cost < CASH_RESERVE:
                log(f"[sofa trial] skip {name[:30]}: est. cost {int(cost):,} would pass STOCK_CAP or the cash reserve")
                break
            held_cost, cash, q = held_cost + cost, cash - cost, q + 1
        if not q:
            break
        units_left -= q
        log(f"[sofa trial] {name[:40]} ({sku}): sold {sold}/30m, avg {avg}, est. cost {int(cost):,}, held "
            f"{held.get(sku, 0)} (limit {deep_high.limit(sku, deep)}) -> buy {q} "
            f"(trial {datetime.fromtimestamp(t0):%m-%d %H:%M} - {datetime.fromtimestamp(t1):%m-%d %H:%M})")
        out.append({"sku": sku, "qty": q})
        if units_left <= 0:
            break
    return out or None


def cap_record(inv, units, catalog, budget, plan, case):
    """Log only, buying is not changed: when STOCK_CAP blocked or cut this run, the budget and the buy lines
    without the cap (same rules, quiet), into the log ([cap]) and market.db cap_runs (for the daily report)."""
    cash, stock = int(inv.get("balance", 0)), int(pricing.held_cost(inv))
    free = (cash - CUSHION) * spend_share()                 # the budget without the cap
    if free <= 0 or free <= budget + 1:
        return                                              # the cap did not bind: cash did, or nothing
    try:
        free_plan = make_plan(inv, units, catalog, free, lambda m: None, record=False)
    except Exception as e:
        log(f"[cap] plan without the cap failed: {e}")
        free_plan = []
    names = {k: (catalog.get(k) or {}).get("name") or k for k in [p["sku"] for p in free_plan]}
    mine, free_qty = {}, {}                                 # a SKU can sit on two lines (wanted + rest): add up
    for p in plan:
        mine[p["sku"]] = mine.get(p["sku"], 0) + p["qty"]
    for p in free_plan:
        free_qty[p["sku"]] = free_qty.get(p["sku"], 0) + p["qty"]
    extra = [(k, q - mine.get(k, 0)) for k, q in free_qty.items() if q > mine.get(k, 0)]
    if extra:                                               # person's decision 11/07 ~14:45: Telegram when the cap
        try:                                                # starts cutting buying (at most once per 6 h)
            import alerts
            cut = int(sum(q * ((catalog.get(k) or {}).get("list_price") or 0) * COST_SHARE.get(
                (catalog.get(k) or {}).get("kind", "goods"), 0.6) for k, q in extra))
            alerts.send("info", "stock_cap_binding",
                        f"STOCK_CAP {__import__('limits').stock_cap_now(cash, stock)[0]:,}에 매입이 막히기 시작: 재고 원가 {stock:,}, 현금 {cash:,}, "
                        f"이번 run 예산 {int(max(budget, 0)):,} (상한 없으면 {int(free):,}), 상한 때문에 못 산 "
                        f"{len(extra)}개 SKU 약 {cut:,}원. STOCK_CAP은 그대로(사람 결정 대기)", log=log,
                        repeat_s=6 * 3600)
        except Exception as e:
            log(f"[cap] alert failed: {type(e).__name__}: {e}")
    log(f"[cap] case {case}, cash {cash:,}, stock at cost {stock:,}: budget without the cap {int(free):,} vs "
        f"actual {int(max(budget, 0)):,}; without the cap would also buy: "
        + (", ".join(f"{names[k][:24]} x{q}" for k, q in extra) or "nothing more"))
    decisions.record("purchasing", "cap", case, {"budget_free": int(free), "budget": int(max(budget, 0)),
                                                 "extra": dict(extra)},
                     f"cash {cash:,}, stock at cost {stock:,}", limit_hit="STOCK_CAP")
    db = connect()
    try:
        db.execute("CREATE TABLE IF NOT EXISTS cap_runs (ts REAL PRIMARY KEY, shop_case TEXT, cash INTEGER, "
                   "stock_cost INTEGER, budget_free INTEGER, budget INTEGER, plan_free TEXT, plan TEXT)")
        db.execute("INSERT OR REPLACE INTO cap_runs VALUES (?,?,?,?,?,?,?,?)",
                   (time.time(), case, cash, stock, int(free), int(max(budget, 0)),
                    json.dumps(free_plan), json.dumps(plan)))
        db.commit()
    finally:
        db.close()


def my_sold_30m(sku):
    """Units of this SKU my shop sold in the last 30 minutes (settles, refreshed every 5 min)."""
    db = connect()
    try:
        return db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND sku=? AND ts>=?",
                          (ME, sku, time.time() - 1800)).fetchone()[0]
    finally:
        db.close()


def busy_categories(catalog, top=5):
    """The leaf categories with the most settles in the last 2 hours, market-wide."""
    db = connect()
    try:
        rows = db.execute("SELECT sku, COUNT(*) FROM settles WHERE ts>=? GROUP BY sku", (time.time() - 7200,)).fetchall()
    finally:
        db.close()
    cats = {}
    for sku, n in rows:
        leaf = ((catalog.get(sku) or {}).get("category") or "").split(">")[-1]
        if leaf:
            cats[leaf] = cats.get(leaf, 0) + n
    return sorted(cats, key=cats.get, reverse=True)[:top]
