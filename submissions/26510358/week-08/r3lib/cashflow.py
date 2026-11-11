"""Adaptive stock cap: put idle cash into stock while it sells, pull back when it does not.

Until 2026-11-08 the stock cap was a fixed number raised by hand (8M -> 16M -> 25M -> 60M), and each time it sat at
the cap for most of a day while 100M+ of balance stayed idle. Book counts unsold stock at cost (patch 3.3), so cash
held as cash earns nothing; stock that does not sell costs storage (1%/day) and, for food, spoilage.

Once per CAP_STEP_MIN the cap moves on two signals from our own shop:
  grow   x CAP_GROW    stock is at the cap AND (buyers were turned away for lack of stock OR it sells through)
  shrink x CAP_SHRINK  what the market supplied to us in the last CAP_SELL_HOURS sold under CAP_SLOW_SELL
  hold   otherwise
and stays inside [CAP_MIN, CAP_CEILING_SHARE x balance]; the balance ceiling wins over CAP_MIN, so stock can never
spend the shop's cash down. The premium (800k+ goods) budget is PREMIUM_SHARE of the cap, on top of it: as stock grows
the balance falls, so with the ceiling at 0.60 (0.40 until 2026-11-11) stock settles near half of book at most.
"""
import time

from r3lib import config


def _sell_rate(mem, hours):
    st = mem.sell_through(hours=hours)
    received = sum(d.get("received") or 0 for d in st.values())
    sold = sum(d.get("sold") or 0 for d in st.values())
    return (sold / received if received else None), received


def caps(mem, balance, stock_cost, now=None, dry_run=False, premium_cost=0):
    """-> (stock cap, premium budget, premium max units, why). stock_cost: non-premium stock, the cap's own.
    premium_cost: premium stock on hand at cost. The ceiling is a share of balance + premium_cost: until 2026-11-11 it
    was a share of the balance alone, so every premium buy lowered it (104M -> 90M in the first hour of the larger
    premium budget) and food and normal goods got no restock line while their stock sat over the shrunken cap."""
    now = now or time.time()
    st = mem.get("cash_cap") or {}
    cap = float(st.get("cap") or config.STOCK_BUDGET_MAX)
    ceiling = max(config.MAX_STOCK_COST,
                  config.CAP_CEILING_SHARE * (max(0, balance or 0) + max(0, premium_cost or 0)))
    why = {"cap_before": int(cap), "ceiling": int(ceiling), "stock_cost": int(stock_cost)}
    if now - (st.get("ts") or 0) >= config.CAP_STEP_MIN * 60:
        rate, received = _sell_rate(mem, config.CAP_SELL_HOURS)
        turned = sum(mem.stockout_rates(now=now).values())
        at_cap = stock_cost >= config.CAP_AT_SHARE * cap
        if at_cap and (turned >= config.CAP_TURNED_PER_H or (rate is not None and rate >= config.CAP_FAST_SELL)):
            cap, move = cap * config.CAP_GROW, "grow"
        elif rate is not None and received >= config.CAP_MIN_RECEIVED and rate < config.CAP_SLOW_SELL:
            cap, move = cap * config.CAP_SHRINK, "shrink"
        else:
            move = "hold"
        cap = min(max(cap, config.CAP_MIN), ceiling)
        why.update(move=move, sell_rate=None if rate is None else round(rate, 2), received=received,
                   turned_per_h=round(turned, 2), at_cap=at_cap)
        if not dry_run:
            mem.put("cash_cap", {"cap": cap, "ts": now, **why})
    cap = min(max(cap, config.CAP_MIN), ceiling)
    prem = config.PREMIUM_SHARE * cap
    units = max(1, int(prem // config.PREMIUM_UNIT_COST))
    why.update(cap=int(cap), premium_budget=int(prem), premium_units=units)
    return int(cap), int(prem), units, why
