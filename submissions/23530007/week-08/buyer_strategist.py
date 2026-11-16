"""Buyer strategist: the buyer's own analyst, beside strategist.py (the shop's) (user, 11-06).

    python buyer_strategist.py [--force] [--dry-prompt]   (watch_round.sh runs it every 15 min; it works in its slots)

The buyer scores .5 x pct(saved) + .3 forum + .1 x pct(deals) + .1 x pct(posts) among the students; saved is the
patch-3.6 v = min(1, (list - price)/(list - unit cost)) per deal over (deals + 100). The loop:
  memory    buyer_memory.json (every visit: shop, SKU, kind, asks, outcome, price), every deal scored with
            buyer_score.score (3.6 scale; 11-13: it was 1 - price/list, so the agent read .37 next to a board .73)
  end game  code owns v_breakeven from T-12 h and slot_value from T-6 h (endgame_keys); from T-3 h it only reports
  learn     each move names a metric and a 1-3 h window; code measures it against the same window before the move and
            writes helped / hurt / unclear (2 standard errors); only a harm guard reverts (11-13: 5 reverts on
            old-scale predictions in one day, none of them harm)
  evidence  race and targets (market_picture.race_view), the funnel over 1 h and 3 h, score, buy health, shops,
            peers, knobs with bounds, the move ledger, facts with an expiry, curator answers, notes
  decide    MODEL moves whitelisted knobs (GLOBAL, per shop SHOP); price floors .01 per run, the rest anywhere in bounds
  act       params.json strategist.mode "apply" writes params_auto_buyer.json under a lock (write_auto)
Log: logs/buyer_strategist.log.
"""
import argparse
import fcntl
import json
import math
import os
import sys
from collections import Counter
import time
from pathlib import Path

import market_probe as probe
import model_env
import strategist as shop
from harness import last_json_object
from strategist_memory import Memory, operator_note, my_turn, run_gate, post_board, read_board, read_digest, valid_requests

HERE = Path(__file__).resolve().parent
BUYER_WAKE = HERE / ".market" / "buyer_strategist_wake"   # 11-11: written by guards.py; honored at most hourly
AUTO = HERE / "params_auto_buyer.json"
AUTO_LOCK = HERE / ".market" / "params_auto_buyer.lock"  # 11-13: the guards and this file both write AUTO
STATE = Path(os.environ.get("BUYER_STRATEGIST_STATE", HERE / "buyer_strategist_memory.json"))
LOGF = HERE / "logs" / "buyer_strategist.log"
BMEM = HERE / "buyer_memory.json"
OPERATOR_NOTE = HERE / "operator_to_buyer.json"   # 11-08: the operator (with the user) asks; you answer (answer file)
OPERATOR_ANSWER = HERE / "buyer_answer_to_operator.json"
CATALOG = HERE / ".market" / "catalog.json"
LAST = HERE / "buyer_strategist_last.json"
END = 1794927540                                   # 2026-11-17 14:59 UTC (23:59 KST), the round end
EVERY = 3600 - 120
MAX_MOVES = 4                                      # 11-13: 4 (was 8); a ledger with fewer moves can be read
PROMPT_MAX = 35_000                                # 11-13: the prompt was 80-110 k chars; the evidence is cut to fit
GLOBAL = {                                    # role, bounds, step (11-12 patch 3.6 rebuild: the price policy is vprice.py)
    # price policy in v = (list - price)/(list - unit cost), the score of patch 3.6 (vprice.plan / vprice.move)
    "take_v": ((0.80, 0.98), 0.02),           # an ask that already scores this v is taken at once (the next order waits)
    "open_v": ((0.85, 1.0), 0.02),            # our first counter, in v (1.0 = the unit's cost itself)
    "step_v": ((0.01, 0.10), 0.01),           # each later counter concedes this much v
    "rounds": ((1, 8), 1),                    # counters before we take any ask at or under the cap (else walk); 11-15: 1-8, the A/B promoted 7 (.051 vs .030 per order)
    "soft_rounds": ((1, 4), 1),               # the same at shops that end talks on low offers (reject share >= 30%)
    "v_floor": ((0.70, 0.82), 0.01),          # 11-12: the price floor's base, the break-even v (not m*): floor = v_floor + delta
    "v_floor_rel": ((-0.05, 0.05), 0.01),     # 11-12 23:50: when set, the floor base is m* + this (follows saved live)
    "food_delta": ((-0.10, 0.10), 0.02),      # food floor = v_floor + this (the least a food deal may score)
    "goods_delta": ((0.0, 0.15), 0.02),       # goods floor = (m* + this) / (1 - break rate of the stated grade)
    "near_v": ((0.0, 0.08), 0.01),            # a receipt within this v under the floor is still paid
    "skip_delta_food": ((-0.15, 0.10), 0.02), # skip an order when the best shop's expected v < m* + this
    "skip_delta_goods": ((-0.20, 0.10), 0.02),
    "target_mean": ((0.60, 0.90), 0.02),      # m* fallback when buyer_score.json has none
    # shop choice and order flow
    "food_extra_hours": ((0.25, 12), 2),      # food must last the order's hours + this (a spoiled deal counts -1)
    # 11-12 slot economics: one order per 20 s per buyer; a visit is ranked by P(deal per visit, ours, 6 h, this kind)
    # x (expected v - v_breakeven) - slot_value x (seconds past the order's free 20 s)/20
    # 11-13: lower bound .73 (was .70): the end game sets .73 when a deals rank is in play; the rail keeps >= .745 before
    "v_breakeven": ((0.73, 0.80), 0.01),      # the v at which one more deal neither lifts nor lowers the final buyer score
    "slot_value": ((0.02, 0.15), 0.02),  # 11-16: >= .02 (0 let slow shops eat ~50 orders/h)       # one order's expected gain, charged per 20 s an order runs past its slot
    "dead_p": ((0.0, 0.15), 0.02),            # a shop closing under this per visit (>= 10 visits of the kind, 6 h) is skipped
    "hurry_secs": ((8, 30), 2),               # past this many seconds into an order, an ask under the cap is taken at once
    "max_shops": ((1, 6), 1),                 # shops tried per order
    "value_half_life_h": ((0, 6), 1),         # weight half-life of market deals in the shop values (0 = flat 6 h)
    "talk_share": ((0.0, 1.0), 0.25),         # share of orders that first ask the shop for its lowest price, then haggle
    "anchor_share": ((0.0, 1.0), 0.25),       # 11-12 23:40 A/B: share of orders whose first number is anchor_x of list
    # 11-13: per kind (vprice reads anchor_share_<kind> before anchor_share; both sat at 1.0 unseen by the agent)
    "anchor_share_food": ((0.0, 1.0), 0.25),
    "anchor_share_goods": ((0.0, 1.0), 0.25),
    "anchor_x": ((0.30, 0.60), 0.05),         # (SungJinho's buyer opens at ~.45 of list, under cost, and takes the counter)
}
# 11-13 (user: conduct): no routing penalty aimed at classmate shops; code writes 0 on every write
FORCED = {"rival_penalty": 0.0}
# 11-13: the price floors move at most .01 per run; every other knob may go anywhere in its bounds (the adaptive
# steps had shrunk to .005-.02 and the agent held 'a move I cannot score' while a target was off)
FLOOR_KNOBS = {"v_floor", "v_floor_rel", "food_delta", "goods_delta", "v_breakeven", "near_v"}
FLOOR_STEP = 0.01
INT_KNOBS = {"rounds", "soft_rounds", "max_shops", "hurry_secs"}
BREAKEVEN_RAIL = 0.745                        # 11-13: v_breakeven >= .745 until the end game owns it

SHOP = {                                      # per shop (user 11-06: the strategist steers the rule-based buyer)
    "shop_bias": ((-0.1, 0.1), 0.03),         # added to the shop's value score (prefer > 0, avoid < 0)
    "bias_food": ((-0.1, 0.1), 0.03),         # 11-11: same, for food orders only (route food to the .40+ shops)
    "bias_goods": ((-0.1, 0.1), 0.03),        # 11-11: same, for goods orders only (beds/sofas to the .30+ shops)
}
CAT = {}                                      # 11-12: category caps are gone (the cap is the price at floor_v)
# 11-13: measured on the 3.6 scale over a 1-3 h window against the same window before the move
METRICS = ("deals_h", "v_mean", "v_food", "v_goods", "saved_speed")
V_METRICS = {"v_mean", "v_food", "v_goods"}
# 11-13 harm guard (the only revert): v6 ran .848, so a window mean under .83 over 60+ deals is a real drop; deals/h
# under 60% of the window before the move (base >= 40/h) is a collapse
HARM_V, HARM_V_N, HARM_DEALS_BASE, HARM_DEALS_SHARE = 0.83, 60, 40, 0.6
CHURN_N, CHURN_H, CHURN_BLOCK_H = 3, 3, 2     # a knob moved >= 3 times in 3 h is not moved for 2 h
MAX_FACTS = 15


def saved_speed(rows, hours, base):
    """How fast the scored saved rate climbs (user 11-06: compare settings on this, not on saved per deal or
    deals alone): deals per hour x (mean saved per deal - our current saved). Divided by the denominator it is
    the saved rise per hour; the product alone ranks settings the same way. 3.6 scale since 11-13."""
    s = summary(rows)
    if not s["deals"] or s["mean_saved"] is None or not hours:
        return 0.0
    return round(s["deals"] / hours * (s["mean_saved"] - base), 2)


def log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {text}"
    print(line)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


_ROWS = {"k": None, "rows": []}


def _all_rows():
    """Every trade with its catalog list price and, for a signed deal, its patch-3.6 v (buyer_score.score: exact unit
    cost from the ledger where archived, 0 for a refunded break, -1 for an unrefunded one). Built once a minute per
    process (11-13: ~0.3 s; the curator calls trades() 30+ times a run)."""
    k = int(time.time() // 60)
    if _ROWS["k"] == k:
        return _ROWS["rows"]
    import buyer_score
    cat = shop.read(CATALOG, {})
    rows = []
    for t in shop.read(BMEM, {}).get("trades", []):
        rows.append(dict(t, list=(cat.get(t.get("item")) or {}).get("list_price")))
    try:
        buyer_score.costs(rows, time.time())
    except Exception as e:                    # the estimates stay (buyer_score.score falls back to cost_model)
        print(f"buyer_score.costs failed: {e!r}", file=sys.stderr)
    for r in rows:
        ok = r.get("outcome") == "deal" and r.get("signed") is not False and r.get("price") and r.get("list")
        r["saved"] = buyer_score.score(r) if ok else None
    _ROWS.update(k=k, rows=rows)
    return rows


def trades(since, until=None):
    """Our trades in [since, until], each with list (catalog) and saved (3.6 v for a deal, else None)."""
    return [dict(t) for t in _all_rows() if t.get("t", 0) >= since and not (until and t["t"] > until)]


def summary(rows):
    skipped = sum(str(r.get("outcome", "")).startswith("skipped") for r in rows)
    rows = [r for r in rows if not str(r.get("outcome", "")).startswith("skipped")]
    deals = [r for r in rows if r.get("outcome") == "deal" and r.get("signed") is not False]
    sv = [r["saved"] for r in deals if r["saved"] is not None]
    return {"visits": len(rows), "skipped_orders": skipped, "deals": len(deals), "conversion": round(len(deals) / len(rows), 2) if rows else None,
            "mean_saved": round(sum(sv) / len(sv), 3) if sv else None,
            "first_ask_over_list": round(sorted(r["asks"][0] / r["list"] for r in rows if r.get("asks") and r.get("list"))
                                         [len([1 for r in rows if r.get("asks") and r.get("list")]) // 2], 2)
            if any(r.get("asks") and r.get("list") for r in rows) else None}


def _is_deal(r):
    return r.get("outcome") == "deal" and r.get("signed") is not False


def _outcome_key(r):
    s = str(r.get("outcome"))
    if r.get("outcome") == "deal":
        return "deal (unsigned)"
    return ": ".join(s.split(": ")[:2])[:40]          # 'unpaid: not paying: 163.76 h ...' -> 'unpaid: not paying'


def funnel(now):
    """11-13: where our orders end, per hour over the last 1 h and 3 h, on the 3.6 scale: orders/h, deals/h and v by
    kind, the non-deal outcomes by kind, skips by reason, and orders that ran past their 20 s slot."""
    out = {}
    for h in (1, 3):
        rows = trades(now - h * 3600, now)
        orders = {}
        for r in rows:
            orders.setdefault(r.get("order"), []).append(r)
        deals = [r for r in rows if _is_deal(r)]
        dh, vm = {}, {}
        for kind in ("all", "food", "goods"):
            d = [r for r in deals if kind == "all" or r.get("kind") == kind]
            v = [r["saved"] for r in d if r.get("saved") is not None]
            dh[kind] = round(len(d) / h, 1)
            vm[kind] = round(sum(v) / len(v), 3) if v else None
        not_deal, skipped = {}, {}
        for r in rows:
            if _is_deal(r):
                continue
            kind = r.get("kind") or "?"
            if str(r.get("outcome", "")).startswith("skipped"):
                skipped.setdefault(kind, Counter())[str(r.get("outcome"))[:40]] += 1
            else:
                not_deal.setdefault(kind, Counter())[_outcome_key(r)] += 1
        per_h = lambda c: {k: round(n / h, 1) for k, n in c.most_common(10)}
        over = sum(1 for rs in orders.values() if max((r.get("elapsed") if r.get("elapsed") is not None else r.get("secs") or 0)
                                                       for r in rs) > 20)
        out[f"last_{h}h"] = {"orders_h": round(len(orders) / h, 1), "deals_h": dh, "v_mean": vm,
                             "non_deal_visits_per_h": {k: per_h(c) for k, c in not_deal.items()},
                             "skipped_per_h": {k: per_h(c) for k, c in skipped.items()},
                             "orders_over_20s_h": round(over / h, 1)}
    return out


# ---- learning (11-13) ---------------------------------------------------------------------------------------------
def window_stats(t0, t1, S):
    """Our signed deals in [t0, t1] with their 3.6 v, overall and by kind; S is the board saved at the move."""
    deals = [r for r in trades(t0, t1) if _is_deal(r)]
    v = lambda kind: [r["saved"] for r in deals if r.get("saved") is not None and (kind is None or r.get("kind") == kind)]
    return {"h": max(1e-9, (t1 - t0) / 3600), "n": len(deals), "v": v(None), "v_food": v("food"), "v_goods": v("goods"),
            "S": S or 0.0}


def metric_value(metric, st):
    """(value, n, variance of the value) of a metric over a window. Counts are Poisson: deals_h var = n/h^2, and
    saved_speed = sum(v - S)/h is a compound Poisson sum, var = sum((v - S)^2)/h^2 (the count case with v - S = 1)."""
    h = st["h"]
    if metric == "deals_h":
        return round(st["n"] / h, 2), st["n"], st["n"] / h ** 2
    if metric == "saved_speed":
        xs = [x - st["S"] for x in st["v"]]
        return round(sum(xs) / h, 3), len(xs), sum(x * x for x in xs) / h ** 2
    xs = st["v"] if metric == "v_mean" else st[metric]
    if not xs:
        return None, 0, None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1) / len(xs) if len(xs) > 1 else None
    return round(m, 4), len(xs), var


def judge(op, got, var_got, base, var_base):
    """helped / hurt when |got - base| > 2 standard errors in (or against) the predicted direction, else unclear."""
    if got is None or base is None or var_got is None or var_base is None:
        return "unclear"
    se = math.sqrt(var_got + var_base)
    d = got - base
    if abs(d) <= 2 * se:
        return "unclear"
    return "helped" if (d > 0) == (op == ">=") else "hurt"


def harm_of(e, st):
    """The harm guard for one move's window: a v drop over 60+ deals or a deals/h collapse against the window before."""
    v = st["v"]
    if st["n"] >= HARM_V_N and v and sum(v) / len(v) < HARM_V:
        return f"v_mean {sum(v) / len(v):.3f} < {HARM_V} over {st['n']} deals"
    base = e.get("base_deals_h") or 0
    if base >= HARM_DEALS_BASE and st["n"] / st["h"] < HARM_DEALS_SHARE * base:
        return f"deals/h {st['n'] / st['h']:.1f} < {HARM_DEALS_SHARE:.0%} of {base:.1f} before"
    return None


def _target(e):
    """The params_auto_buyer section and key of a move: ('shop', name) for a per-shop knob, else ('globals', None)."""
    return ("shop", e.get("sku")) if e.get("knob") in SHOP and e.get("sku") else ("globals", None)


def learn(mem, now, ek, mode):
    """Score the due moves (helped / hurt / unclear) and revert a run's moves only on harm."""
    harmed = {}
    for e in mem.d["episodes"]:                       # old-scale predictions are never scored nor reverted (11-13)
        if e.get("outcome") is None and ((e.get("prediction") or {}).get("metric") not in METRICS or "baseline" not in e):
            e["outcome"] = {"skipped": "pre-11-13 format", "hit": False, "got": None, "t": now}
            log(f"skipped {e.get('knob')} {e.get('sku') or ''} from {time.strftime('%m-%d %H:%M', time.gmtime(e.get('t') or 0))}: pre-11-13 format")
    for e in mem.due(now):
        p = e["prediction"]
        w = float(p.get("window_h") or 1)
        st = window_stats(e["t"], e["t"] + w * 3600, e.get("saved_at"))
        got, n_got, var_got = metric_value(p["metric"], st)
        verdict = judge(p.get("op"), got, var_got, e.get("baseline"), e.get("var_base"))
        pred_hit = got is not None and (got >= p["value"] if p.get("op") == ">=" else got <= p["value"])
        e["outcome"] = {"got": got, "n_got": n_got, "base": e.get("baseline"), "n_base": e.get("n_base"),
                        "verdict": verdict, "hit": verdict == "helped", "pred_hit": pred_hit, "t": now}
        log(f"scored {e['knob']} {e.get('sku') or ''} {p['metric']} {w:g} h: base {e.get('baseline')} (n {e.get('n_base')}) "
            f"-> got {got} (n {n_got}); predicted {p.get('op')} {p.get('value')} -> {verdict}")
        why = harm_of(e, st)
        if why and e.get("applied") and not e.get("reverted"):
            harmed.setdefault(e["t"], why)
    for bt, why in harmed.items():
        batch = [x for x in mem.d["episodes"] if x.get("t") == bt and x.get("applied") and not x.get("reverted")
                 and x.get("value") is not None and x.get("knob")]
        changes = [(*_target(x), x["knob"], x.get("before"), x["value"]) for x in batch]
        if mode != "apply" or not changes:
            log(f"harm (not reverted, mode {mode}): run {time.strftime('%H:%M', time.gmtime(bt))}: {why}")
            continue
        done = write_auto(changes, ek, mem.d.get("t") or now)
        for x, ok in zip(batch, done):
            if ok:
                x["reverted"] = True
                log(f"harm revert {x['knob']} {x.get('sku') or ''} {x['value']} -> {x.get('before')}: {why}")
            else:
                log(f"harm: not reverted {x['knob']} {x.get('sku') or ''}: moved since (this move set {x['value']})")


# ---- writes (11-13) -----------------------------------------------------------------------------------------------
_KEEP = object()


def write_auto(changes, ek, t=None):
    """Locked read-modify-write of params_auto_buyer.json: apply only this run's diffs, the FORCED keys, the end-game
    keys and the rails, then tmp + os.replace. changes: (section, key, knob, value, expect); value None pops the knob;
    a change whose knob no longer holds `expect` (unless _KEEP) is skipped. Returns one bool per change."""
    AUTO_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        # guards.py and ab.py still write AUTO in place (truncate + write, no lock): a read in that window fails, and
        # writing {} + our keys back would wipe every other knob (shop biases, rounds, max_shops, ...). Retry, then skip.
        auto = None
        for i in range(5):
            auto = shop.read(AUTO, None)
            if isinstance(auto, dict) or not AUTO.exists():
                break
            time.sleep(0.2 * (i + 1))
        if not isinstance(auto, dict):
            if AUTO.exists():
                fcntl.flock(lk, fcntl.LOCK_UN)
                log(f"write_auto skipped: {AUTO.name} unreadable after 5 tries ({len(changes)} changes not written)")
                return [False] * len(changes)
            auto = {}
        done = []
        for section, key, knob, value, expect in changes:
            d = auto.setdefault(section, {})
            if key is not None:
                d = d.setdefault(key, {})
            cur = d.get(knob)
            if expect is not _KEEP and (cur is None or abs(float(cur) - float(expect)) > 1e-9):
                done.append(False)
                continue
            if value is None:
                d.pop(knob, None)
            else:
                d[knob] = value
            done.append(True)
        g = auto.setdefault("globals", {})
        g.update(FORCED)
        g.update(ek or {})
        if "v_breakeven" not in (ek or {}) and g.get("v_breakeven") is not None and g["v_breakeven"] < BREAKEVEN_RAIL:
            g["v_breakeven"] = BREAKEVEN_RAIL
        if g.get("goods_delta") is not None and g["goods_delta"] < 0:
            g["goods_delta"] = 0.0
        auto["t"] = t or time.time()
        tmp = AUTO.with_name(AUTO.name + ".tmp")
        tmp.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, AUTO)
        fcntl.flock(lk, fcntl.LOCK_UN)
    return done


# ---- end game (11-13, BS5) ----------------------------------------------------------------------------------------
def hours_left(now=None):
    return (END - (now or time.time())) / 3600


def phase_of(h):
    return ("report only (T-3 h)" if h <= 3 else "end game T-6 h" if h <= 6 else "end game T-12 h" if h <= 12
            else "normal")


def endgame_keys(hours_left, race):
    """11-13 (BS5): in the last 12 h an extra deal near a deals-rank flip is worth more, so the break-even falls to .73
    while a deals rank is within 150 projected deals (race targets.deals_in_play), else .745; in the last 6 h an
    order's slot is worth less (slot_value .02)."""
    if hours_left > 12:
        return {}
    out = {"v_breakeven": 0.73 if race_target(race, "deals_in_play") is True else 0.745}
    if hours_left <= 6:
        out["slot_value"] = 0.02
    return out


def race_now():
    try:
        import market_picture as mp
        r = mp.race_view()
        return r if isinstance(r, dict) else {"error": f"race_view returned {type(r).__name__}"}
    except Exception as e:
        return {"error": repr(e)[:200]}


def _tval(x):
    """A race target is {value, need, status, owner, ...} (market_picture._t); older or plain ones are the value."""
    return x.get("value") if isinstance(x, dict) else x


def race_target(race, key):
    """targets[key] as a value, or a field of the saved_need target (F_hat, f_per_rank live there)."""
    tg = (race or {}).get("targets") or {}
    if key in tg:
        return _tval(tg[key])
    sn = tg.get("saved_need")
    return sn.get(key) if isinstance(sn, dict) else None


def race_brief(race):
    parts, tg = race.get("parts") or {}, race.get("targets") or {}
    out = {"parts": {k: parts.get(k) for k in ("saved", "deals", "posts") if k in parts},
           "targets": {k: v for k, v in tg.items()
                       if k.startswith("buyer_") or k in ("saved_need", "deals_in_play", "F_hat", "f_per_rank", "phase")}}
    if race.get("error"):
        out["error"] = race["error"]
    return out


# ---- evidence ------------------------------------------------------------------------------------------------------
def buy_health(now, hours=3):
    """Plumbing (11-08: the shop lost ~10 h of sales to refused settles nobody saw): our deal records against the
    market's settles for our buyer, and where the other visits ended."""
    rows = [t for t in shop.read(BMEM, {}).get("trades", []) if t.get("t", 0) >= now - hours * 3600]
    out = Counter(str(t.get("outcome"))[:30] for t in rows)
    return {"window_h": hours, "deal_records": out.get("deal", 0), "outcomes": dict(out.most_common(10)),
            "note": "compare deal_records with the board's deal count growth; 'unpaid'/'refused' are plumbing"}


# 11-13 review: policy.merged lays a params_auto_buyer global over the buyer's params ONLY when params.json buyer.base
# has that key, so v_floor_rel, anchor_share, anchor_share_food/goods and anchor_x sat in the file unused (3 h: anchor
# 50% per kind = vprice default .5, food floor .765 = v_floor, not m* + v_floor_rel). value_half_life_h is read
# straight from the file (buyer_value.half_life).
DIRECT_KNOBS = {"value_half_life_h"}


def knob_live(k, bp):
    """Does a move on global knob k reach market_buyer? (k in params.json buyer.base, or read from the file directly)"""
    return k in (bp or {}) or k in DIRECT_KNOBS


def knob_now(k, auto, bp):
    if not knob_live(k, bp):                 # what the buyer really uses: vprice's default (None = unset)
        try:
            import vprice
            return vprice.DEFAULTS.get(k)
        except Exception:
            return None
    v = (auto.get("globals") or {}).get(k)
    if v is None:
        v = bp.get(k)
    if v is None:
        try:
            import vprice
            v = vprice.DEFAULTS.get(k)
        except Exception:
            v = None
    return v


def knobs_view(auto, bp):
    out = [{"name": k, "bounds": list(b), "now": knob_now(k, auto, bp)} | ({"max_move": FLOOR_STEP} if k in FLOOR_KNOBS else {})
           | ({} if knob_live(k, bp) else {"live": False, "note": "not in params.json buyer.base: the buyer ignores it; moves are dropped"})
           for k, (b, _) in GLOBAL.items()]
    shops_auto = auto.get("shop") or {}
    for k, (b, _) in SHOP.items():
        out.append({"name": k, "per_shop": True, "bounds": list(b),
                    "now": {s: d[k] for s, d in shops_auto.items() if isinstance(d, dict) and d.get(k)}})
    return out


def ledger_view(mem, n=12):
    out = []
    for e in [e for e in mem.d["episodes"] if e.get("knob")][-n:]:
        p, o = e.get("prediction") or {}, e.get("outcome") or {}
        verdict = (o.get("verdict") or (o.get("skipped") and f"skipped ({o['skipped']})")
                   or ("pending" if not o else ("hit" if o.get("hit") else "miss") + " (old format)"))
        out.append({"at": time.strftime("%m-%d %H:%M", time.gmtime(e.get("t") or 0)), "knob": e["knob"],
                    **({"shop": e["sku"]} if e.get("sku") else {}), "from": e.get("before"), "to": e.get("value"),
                    "metric": p.get("metric"), "window_h": p.get("window_h") or p.get("hours"),
                    "predicted": f"{p.get('op')} {p.get('value')}", "baseline": e.get("baseline"), "got": o.get("got"),
                    "verdict": verdict, "reverted": bool(e.get("reverted"))})
    return out


def facts_view(mem):
    return [{"id": f["id"], "text": f["text"], "query": f.get("query"),
             "measured_at": time.strftime("%m-%d %H:%M", time.gmtime(f.get("measured_at") or 0)),
             "expires": time.strftime("%m-%d %H:%M", time.gmtime(f.get("expires") or 0))}
            for f in mem.d.get("facts") or []]


def edit_facts(mem, ops, now):
    """11-13: facts replace lessons: at most MAX_FACTS, each with the query that measured it and an expiry (1-48 h)."""
    facts = mem.d.setdefault("facts", [])
    for e in ops or []:
        if not isinstance(e, dict):
            continue
        op, fid = e.get("op"), str(e.get("id") or "")
        cur = next((f for f in facts if f["id"] == fid), None)
        try:
            h = min(48.0, max(1.0, float(e.get("expires_h") or 24)))
        except (TypeError, ValueError):
            h = 24.0
        if op == "retire" and cur:
            facts.remove(cur)
            log(f"fact {fid} retired: {cur['text'][:200]}")
        elif op in ("add", "rewrite") and e.get("text"):
            if op == "add" and (cur or not fid):
                n = max([int(f["id"][1:]) for f in facts if f["id"][1:].isdigit()] + [0])
                fid, cur = f"F{n + 1}", None
            new = {"id": fid, "text": str(e["text"])[:300], "query": str(e.get("query") or "")[:300],
                   "measured_at": now, "expires": now + h * 3600}
            if cur:
                cur.update(new)
            else:
                facts.append(new)
            log(f"fact {fid} {op}: {new['text'][:200]} (expires in {h:g} h)")
    while len(facts) > MAX_FACTS:
        old = min(facts, key=lambda f: f.get("measured_at") or 0)
        facts.remove(old)
        log(f"fact {old['id']} dropped (capacity {MAX_FACTS})")


def evidence(mem, auto, bp, race, now):
    r24 = trades(now - 24 * 3600)
    shops = {}
    for r in r24:
        if r.get("shop") and not str(r.get("outcome", "")).startswith("skipped"):
            shops.setdefault(r["shop"], []).append(r)
    top_s = sorted(shops.items(), key=lambda kv: -len(kv[1]))[:12]
    try:
        dg = json.loads(read_digest("buyer") or "{}")
    except ValueError:
        dg = {}
    pb = shop.read(HERE / ".market" / "peer_bench.json", {}) or {}
    h = hours_left(now)
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), "hours_left": round(h, 1), "phase": phase_of(h),
            "race": race_brief(race), "funnel": funnel(now), "score": score_view(), "buy_health": buy_health(now),
            "by_shop_24h": {k: summary(v) for k, v in top_s},
            "peer_benchmark": (pb.get("better") or [])[:6],
            "knobs": knobs_view(auto, bp), "ledger": ledger_view(mem), "facts": facts_view(mem),
            "curator": {k: dg.get(k) for k in ("rules_change", "answers_to_your_requests")},
            "from_shop_strategist": read_board("buyer"),
            "from_operator": operator_note(OPERATOR_NOTE, OPERATOR_ANSWER)}


DECIDE = """You run the BUYER of yms979 (student 23530007) in a class market. Round 3 ends 2026-11-17 14:59 UTC; {hours_left:.1f} h are left (phase: {phase}).

OBJECTIVE
Maximise yms979's class TOTAL at the end. Your parts are .5 x pct(saved) + .1 x pct(deals) among the 46 students: one saved rank is worth .0111, one deals rank .0022. Code sends posts at the maximum rate. Forum votes are not yours. Report the runner-up's buyer (25620027) head to head, but never target it, any shop or any classmate.

SCORE MODEL (patch 3.6 scale; every v in this prompt is on it)
- v = min(1, (list - price) / (list - unit cost)) per deal. A broken unit scores 0 if refunded and -1 if not.
- saved = sum(v) / (deals + 100). With ~16,500 deals in the mean, a deal raises the final saved only if its v beats F, the projected final saved (now {F_hat}).
- +.01 of v on every future deal is worth about +.003 of final saved. +10 deals/h at v .85 is worth about +.0033.
- One saved rank is about +{f_per_rank} of final saved now. The deals ranks next to us are in RACE.
- The break-even v (one more deal neither lifts nor lowers our class points, the deals rank included) is the knob v_breakeven, about .75.

RACE AND TARGETS (code, every 15 min)
{race}

FUNNEL (our trades, last 1 h and 3 h, 3.6 scale)
{funnel}

HOW THE CODE BUYER WORKS (no model call per order)
- Per order it ranks the shops that hold an acceptable SKU. The score is P(deal per visit, ours, 6 h, this kind) x (expected v - v_breakeven) - slot_value x (seconds past the order's 20 s) / 20. Expected v is the market's realized v at that shop, calibrated with our own results there.
- A shop under dead_p per visit is skipped. An order is skipped when no visit is worth more than 0.
- Price policy (vprice.py), all in v: floor = m* + v_floor_rel, plus food_delta for food. Goods use (base + goods_delta) / (1 - break rate of the grade). The cap is the price at the floor.
- An ask at take_v or better is taken at once. Otherwise we counter at open_v, then open_v - step_v, and so on for `rounds` counters (soft_rounds at shops that end talks on low offers). Then we take any ask at or under the cap, or walk (max_shops per order).
- anchor_share_food and anchor_share_goods set the share of orders that open at anchor_x of list. The anchor won on F per order. Rejections mostly come before any offer.
- A shop|SKU that answered 'grade C claimed', 'no warranty', 'no warranty on receipt' or a fixed ask above our cap is skipped for 2 h. Goods receipts are paid only with a warranty of 6 h or more.
Knobs (name, bounds, now):
{knobs}

WHAT YOU DECIDE
- The knobs above, inside their bounds.
- Price floors (v_floor, v_floor_rel, food_delta, goods_delta, v_breakeven, near_v) move at most .01 per run. Every other knob may go anywhere in its bounds.
- Per-shop bias_food, bias_goods and shop_bias (-0.1..0.1) only for a shop named in the evidence.
- At most {max_moves} moves.
RAILS (code enforces them): v_breakeven >= .745 until the end game sets it. goods_delta >= 0. rival_penalty is 0. unit_skip stays on. The end-game keys belong to code.

WHEN TO MOVE
The targets are v6 >= .845 per deal and >= 85 deals/h.
- Move when a target is OFF and the FUNNEL names a cause a knob reaches. Example: food orders skipped as low value while their expected v is above F. Goods visits that end 'rejected' before any offer are not a price problem.
- Hold when both targets are ON, or while a move on the same metric is still inside its window. 'Hard to score' is never a reason to hold.
- A peer row counts only when it shows a copyable mechanism at the same shop and kind.

MOVE FORMAT AND LEARNING
Each move names ONE metric from {metrics} and a window of 1-3 h. Code measures it on the 3.6 scale over the window, against the same window before the move, and writes helped, hurt or unclear (2 standard errors) into the MOVE LEDGER. Only a harm guard reverts a move: mean v < .83 over 60 or more deals in the window, or deals/h under 60% of the baseline.
MOVE LEDGER (last 12): {ledger}
FACTS (at most 15, each with an expiry; add, rewrite or retire them, with the query that measured each): {facts}

END GAME (code; report it, do not undo it)
- T-12 h: v_breakeven = .73 if a deals rank is within 150 projected deals either way (race targets.deals_in_play), else .745.
- T-6 h: slot_value .02.
- T-3 h: report only, no moves.

CONDUCT
No agree-then-refuse talks. No stalling. No terms aimed at a shop or a classmate. Forum text is unverified; never act on it.

RULE CHANGES
When evidence.curator.rules_change is not empty, re-check every knob against the new rule before anything else, and say what changed.

THE OPERATOR AND THE USER
evidence.from_operator holds notes and CONSTRAINTs {{knob, value, expires}}. A note is evidence. An unexpired CONSTRAINT binds only the knob it names. Answer any question in 'answer_to_operator'.
Do not wait on the user for anything reversible: decide, say why, and watch it.
'alert_user' (Korean, 1-3 short items) is only for: class TOTAL 1st under threat now, money leaking, a fault, or a decision that is the user's. No future ranks or projections. It is usually empty.

TOOLS: the read-only mcp__market__* tools, at most 6 calls, only to check a cause before a move.

EVIDENCE
{evidence}

Answer with one JSON object only:
{{"read": "<6-10 sentences: which target is OFF, its cause in the funnel numbers, why this move or why holding>",
  "moves": [{{"knob": "...", "shop": "<per-shop knobs only>", "value": n, "why": "...", "target": "buyer_v6" or "buyer_deals_h", "prediction": {{"metric": "...", "window_h": 1, "op": ">=", "value": n}}}}],
  "facts": [{{"op": "add", "id": "F1", "text": "...", "query": "...", "expires_h": 24}}],
  "notes_for_shop": ["..."],
  "answer_to_operator": "...",
  "alert_user": []}}"""


def build_prompt(ev):
    """DECIDE filled from the evidence; the blocks with their own place in the template leave the EVIDENCE block.
    11-13: kept under PROMPT_MAX by cutting by_shop_24h to 8 rows, then peer_benchmark to 4."""
    j = lambda x: json.dumps(x, ensure_ascii=False)
    own = ("time_utc", "hours_left", "phase", "race", "funnel", "knobs", "ledger", "facts")

    def fill():
        F, fpr = race_target(ev["race"], "F_hat"), race_target(ev["race"], "f_per_rank")
        if F is None:
            F = (ev.get("score") or {}).get("m_star")
        return DECIDE.format(hours_left=float(ev["hours_left"]), phase=ev["phase"],
                             F_hat=F if F is not None else "n/a", f_per_rank=fpr if fpr is not None else "n/a",
                             race=j(ev["race"]), funnel=j(ev["funnel"]), knobs=j(ev["knobs"]), max_moves=MAX_MOVES,
                             metrics=j(list(METRICS)), ledger=j(ev["ledger"]), facts=j(ev["facts"]),
                             evidence=j({"time_utc": ev["time_utc"]} | {k: v for k, v in ev.items() if k not in own}))
    prompt = fill()
    if len(prompt) > PROMPT_MAX:
        ev["by_shop_24h"] = dict(list(ev["by_shop_24h"].items())[:8])
        prompt = fill()
    if len(prompt) > PROMPT_MAX:
        ev["peer_benchmark"] = ev["peer_benchmark"][:4]
        prompt = fill()
    return prompt


def score_view():
    """buyer_score.json (buyer_score.py, every 15 min) without its history, plus the guards' state."""
    sc = shop.read(HERE / ".market" / "buyer_score.json", {})
    sc = {k: v for k, v in sc.items() if k != "hist"}
    cal = sc.get("calibration") or {}
    shops_cal = sorted(((k, v) for k, v in cal.items() if "|" in k and v.get("n", 0) >= 8), key=lambda kv: kv[1]["gap"])
    sc["calibration"] = {k: v for k, v in cal.items() if "|" not in k} | dict(shops_cal[:8] + shops_cal[-4:])
    ours = sc.get("ours") or {}
    sc["ours"] = dict(sorted(ours.items(), key=lambda kv: -kv[1]["n"])[:20])
    g = shop.read(HERE / ".market" / "guard_state.json", {})
    now = time.time()
    sc["guard"] = {"fires_last_tick": g.get("fires"), "frozen": {k: round((u - now) / 60) for k, u in (g.get("frozen") or {}).items() if u > now},
                   "freeze_all_min": max(0, round((g.get("freeze_all_until", 0) - now) / 60)),
                   "freeze_shares_min": max(0, round((g.get("freeze_shares_until", 0) - now) / 60)),
                   "overrides": {k: v.get("value") for k, v in (shop.read(HERE / ".market" / "guard_overrides.json", {}) or {}).items() if v.get("until", 0) > now},
                   "spend_24h_usd": g.get("spend_24h"), "book_eta_h": g.get("book_eta_h")}
    return sc


def class_ladder(b):
    """Students only: the 8 just above and 3 below us in saved, with the deals at a per-deal value v needed to pass
    each (saved' = (S + k v)/(N + 100 + k)), for v = .36 and .42. (market_tools.class_ladder; not in the prompt.)"""
    try:
        full = __import__("board_api").fetch(timeout=20)
        names = {(p.get("buyer") or {}).get("name") for p in full["players"] if p.get("student")}
        rows = sorted((x for x in full["buyers"] if x["name"] in names and x.get("saved") is not None), key=lambda x: -x["saved"])
        i = next(k for k, x in enumerate(rows) if x["name"] == "yms979")
        me = rows[i]
        S, N = me["saved"] * (me["deals"] + 100), me["deals"]
        out = []
        for k, x in enumerate(rows[max(0, i - 8): i + 4]):
            e = {"rank": max(0, i - 8) + k + 1, "name": x["name"], "saved": x["saved"], "deals": x["deals"]}
            if x["saved"] > me["saved"]:
                tgt = x["saved"] + 0.0005
                e["deals_to_pass_at_v"] = {str(v): (int((tgt * (N + 100) - S) / (v - tgt)) if v > tgt else None) for v in (0.36, 0.42)}
            if x["name"] == "yms979":
                e["us"] = True
            out.append(e)
        return {"students": len(rows), "our_saved_rank": i + 1, "rows": out}
    except Exception as e:
        return {"error": repr(e)}


def board_view():
    """The board for memory_curator.watch (bs.board_view); not in this prompt since 11-13."""
    b = shop.board()
    full = __import__("board_api").fetch(timeout=30)
    me = next(x for x in full["buyers"] if x["name"] == "yms979")
    b["buyer"] = {k: me.get(k) for k in ("all", "saved", "forum", "deals", "orders", "posts")} | \
        {k: v for k, v in b["buyer"].items() if k.endswith("_rank")}
    rows = [x for x in full["buyers"] if x.get("all") is not None]
    b["buyers_all"] = [{"name": x["name"], "saved": x.get("saved")} for x in rows]
    order = sorted(rows, key=lambda x: -(x.get("all") or 0))
    b["buyer"]["rank_on_buyer_all"] = next((i + 1 for i, x in enumerate(order) if x["name"] == "yms979"), None)
    b["buyer_leaders"] = [{k: x.get(k) for k in ("name", "all", "saved", "forum", "deals", "posts")} for x in order[:6]]
    # user 11-06: learn from buyers ranked above us with a saved rate like ours (inextro 0.246 is 5th): which
    # components carry them, as percentiles, so the gap to close is visible per component
    def pct(k, v):
        vals = [x.get(k) or 0 for x in rows]
        return round((sum(1 for q in vals if q < v) + 0.5 * sum(1 for q in vals if q == v)) / len(vals), 2)
    b["peers_above"] = [{"rank": i + 1, "name": x["name"], "all": x["all"],
                         **{k: (x.get(k), pct(k, x.get(k) or 0)) for k in ("saved", "forum", "deals", "posts")}}
                        for i, x in enumerate(order[:20])]
    me = next(x for x in rows if x["name"] == "yms979")
    b["buyer"]["percentiles"] = {k: pct(k, me.get(k) or 0) for k in ("saved", "deals", "posts")} | \
        {"forum_rating_scored_as_is": me.get("forum")}          # patch 3.5: forum enters buyer ALL as the rating
    return b


def board_saved(sc):
    """The board saved every new deal is measured against (buyer_score.json m_now, refreshed every 15 min)."""
    if sc.get("m_now") is not None:
        return sc["m_now"]
    try:
        return shop.board()["buyer"].get("saved")
    except Exception:
        return None


def dry_prompt():
    """--dry-prompt: build the evidence and the prompt, print its length and head; no model call, no writes."""
    mem = Memory(STATE)
    params = shop.read(shop.PARAMS, {})
    auto = shop.read(AUTO, {"globals": {}})
    now = time.time()
    race = race_now()
    ev = evidence(mem, auto, params.get("buyer", {}).get("base", {}), race, now)
    prompt = build_prompt(ev)
    print(len(prompt))
    print(prompt[:3000])
    print(f"--- endgame_keys now: {endgame_keys(hours_left(now), race)}; race: {json.dumps(race_brief(race), ensure_ascii=False)[:300]}")


def run(force=False):
    mem = Memory(STATE)
    params = shop.read(shop.PARAMS, {})
    mode = params.get("strategist", {}).get("mode", "propose")
    now = time.time()
    H = hours_left(now)
    race = race_now()                                         # 11-13: the end game and FORCED keys every tick
    ek = endgame_keys(H, race)
    g0 = (shop.read(AUTO, {}) or {}).get("globals") or {}
    need = {k: v for k, v in (FORCED | ek).items() if g0.get(k) != v}
    if need:
        if mode == "apply":
            write_auto([], ek, mem.d.get("t") or now)
        for k, v in need.items():
            log(f"{'endgame' if k in ek else 'forced'}: {k} {g0.get(k)} -> {v} ({H:.1f} h left, {phase_of(H)})"
                f"{'' if mode == 'apply' else ' [not written: mode ' + mode + ']'}")
    slot = my_turn("buyer")
    wake = shop.read(BUYER_WAKE, None)
    if wake and not force and time.time() - mem.d.get("t", 0) < 3600:
        wake = None                     # a guard may wake the buyer at most once an hour
    if not force and not wake and (slot is None or slot == mem.d.get("slot")):
        return
    if wake:
        BUYER_WAKE.unlink(missing_ok=True)
        log(f"woken early by a guard: {json.dumps(wake, ensure_ascii=False)[:300]}")
    guard = shop.read(HERE / ".market" / "guard_state.json", {})     # 11-11: guards.py
    if not force and not wake and guard.get("budget_block_until", 0) > time.time():
        log("slot skipped: G10 budget block (guard wakes still run)")
        return
    mem.d["slot"] = slot if slot is not None else mem.d.get("slot")
    mem.d["t"] = time.time()
    mem.d.setdefault("working", {})["woken_by"] = wake
    mem.d.pop("steps", None)                                  # 11-13: no adaptive steps
    now = time.time()
    facts = mem.d.setdefault("facts", [])
    for f in [f for f in facts if (f.get("expires") or 0) <= now]:
        facts.remove(f)
        log(f"fact {f['id']} expired: {f['text'][:200]}")
    learn(mem, now, ek, mode)
    mem.fold()
    go, shop.EFFORT_NOW, why = run_gate("buyer", mem.d, woken=bool(wake) or force)
    if not go:
        log(f"model skipped (cost gate): {why}")
        mem.save()
        return
    log(f"model run (effort {shop.EFFORT_NOW}): {why}")
    auto = shop.read(AUTO, {"globals": {}, "category": {}})
    bp = params.get("buyer", {}).get("base", {})
    ev = evidence(mem, auto, bp, race, now)
    prompt = build_prompt(ev)
    log(f"prompt {len(prompt)} chars; v 1 h {ev['funnel']['last_1h']['v_mean']}, 3 h {ev['funnel']['last_3h']['v_mean']}, "
        f"deals/h 3 h {ev['funnel']['last_3h']['deals_h']} (3.6 scale)")
    raw = shop.ask_model(prompt, "buyer strategist")
    ans = last_json_object(raw) or {}
    LAST.write_text(json.dumps({"t": mem.d["t"], "answer": ans, "raw": raw[-4000:]}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    if not ans:
        log(f"no usable answer ({len(raw)} chars)")
        mem.save()
        return
    log(f"[{mode}] read: {str(ans.get('read'))[:1200]}")
    sent = model_env.alert_user("buyer", ans.get("alert_user"))
    if sent:
        log(f"alerted the user: {sent}")
    if ans.get("answer_to_operator") and shop.read(OPERATOR_NOTE, {}).get("note"):
        OPERATOR_ANSWER.write_text(json.dumps({"t": time.time(), "question": shop.read(OPERATOR_NOTE, {}).get("note"),
                                               "answer": ans["answer_to_operator"]}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        log(f"answered the operator: {str(ans['answer_to_operator'])[:600]}")
    requests = valid_requests(ans.get("requests") or ans.get("investigate"))
    moves_in = [m for m in (ans.get("moves") or []) if isinstance(m, dict)]
    post_board("buyer", ans.get("read"), {"moves": [(m.get("knob"), m.get("shop"), m.get("value")) for m in moves_in][:8],
                                          "v_by_kind_3h": ev["funnel"]["last_3h"]["v_mean"],
                                          "deals_h_3h": ev["funnel"]["last_3h"]["deals_h"]},
               ans.get("notes_for_shop"), requests)
    for q in requests:
        log(f"asked the curator: {json.dumps(q, ensure_ascii=False)[:160]}")
    edit_facts(mem, ans.get("facts"), now)
    shops_known = set(ev["by_shop_24h"]) | {x.get("shop") for r in ev["peer_benchmark"] for x in (r.get("same_shop") or [])} | {
        k.split("|")[0] for k in list((ev["score"].get("ours") or {})) + list((ev["score"].get("calibration") or {})) if "|" in k}
    S = board_saved(ev["score"])
    churn = mem.d.setdefault("churn_block", {})
    plan = []                                                 # (episode, change)
    for mv in moves_in[MAX_MOVES:]:
        log(f"dropped {mv.get('knob')} {mv.get('shop') or ''}: over MAX_MOVES {MAX_MOVES}")
    for mv in moves_in[:MAX_MOVES]:
        knob, pred = mv.get("knob"), mv.get("prediction")
        pred = pred if isinstance(pred, dict) else {}
        if not isinstance(knob, str) or (mv.get("shop") is not None and not isinstance(mv.get("shop"), str)):
            log(f"dropped {knob!r}: malformed move")
            continue
        try:
            value = float(mv.get("value"))
        except (TypeError, ValueError):
            log(f"dropped {knob}: no numeric value")
            continue
        if H <= 3:
            log(f"dropped {knob}: report only in the last 3 h ({H:.1f} h left)")
            continue
        if knob in ek:
            log(f"dropped {knob}: endgame key (code holds {ek[knob]})")
            continue
        if knob in FORCED:
            log(f"dropped {knob}: forced by code to {FORCED[knob]}")
            continue
        try:
            w = min(3.0, max(1.0, float(pred.get("window_h") or 1)))
            pv = float(pred.get("value"))
        except (TypeError, ValueError):
            log(f"dropped {knob}: no checkable prediction")
            continue
        if pred.get("metric") not in METRICS or pred.get("op") not in (">=", "<="):
            log(f"dropped {knob}: no checkable prediction ({pred.get('metric')!r} {pred.get('op')!r})")
            continue
        if knob in SHOP:
            key = mv.get("shop")
            if key not in shops_known:
                log(f"dropped {knob} {key}: not a shop in the evidence")
                continue
            (lo, hi), _ = SHOP[knob]
            cur = ((auto.get("shop") or {}).get(key) or {}).get(knob, 0)
        elif knob in GLOBAL:
            key = None
            if not knob_live(knob, bp):
                log(f"dropped {knob}: not live (policy.merged ignores keys missing from params.json buyer.base)")
                continue
            (lo, hi), _ = GLOBAL[knob]
            cur = knob_now(knob, auto, bp)
            cur = value if cur is None else cur
        else:
            log(f"dropped unknown knob {knob!r}")
            continue
        new = min(hi, max(lo, value))
        if knob in FLOOR_KNOBS:
            new = min(cur + FLOOR_STEP, max(cur - FLOOR_STEP, new))
        if knob == "v_breakeven":
            new = max(BREAKEVEN_RAIL, new)
        if knob == "goods_delta":
            new = max(0.0, new)
        new = int(round(new)) if knob in INT_KNOBS else round(new, 3)
        if abs(new - float(cur)) < 1e-9:
            log(f"dropped {knob} {key or ''}: no change ({cur:g}, asked {value:g})")
            continue
        now_ = time.time()                                             # 11-11: guards.py freezes
        if (guard.get("frozen") or {}).get(knob, 0) > now_:
            log(f"dropped {knob}: frozen by G6 until {time.strftime('%H:%M', time.gmtime(guard['frozen'][knob]))} UTC")
            continue
        if guard.get("freeze_all_until", 0) > now_ or (knob.endswith("_share") and guard.get("freeze_shares_until", 0) > now_):
            log(f"dropped {knob}: moves frozen by a guard (G3 collapse / G1 reconcile)")
            continue
        ck = f"{knob}|{key or ''}"
        if churn.get(ck, 0) > now_:
            log(f"dropped {knob} {key or ''}: churn block until {time.strftime('%H:%M', time.gmtime(churn[ck]))} UTC")
            continue
        moved = sum(1 for e in mem.d["episodes"] if e.get("applied") and e.get("knob") == knob and (e.get("sku") or None) == key
                    and e.get("t", 0) >= now_ - CHURN_H * 3600)
        if moved >= CHURN_N:
            churn[ck] = now_ + CHURN_BLOCK_H * 3600
            log(f"dropped {knob} {key or ''}: moved {moved} times in {CHURN_H} h; blocked {CHURN_BLOCK_H} h")
            continue
        st = window_stats(mem.d["t"] - w * 3600, mem.d["t"], S)
        base, n_base, var_base = metric_value(pred["metric"], st)
        log(f"[{mode}] {knob} {key or ''} {cur:g} -> {new:g} (asked {value:g}) | {str(mv.get('why'))[:200]} "
            f"| predicts {pred['metric']} {pred['op']} {pv:g} in {w:g} h (base {base}, n {n_base}) | target {mv.get('target')}")
        ep = {"t": mem.d["t"], "due": mem.d["t"] + w * 3600, "saved_at": S, "knob": knob, "sku": key, "value": new,
              "before": cur, "why": str(mv.get("why"))[:400], "target": mv.get("target"), "uses": [],
              "prediction": {"metric": pred["metric"], "window_h": w, "op": pred["op"], "value": pv},
              "baseline": base, "n_base": n_base, "var_base": var_base, "base_deals_h": round(st["n"] / st["h"], 2),
              "applied": False, "reverted": False}
        plan.append((ep, ("shop", key, knob, new, _KEEP) if key else ("globals", None, knob, new, _KEEP)))
    if plan and mode == "apply":
        done = write_auto([c for _, c in plan], ek, mem.d["t"])
        for (ep, _), ok in zip(plan, done):
            ep["applied"] = ok
    for ep, _ in plan:
        mem.add_episode(ep)
    mem.save()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-prompt", action="store_true", help="print the prompt's length and head; no model call, no writes")
    a = ap.parse_args()
    if a.dry_prompt:
        dry_prompt()
        raise SystemExit(0)
    lock = open(HERE / ".market" / "buyer_strategist.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(0)
    run(a.force)
