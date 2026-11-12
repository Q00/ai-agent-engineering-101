"""Week 08 — restock.py: the kit's restock hook (every 60 s in Round 3). Little stock, fast turnover.

    python restock.py --state .market

Order of buying, each line only while cash stays above the reserve:
  1. reservations: SKUs a buyer asked our shop for that we did not have (the shop wrote them to
     private/reserve.jsonl); one unit each, so the next buyer gets it
  2. demand: target = rate per hour x cover hours (market settles plus unmet asks, decayed); the hero SKUs
     (selling fastest) get a deeper target, they are the ones sold lowest in the market
  3. display: one unit in categories with demand but no stock, so buyers see us in shops.json; long shelf life only
Food that lasts 48 h or less is bought only once its demand is proven.
Supply opens for 3 minutes every 20 (inventory.json "restock": open, opens_at, closes_at; rule added 2026-11-03).
Outside a window the run prints no lines (the kit would wait up to 90 s per line, then skip it); a minute before
a window opens it plans, so the kit's first line goes out as the window opens. What a window buys has to last to
the next one, so reservations wait for it and are kept an hour.
Prints {"buy": [{"sku": ..., "qty": ...}]}; the kit buys at most 10 lines a run.
"""
import argparse
import json
import math
import os
import time
from pathlib import Path

import market_model

THETA = Path(os.environ.get("SHOP_THETA", Path(__file__).parent / "private" / "shop_theta.json"))
RESERVE = Path(__file__).parent / "private" / "reserve.jsonl"
DEFAULTS = {"cash_reserve": 0.35, "max_list": 300000, "cover_h": {"food": 6, "goods": 24}, "hero_depth": 2.0,
            "heroes": 8, "max_qty": 20, "display_cats": 40, "short_shelf_rate": 0.5, "cost_guess": 0.6,
            # first window of Round 3 (2026-11-03 21:05) bought ~1.9M on 3 market settles per SKU: the market's
            # demand counted as ours, and no floor on evidence. Now: our share of the market rate, a minimum of
            # settles before buying for demand, a cap on goods we have not sold yet, and a spend cap per window
            "share_prior": 0.2, "min_settles": 3, "goods_cap_unproven": 3, "proven_sales": 2,
            "window_budget": 0.08,
            # regulars: buyers with 2+ settled buys from us in 48 h; keep their categories in stock first
            "regular_min_buys": 2, "regular_depth": 2,
            # the marketing plan's picks (high-ticket entries, exclusives) get their own budget per window, above
            # the general cap: leaders sell 88k-210k per deal in bedding and mattresses, we sold 10k (2026-11-03)
            "pick_window_budget": 0.08}
STATE = Path(__file__).parent / "private" / "restock_state.json"


def theta():
    try:
        t = {**DEFAULTS, **json.loads(THETA.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        t = dict(DEFAULTS)
    try:                                                      # the finance team's limits bind every run
        import finance_agent
        fl = finance_agent.limits()
    except Exception:
        fl = {}
    try:                                                      # the owner's override wins over finance (HQ 11-04)
        ov = json.loads((Path(__file__).parent / "private" / "owner_override.json").read_text(encoding="utf-8"))
        if ov.get("window_spend") is not None:
            fl = {**fl, "window_spend": ov["window_spend"]}
    except (OSError, ValueError):
        pass
    if fl:
        t["cash_reserve"] = max(t["cash_reserve"], fl.get("cash_floor", 0))
        t["window_budget"] = min(t["window_budget"], fl.get("window_spend", t["window_budget"]))
        t["pick_window_budget"] = min(t["pick_window_budget"], fl.get("window_spend", t["pick_window_budget"]))
    pace = load(PACE.parent, PACE.name, {})
    if pace.get("calm"):                                      # stock outran sales for an hour: the pre-attack levels
        for k, v in CALM.items():
            t[k] = min(t.get(k, v), v)
    return t


PACE = Path(__file__).parent / "private" / "stock_pace.json"
# HQ 11-04 16:50: "팔리는 추이를 보고, 이 상태가 1시간 이상 반복될 것 같으면 매입 추이를 기존으로 회복시켜". The levels
# before the 14:35 / 16:20 attack: 15M per window for picks, 2.5M general, 6-window hit cover, no big-ticket doubling
CALM = {"window_budget": 0.5, "pick_window_budget": 3.0, "hit_cover": 6, "hit_max_value": 6_000_000,
        "hit_big_ticket": 10 ** 12}
PACE_OVER_H, PACE_OK_H, PACE_HOLD_S = 3.0, 2.0, 3600
PACE_CASH_OK = 17_000_000     # 11-04 17:05 finance simulation: profit >= 12M (balance 17M) is shop top 3


def pace(c, inv, now=None):
    """Stock cover in hours: our stock at cost / the cost of what we sold in the last hour. Over PACE_OVER_H for an
    hour in a row -> calm (the CALM levels apply in theta()); back at or under PACE_OK_H -> aggressive again. Every
    change is logged in private/stock_pace.json."""
    now = now or time.time()
    stock = sum(u.get("cost") or 0 for u in inv.get("units", []) if u.get("grade") != "expired")
    cogs = c.execute("""select coalesce(sum(su.cost), 0) from settles s join settle_units x on x.seq = s.seq
                        join supply_units su on su.unit = x.unit where su.shop = 'dragonash' and s.ts > ?""",
                     (now - 3600,)).fetchone()[0]
    cover = stock / cogs if cogs else float("inf")
    st = load(PACE.parent, PACE.name, {})
    was = bool(st.get("calm"))
    # Round 3 patch 3.4 (11-06): the shop score is Book = balance - start - aid + cost of unsold stock, so stock bought
    # is no longer a loss on the board; only cash can hurt (bankruptcy). Calm now means cash under PACE_CASH_OK only.
    # The cover rule had held the shop calm since 11-05 on 64.7M of slow stock while 252M sat idle (11-07).
    if (inv.get("balance") or 0) < PACE_CASH_OK:
        st["over_since"], st["calm"] = st.get("over_since") or now, True
    else:
        st["over_since"], st["calm"] = None, False
    st.update(cover_h=round(cover, 2) if cover != float("inf") else None, stock_cost=round(stock),
              cogs_1h=round(cogs), at=time.strftime("%m-%d %H:%M"))
    if bool(st.get("calm")) != was:
        st.setdefault("log", []).append(f"{st['at']} {'calm' if st['calm'] else 'aggressive'}: cover {st['cover_h']} h")
        st["log"] = st["log"][-50:]
    try:
        PACE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return st


def load(md, name, default):
    try:
        return json.loads((md / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _ours(c, now, hours=24):
    """{sku: (units sold, average ticket, average profit per unit)} for our shop over the last `hours`."""
    out = {}
    for sku, n, tk, pr in c.execute("""select s.sku, count(*), avg(s.amount), avg(s.amount - su.cost) from settles s
                                       join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                                       where su.shop = 'dragonash' and s.ts > ? group by s.sku""", (now - hours * 3600,)):
        out[sku] = (n, tk or 0, pr or 0)
    return out


def by_value(c, buy, now=None):
    """HQ 11-04 priority for the window (the kit buys 10 lines per run, 10 s apart, in a 3-min window, so the first
    lines are the ones that get bought): 1. sells well (our units sold in 24 h, in tiers), 2. high ticket, 3. high
    margin. A SKU we have not sold yet goes after every one we have."""
    now = now or time.time()
    ours = _ours(c, now)
    tier = lambda n: 3 if n >= 50 else 2 if n >= 10 else 1 if n >= 3 else 0

    def key(b):                                        # HQ 11-10: profit per day (units x profit per unit) first --
        n, tk, pr = ours.get(b["sku"], (0, 0, 0))      # pace, ticket and margin in one number
        return (n * pr, tier(n), tk)
    return sorted(buy, key=key, reverse=True)


def low_value(c, now, t, cat):
    """HQ 11-04 "객단가 낮고 이윤 낮은 제품은 팔지마": SKUs we do not stock again.
      sold by us (2+ in 24 h): average ticket under low_ticket AND average profit per unit under low_profit
      never sold by us: list price under low_list (a small ticket whose profit is unknown)"""
    ours = {}
    for sku, a, cost in c.execute("""select s.sku, s.amount, su.cost from settles s join settle_units x on x.seq = s.seq
                                     join supply_units su on su.unit = x.unit where su.shop = 'dragonash' and s.ts > ?""",
                                  (now - 24 * 3600,)):
        ours.setdefault(sku, []).append((a, a - cost))
    low = {sku for sku, v in ours.items() if len(v) >= 2
           and sum(a for a, _ in v) / len(v) < t.get("low_ticket", 30000)
           and sum(p for _, p in v) / len(v) < t.get("low_profit", 5000)}
    low |= {sku for sku, r in cat.items() if sku not in ours and r.get("list_price", 0) < t.get("low_list", 15000)}
    return low


def hits(c, now, t):
    """HQ 11-04 "잘 팔린 상품만 지속 늘려": every SKU our shop sold lately, stocked to cover its own sales until
    hit_cover windows from now. The rate is the faster of the last 3 h and the last 24 h (per 20-min supply window),
    so a SKU that speeds up gets deeper stock at once and one that slows down is not refilled past its pace.
    Returns [(sku, target units, why)], fastest first."""
    sold = {}
    for hours in (3, 24):
        for sku, n in c.execute("""select s.sku, count(*) from settles s join settle_units x on x.seq = s.seq
                                   join supply_units su on su.unit = x.unit where su.shop = 'dragonash' and s.ts > ?
                                   group by s.sku""", (now - hours * 3600,)):
            sold.setdefault(sku, {})[hours] = n
    out, econ = [], _ours(c, now)
    breaks = dict(c.execute("select sku, count(*) from broken where ts > ? group by sku", (now - 86400,)).fetchall())
    mkt = dict(c.execute("select sku, count(*) from settles where ts > ? group by sku", (now - 86400,)).fetchall())
    for sku, n in sold.items():
        if n.get(24, 0) < t.get("hit_min_24h", 6) and n.get(3, 0) < 3:
            continue                                          # not a hit yet
        per_window = max(n.get(3, 0) / 9, n.get(24, 0) / 72)  # 9 windows in 3 h, 72 in 24 h
        _, tk, pr = econ.get(sku, (0, 0, 0))
        cover = t.get("hit_cover", 4) * (2 if tk >= t.get("hit_big_ticket", 100000) else 1) \
            * (1.5 if tk and pr / tk >= t.get("hit_good_margin", 0.25) else 1)   # HQ 11-04: sells, ticket, margin
        if tk and pr / tk < t.get("hit_min_margin", 0.10):
            continue                                          # HQ 11-10: thin-margin SKUs are not refilled
        brk = breaks.get(sku, 0) / max(1, mkt.get(sku, 0))     # HQ 11-11: the market's 24 h break rate; a refund
        if mkt.get(sku, 0) >= 30 and brk > t.get("hit_max_break", 0.11) and tk and pr / tk < 0.15:   # costs the
            continue                                          # whole price, so breaky thin lines are not refilled
        target = max(2, math.ceil(per_window * cover))
        cost = tk - pr
        if cost > 0:                                          # no single SKU above hit_max_value at cost
            target = min(target, max(2, int(t.get("hit_max_value", 10_000_000) // cost)))
        profit_h = per_window * 3 * pr                        # what this SKU earns us per hour at its pace
        out.append((sku, target, f"hit: sold {n.get(3, 0)} in 3 h, {n.get(24, 0)} in 24 h, ticket {tk:,.0f}, "
                                 f"profit {pr:,.0f}/unit, {profit_h:,.0f}/h -> keep {target}", profit_h))
    # HQ 11-10 "이윤 높고, 객단가 높고, 잘 팔리는 제품 중심으로 매입해서 이익 보전": profit per hour = pace x
    # (price - cost) is all three at once; the cash goes to the best earners first
    return [(a, b, w) for a, b, w, _ in sorted(out, key=lambda x: -x[3])]


def plan(md, c, t, now=None):
    now = now or time.time()
    inv, cat, demand = load(md, "inventory.json", {}), market_model.catalog(md), load(md, "demand.json", [])
    if isinstance(demand, dict):
        demand = demand.get("categories", [])
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    stock = {}
    for u in units:
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
    start = inv.get("start") or 5_000_000
    # audit 11-10: the reserve also covers 3 days of storage (1%/day of stock at cost; 406M -> 12.2M)
    stock_cost = sum(u.get("cost") or 0 for u in units)
    t = dict(t, cash_reserve=max(t["cash_reserve"], 3 * (inv.get("storage_per_day") or 0.01) * stock_cost / start))
    cash = (inv.get("balance") or 0) - t["cash_reserve"] * start
    w = inv.get("restock") or {}                              # spend cap per supply window
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    wid = str(w.get("closes_at") or int(now // 1200))
    spent = st.get("spent", 0) if st.get("window") == wid else 0
    cash = min(cash, t["window_budget"] * start - spent)
    n_mkt = dict(c.execute("select sku, count(*) from settles where ts>? group by sku", (now - 24 * 3600,)).fetchall())
    # 11-10 bug fix: our deals were counted over all time against the market's last 24 h, so a SKU we had sold for a
    # week read as a 100% share -- target = the whole market's rate x 24 h (1,025 bedding sets selling 12 a day).
    # Both now count the same 24 h.
    n_ours = {k: v[0] for k, v in _ours(c, now).items()}    # audit 11-10: ledger settles, not quote "deals" (+17%)
    by_sku, by_cat = market_model.rates(c, now=now)
    heroes = [k for k, _ in sorted(by_sku.items(), key=lambda kv: -kv[1])[:t["heroes"]]]
    buy, why = {}, []

    low = low_value(c, now, t, cat)
    skipped = set()
    # 11-07: 214M of furniture at cost sat in stock at 6% quote conversion (cost above the market's price for what
    # was left: grade A units priced against an all-grade median); no more furniture while the stock is over the cap
    big_stock = sum(u.get("cost") or 0 for u in units if (u.get("list_price") or 0) >= 500000)
    our_rate = market_model.our_sales(c, hours=24)            # per SKU per hour, last 24 h
    # patch 3.4 (11-06): over 50 units of a SKU supplied market-wide in the last hour, its cost rises 0.2% a unit (up
    # to 50%). surge >= 15% (125+ units): not bought; >= 5% (75+): half the quantity. HQ orders are exempt.
    n1h = dict(c.execute("select sku, count(*) from supply_units where ts > ? group by sku", (now - 3600,)).fetchall())

    live = {x.get("sku"): (x.get("factor") or 1) - 1 for x in inv.get("surge") or [] if x.get("sku")}

    def surge(sku):                                           # the market's own factor (inventory.json surge) first,
        if sku in live:                                       # our estimate from supply_units otherwise
            return live[sku]
        return min(0.5, 0.002 * max(0, n1h.get(sku, 0) - 50))

    dear_memo, sold24 = {}, _ours(c, now)
    brk24 = dict(c.execute("select sku, count(*) from broken where ts > ? group by sku", (now - 86400,)).fetchall())

    def dear(sku):                                            # HQ 11-12 "매입가가 비싸졌다면 사지마": what we pay now
        if sku not in dear_memo:                              # leaves too little against what we sell it for.
            rows = [r[0] for r in c.execute(                  # MBT 11-12 (2nd): 6 buys were too few -- a unit's cost
                "select cost from supply_units where shop = 'dragonash' and sku = ? and ts > ? and cost > 0 "
                "order by ts desc limit 20", (sku, now - 3 * 86400))]   # depends on its random grade (sofa G00327e80:
            n, tk, _ = sold24.get(sku, (0, 0, 0))           # last 6 1.436M, last 20 1.246M), and the market median is
            if not rows:                                      # not our price (G9df06998: we sell 28.6k, median 26.3k,
                dear_memo[sku] = False                        # cost 20.8k, 27% margin, was blocked). Our own 24 h price
            elif n >= 5 and tk:                               # when we sold 5+, the median otherwise. Review 11-12:
                net = tk * (1 - brk24.get(sku, 0) / max(n, 1))   # refunds of broken units come off our price, and the
                dear_memo[sku] = sum(rows) / len(rows) > net * (1 - t.get("buy_min_margin", 0.10))   # margin matches hits()
            else:
                med = market_model.market_price(c, sku)[0]
                dear_memo[sku] = bool(med and sum(rows) / len(rows) > med * t.get("buy_cap", 0.93))
        return dear_memo[sku]

    def add(sku, qty, reason, any_price=False):
        nonlocal cash
        r = cat.get(sku)
        if not r or qty <= 0 or (r["list_price"] > t["max_list"] and not any_price):
            return
        if sku in low:                                        # HQ 11-04: low ticket and low profit -- not stocked
            return
        if reason != "HQ order" and sku in set(t.get("restock_hold") or []):   # HQ 11-11: lines that lose per unit
            skipped.add(sku)
            return
        if reason != "HQ order" and dear(sku):                # HQ 11-12 "매입가가 비싸졌다면 사지마"
            skipped.add(sku)
            return
        if reason != "HQ order" and stock.get(sku, 0) > 1 and stock.get(sku, 0) / max(our_rate.get(sku, 0), 1 / 48) >= 48:
            skipped.add(sku)                                  # HQ 11-10: no more of a SKU we hold 48 h+ of our sales of
            return
        if r["list_price"] >= 500000 and reason != "HQ order" and big_stock >= t.get("furniture_stock_cap", 120e6):
            skipped.add(sku)
            return
        # HQ 11-07 "상한은 올리되, 공급가가 급등한 제품은 걸러": skip at +5% or more, half at +2% (was 15% / 5%)
        if reason != "HQ order" and surge(sku) >= t.get("surge_skip", 0.05):
            skipped.add(sku)
            return
        if reason != "HQ order" and surge(sku) >= t.get("surge_half", 0.02):
            qty = max(1, qty // 2)
        qty = min(qty, t["max_qty"] - buy.get(sku, 0))
        if reason != "HQ order":                              # 11-07 furniture opened (max_list 4M): one line holds
            cap = t.get("max_line_value_hit", t.get("max_line_value", 8e6)) if reason.startswith("hit:") \
                else t.get("max_line_value", 8e6)       # HQ 11-11: proven earners (hits) may hold a larger line
            qty = min(qty, max(1, int(cap // (r["list_price"] * t["cost_guess"]))))
        cost = r["list_price"] * t["cost_guess"] * qty
        if qty <= 0 or cost > cash or len(buy) >= t.get("max_skus", 17) and sku not in buy:   # 10 -> 17: a 3-min window fits ~18
                                                                                         # supply calls 10 s apart (week 09)
            return
        buy[sku] = buy.get(sku, 0) + qty
        cash -= cost
        why.append(f"{qty} x {r['name'][:24]} ({reason})")

    if RESERVE.exists():                                      # 1. reservations, the last 30 minutes
        for line in RESERVE.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                rsv = json.loads(line)
            except ValueError:
                continue
            if now - rsv.get("ts", 0) < 3600 and not stock.get(rsv.get("sku")) and rsv.get("sku") not in buy:
                add(rsv["sku"], 1, "reserved")
    regular, wants = market_model.regulars(c, t["regular_min_buys"])          # 1b. what our regulars buy
    have_cat = {(cat.get(s) or {}).get("category") for s in stock}
    for category, skus in sorted(wants.items(), key=lambda kv: -sum(kv[1].values())):
        if category in have_cat:
            continue                                          # a regular coming back finds it in stock already
        sku = max(skus, key=skus.get)
        add(sku, min(t["regular_depth"], sum(skus.values())), f"regulars bought {sum(skus.values())} here")
    if regular:
        why.append(f"{len(regular)} regulars ({sum(regular.values())} buys)")
    try:                                                      # 1c. the marketing team's picks
        import promo
        mk = dict(promo.plan().get("restock") or {}, _veto=promo.plan().get("scm_veto"))   # sales team is on top
    except Exception:
        mk = {}
    general = cash
    cash = min((inv.get("balance") or 0) - t["cash_reserve"] * start, t["pick_window_budget"] * start - spent)
    try:
        vetoed = set(((mk.get("_veto") or {}).get("cancel_orders")) or [])
    except Exception:
        vetoed = set()
    for x in promo.hq_orders():                               # 1b. headquarters' direct orders: first, never vetoed
        add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0), "HQ order", any_price=True)
    for sku, target, why_ in hits(c, now, t):                 # 1b'. our own hits, deepened as they sell (HQ 11-04)
        add(sku, target - stock.get(sku, 0) - buy.get(sku, 0), why_, any_price=True)
    if t.get("profit_first"):                                 # HQ 11-10: only proven earners until profit is back
        cash = min(cash, general)
        return [{"sku": k, "qty": v} for k, v in buy.items()], why + ["profit-first: proven earners only"] + \
            ([f"surge skip: {sorted(skipped)}"] if skipped else []), cash, \
            {"window": wid, "spent": spent + sum(cat[k]["list_price"] * t["cost_guess"] * v for k, v in buy.items())}
    for x in mk.get("skus") or []:
        add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0) - buy.get(x.get("sku"), 0),
            "marketing pick", any_price=True)
    try:                                                      # the SCM team's orders (Codex, hourly)
        import scm_agent
        veto = set((mk.get("_veto") or {}).get("cancel_orders") or [])
        for x in [x for x in scm_agent.scm_plan().get("orders") or [] if x.get("sku") not in veto]:
            add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0) - buy.get(x.get("sku"), 0),
                "scm order", any_price=True)
    except Exception as e:
        why.append(f"scm plan not read: {type(e).__name__}")
    cash = min(cash, general)                                 # the rest of the run keeps the general cap
    try:                                                      # 11-07: a focus plan from 11-04 15:52 had cut the
        fresh = time.time() - promo.PLAN.stat().st_mtime < 3 * 3600   # demand step for 3 days (model teams in usage
    except (OSError, AttributeError, NameError):              # cooldown never rewrote it); share 0.7% -> 0.2%
        fresh = False
    try:                                                      # HQ 11-07 weekend: "공격적으로 운영해" -- HQ's
        hq_open = json.loads((Path(__file__).parent / "private" / "owner_override.json").read_text(  # no_focus wins
            encoding="utf-8")).get("no_focus")                # over a sales-team focus plan
    except (OSError, ValueError):
        hq_open = False
    if mk.get("mode") == "focus" and fresh and not hq_open:                   # marketing: only regulars and its picks for now
        return [{"sku": k, "qty": v} for k, v in buy.items()], why + ["marketing focus mode"] + \
            ([f"surge skip: {sorted(skipped)}"] if skipped else []), cash, \
            {"window": wid, "spent": spent + sum(cat[k]["list_price"] * t["cost_guess"] * v for k, v in buy.items())}
    # 2. demand, by market revenue per hour (rate x list): 85% of market revenue is goods listed 500k+ (11-07: 14.5B
    # of 17.0B in 24 h, our share 0.03%), so with max_skus lines per window the dear SKUs go first
    for sku, rate in sorted(by_sku.items(), key=lambda kv: -kv[1] * (cat.get(kv[0]) or {}).get("list_price", 0)):
        r = cat.get(sku)
        if not r:
            continue
        kind = r.get("kind", "food")
        if kind == "food" and (r.get("shelf_hours") or 999) <= 48 and rate < t["short_shelf_rate"]:
            continue
        if n_mkt.get(sku, 0) < t["min_settles"]:
            continue                                          # too little evidence: the display step covers it
        ours = n_ours.get(sku, 0)
        if sku in vetoed:
            continue                                          # audit 11-10: the sales veto binds this step too
        # audit 11-10: the 0.05 share floor (x2 for heroes) bought 10-25x our sales (G021aae64: 20 units, sells 7/day);
        # a proven SKU uses its real share and never more than 2 days of our own 24 h sales
        share = min(1.0, ours / n_mkt[sku]) if ours >= t["proven_sales"] else t["share_prior"]
        target = math.ceil(rate * share * t["cover_h"].get(kind, 12) * (t["hero_depth"] if sku in heroes else 1))
        if ours >= t["proven_sales"]:
            target = min(target, math.ceil(ours / 24 * 48))
        if kind == "goods" and ours < t["proven_sales"]:
            target = min(target, t["goods_cap_unproven"])
        add(sku, target - stock.get(sku, 0) - buy.get(sku, 0),     # audit: minus what hits already queued
            f"{rate:.2f}/h x share {share:.2f}" + (" hero" if sku in heroes else ""))
    have_cat = {(cat.get(s) or {}).get("category") for s in stock} | {(cat.get(s) or {}).get("category") for s in buy}
    cats = sorted(by_cat, key=lambda k: -by_cat[k]) or [d["category"] for d in demand]
    for category in cats[:t["display_cats"]]:                 # 3. display
        if category in have_cat:
            continue
        med = next((d.get("median_price") for d in demand if d.get("category") == category), None)
        pool = [r for r in cat.values() if r.get("category") == category and r["list_price"] <= t["max_list"]
                and (r.get("kind") == "goods" or (r.get("shelf_hours") or 0) >= 168)]
        if pool:
            r = min(pool, key=lambda r: abs(r["list_price"] - (med or r["list_price"])) - 1e6 * (r["sku"] in by_sku))
            add(r["sku"], 1, "display")
    st = {"window": wid, "spent": spent + sum(cat[k]["list_price"] * t["cost_guess"] * v for k, v in buy.items())}
    if skipped:
        why.append(f"surge skip: {sorted(skipped)}")
    return [{"sku": k, "qty": v} for k, v in buy.items()], why, cash, st


def window(md, now=None):
    """(buy now?, why): the supply window from inventory.json; no "restock" field means no windows."""
    now = now or time.time()
    w = load(md, "inventory.json", {}).get("restock")
    if not isinstance(w, dict):
        return True, "no supply windows announced"
    if w.get("open"):
        return True, f"window open until {time.strftime('%H:%M:%S', time.localtime(w.get('closes_at') or now))}"
    opens = w.get("opens_at") or 0
    if opens - now <= 60:
        return True, f"window opens in {max(0, opens - now):.0f} s"
    return False, f"window closed, opens at {time.strftime('%H:%M:%S', time.localtime(opens))} (in {(opens - now) / 60:.0f} min)"


HARD_FLOOR = 300_000         # HQ 11-04: never go bankrupt (< 500,000 is bankruptcy only with nothing left to sell, and
WORST_COST = 0.75            # a supply leaves stock): even at the worst cost (75% of list) keep 300,000


def only_sellers(c, buy, hours=24):
    """HQ 11-04: buy only what sells. A line stays when the SKU sold 3+ times on the market lately, or once here, or
    buyers asked for it twice where nobody had it (gaps), or a customer asked us for it (reservations)."""
    since = time.time() - hours * 3600
    market = dict(c.execute("select sku, count(*) from settles where ts>? group by sku", (since,)).fetchall())
    ours = dict(c.execute(f"select sku, count(*) from settles where ts>? and seq in ({market_model.ours(c)}) group by sku",
                          (since,)).fetchall())
    wanted = {}
    try:
        for (skus,) in c.execute("select skus from gaps where ts>?", (since,)):
            for x in json.loads(skus or "[]"):
                wanted[x] = wanted.get(x, 0) + 1
    except Exception:
        pass
    asked = set()
    if RESERVE.exists():
        for line in RESERVE.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                asked.add(json.loads(line).get("sku"))
            except ValueError:
                pass
    kept, dropped = [], []
    for x in buy:
        k = x["sku"]
        if market.get(k, 0) >= 3 or ours.get(k) or wanted.get(k, 0) >= 2 or k in asked:
            kept.append(x)
        else:
            dropped.append(k)
    return kept, dropped


def never_bankrupt(md, buy):
    """Drop lines from the end until the run, at the worst cost the market can draw, leaves HARD_FLOOR."""
    try:
        inv = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], len(buy)
    cat = market_model.catalog(md)
    bal, kept = inv.get("balance") or 0, []
    for x in buy:
        worst = (cat.get(x["sku"]) or {}).get("list_price", 0) * WORST_COST * x["qty"]
        if bal - worst >= HARD_FLOOR:
            kept.append(x)
            bal -= worst
    return kept, len(buy) - len(kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    c = market_model.db()
    try:
        market_model.sync(c, md, budget_s=15)
    except Exception as e:
        print(f"[restock] ledger sync failed: {type(e).__name__}: {e}", flush=True)
    try:
        p = pace(c, load(md, "inventory.json", {}))
        print(f"[restock] stock cover {p.get('cover_h')} h ({'calm' if p.get('calm') else 'aggressive'})", flush=True)
    except Exception as e:
        print(f"[restock] pace check failed: {type(e).__name__}: {e}", flush=True)
    buy, why, cash, st = plan(md, c, theta())
    buy, unsold = only_sellers(c, buy)
    if unsold:
        why = [f"not selling, not bought: {', '.join(unsold)}"] + why
    buy, cut = never_bankrupt(md, buy)
    if cut:
        why = [f"bankruptcy guard dropped {cut}"] + why
    buy = by_value(c, buy)                 # the kit buys 10 lines per run, 10 s apart, in a 3-min window: most profit first
    go, when = window(md)
    if not go:
        buy, why = [], [f"waiting: {when}"]
    else:
        why = [when] + why
        try:
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(json.dumps(st), encoding="utf-8")
        except OSError:
            pass
    try:                                   # speak first: the forum runs here too, with no order behind it
        import forum
        said = forum.tick(md, c)
        if said:
            print(f"[forum] {sum('post' in x for x in said)} posts, {sum('vote' in x for x in said)} votes to the outbox",
                  flush=True)
    except Exception as e:
        print(f"[forum] skipped: {type(e).__name__}: {e}", flush=True)
    try:                                   # the Enuri team's book for the buyer: passive, rebuilt every tick_s
        import enuri
        card = enuri.tick(md, c)
        if card is not None:
            acc = enuri.book().get("enuri_accountability") or {}
            print(f"[enuri] book rebuilt: {card.get('deals', 0)} deals scored, mae {card.get('mae')}, "
                  f"missed {acc.get('missed', 0)}, wrong {acc.get('wrong', 0)}, lost {acc.get('lost', 0)} (24 h)", flush=True)
    except Exception as e:
        print(f"[enuri] skipped: {type(e).__name__}: {e}", flush=True)
    print(f"[restock] {len(buy)} lines, cash above reserve after: {cash:,.0f}; " + "; ".join(why[:10]), flush=True)
    print(json.dumps({"buy": buy}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
