"""Week 08 — promo.py: the shop's promotion engine. Code decides every price; the sales text only says it.
Nothing here goes into a model prompt, so a buyer cannot talk the rules out of the shop.

The ladder, from the unit and what the shop remembers:
  floor     unit cost x (1 + min_margin): never below it
  instant   list price x an arm the bandit picks per (kind, grade)
  hero      the SKUs selling fastest: one credit under the lowest price another shop settled at lately
  member    "the more you buy, the cheaper it gets": a member code from an earlier deal gives
            repeat (2+ purchases) or volume (10+) prices
  aging     food losing shelf life and goods held long step down toward the floor
  reserve   a SKU we do not stock: quoted after it arrives (restock picks up the reservation)

The arms are learned by Thompson sampling: each quote is a trial, a deal a success worth its margin.
"""
import hashlib
import json
import os
import random
import time
from pathlib import Path

import market_model

THETA = Path(os.environ.get("SHOP_THETA", Path(__file__).parent / "private" / "shop_theta.json"))
DEFAULTS = {
    "min_margin": 0.08,                      # floor = cost x 1.08
    "arms": [0.80, 0.85, 0.90, 0.95],        # instant price as a share of list
    "haggle_room": 0.06,                     # how far below the ask a counter may close
    "tiers": [[10, 0.10], [5, 0.06], [2, 0.03]],   # purchases with a member code -> extra discount
    "heroes": 8,                             # SKUs priced lowest in the market
    "food_markdown": [[0.45, 0.05], [0.25, 0.12], [0.10, 0.25]],   # shelf life share left -> markdown
    "goods_age_h": [[48, 0.05], [96, 0.10]],                        # hours in stock -> markdown
    "warranty_goods_h": 0,                   # warranty offered on goods (0 = none)
    # opening offer (the owner's call, 2026-11-03 21:20): win buyers' memories and reviews while the market forms,
    # at prices most shops will not match, but never below cost (cost x 1.03); ends by itself
    "intro_until": 1793794500,               # 2026-11-04 21:15 KST
    "intro_arms": [0.60, 0.65, 0.70, 0.75],
    "intro_margin": 0.03,
    "intro_haggle": 0.03,
    # clearance: hours our stock of a SKU would last at our own sales rate -> extra markdown (floor still holds)
    "clear_cover_h": [[24, 0.05], [48, 0.10]],
}


PLAN = Path(os.environ.get("MARKETING_PLAN", Path(__file__).parent / "private" / "marketing_plan.json"))


def plan():
    """The marketing team's current plan (marketing_agent.py writes it after its gate), or {}."""
    try:
        return json.loads(PLAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def theta():
    """DEFAULTS, then the owner's private shop_theta.json, then the marketing plan's pricing (latest wins)."""
    t = dict(DEFAULTS)
    try:
        t.update(json.loads(THETA.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    pr = plan().get("pricing") or {}
    if pr.get("price_band"):
        lo, hi = pr["price_band"]
        t["arms"] = [round(lo + (hi - lo) * i / 3, 3) for i in range(4)]
    for k in ("min_margin", "clear_floor_margin", "haggle_room", "hero_undercut", "warranty_goods_h", "vip_discount"):
        if k in pr:
            t[k] = pr[k]
    return t


def member(c, code):
    row = c.execute("select purchases, spent from members where code=?", (code,)).fetchone() if code else None
    return row or (0, 0)


def new_code(task_id):
    return "DRG-" + hashlib.sha256(task_id.encode()).hexdigest()[:6].upper()    # buyers quote it back in any case


def pick_arm(c, kind, grade, arms, explore=True):
    """Thompson sampling: for each arm, draw a deal rate from Beta(deals+1, misses+1), weight by its margin.
    explore=False takes the mean instead of a draw: members are not experimented on, so their price only falls."""
    best, best_v = arms[-1], -1
    for a in arms:
        row = c.execute("select sum(outcome='deal'), sum(outcome!='deal') from quotes where kind=? and grade=? and arm=?",
                        (kind, grade, f"{a:.2f}")).fetchone()
        wins, misses = row[0] or 0, row[1] or 0
        v = (random.betavariate(wins + 1, misses + 1) if explore else (wins + 1) / (wins + misses + 2)) * a
        if v > best_v:
            best, best_v = a, v
    return best


def is_hero(c, sku, n):
    by_sku, _ = market_model.rates(c)
    return sku in [k for k, _ in sorted(by_sku.items(), key=lambda kv: -kv[1])[:n]]


def quote(c, unit, code=None, me=None, now=None, stock_n=1, vip=False):
    """The price for this unit, the lowest price a counter may close at, and why (for the log, never the buyer).
    stock_n is how many units of this SKU we hold (for clearance)."""
    t, now = theta(), now or time.time()
    intro = now < t["intro_until"] and not (plan().get("pricing") or {}).get("price_band")   # a plan wins
    if intro:
        t = {**t, "arms": t["intro_arms"], "min_margin": t["intro_margin"], "haggle_room": t["intro_haggle"]}
    lp, cost = unit["list_price"], unit["cost"]
    floor = int(cost * (1 + t["min_margin"])) + 1
    grade = unit.get("grade") or "?"
    bought = member(c, code)[0]
    exclusive = (plan().get("exclusive_skus") or {}).get(unit["sku"])
    if exclusive:                                        # the marketing team found few competitors holding it
        arm = exclusive
        price, why = lp * arm, [f"exclusive arm {arm:.2f}"]
    else:
        arm = pick_arm(c, unit.get("kind"), grade, t["arms"], explore=not bought)
        price, why = lp * arm, [f"{'intro ' if intro else ''}arm {arm:.2f}"]
    low = market_model.market_low(c, unit["sku"], me)
    if low and not exclusive and t.get("hero_undercut", True) and is_hero(c, unit["sku"], t["heroes"]):
        price, why = min(price, low - 1), why + [f"hero under {low}"]
    if unit.get("kind") == "food" and unit.get("expires_at") and unit.get("shelf_hours"):
        left = (unit["expires_at"] - now) / (unit["shelf_hours"] * 3600)
        cut = next((m for share, m in sorted(t["food_markdown"]) if left <= share), 0)
        if cut:
            price, why = price * (1 - cut), why + [f"shelf {left:.0%} left -{cut:.0%}"]
    elif unit.get("kind") == "goods" and unit.get("supplied_at"):
        age = (now - unit["supplied_at"]) / 3600
        cut = max([m for h, m in t["goods_age_h"] if age >= h], default=0)
        if cut:
            price, why = price * (1 - cut), why + [f"{age:.0f} h in stock -{cut:.0%}"]
    if stock_n > 1:
        rate = market_model.our_sales(c, unit["sku"])
        cover = stock_n / rate if rate > 0 else float("inf")
        cut = max([m for h, m in t["clear_cover_h"] if cover >= h], default=0)
        if cut:
            price, why = price * (1 - cut), why + [f"{stock_n} in stock, {cover:.0f} h of sales -{cut:.0%}"]
    tier = next(((n, d) for n, d in t["tiers"] if bought >= n), None)
    if tier:
        price, why = price * (1 - tier[1]), why + [f"member {code} {bought} buys -{tier[1]:.0%}"]
    if vip and t.get("vip_discount"):
        price, why = price * (1 - t["vip_discount"]), why + [f"VIP -{t['vip_discount']:.0%}"]
    if any(w.startswith(("shelf", "in stock")) or " in stock" in w for w in why) and "clear_floor_margin" in t:
        floor = int(cost * (1 + min(t["min_margin"], t["clear_floor_margin"]))) + 1   # clearing may go lower
    price = max(int(price), floor)
    least = max(int(price * (1 - t["haggle_room"])), floor)
    return {"price": price, "least": least, "arm": f"{arm:.2f}", "floor": floor, "tier": tier, "why": "; ".join(why)}


def record(c, task, unit, q, price, outcome):
    c.execute("insert into quotes values (?,?,?,?,?,?,?,?,?,?,?)",
              (time.time(), task, unit["sku"], unit.get("kind"), unit.get("grade"), q["arm"], unit["list_price"],
               unit["cost"], q["price"], price, outcome))
    c.commit()


def count_purchase(c, code, price):
    now = time.time()
    c.execute("insert into members values (?,1,?,?,?) on conflict(code) do update set purchases=purchases+1, "
              "spent=spent+excluded.spent, last=excluded.last", (code, price, now, now))
    c.commit()
