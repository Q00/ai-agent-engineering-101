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
    # follow (take the leaders' best sellers): ask a little under the market's 24 h median for SKUs the plan lists,
    # never below cost x (1 + follow_margin)
    "follow_margin": 0.12,
}


OVERRIDE = Path(os.environ.get("OWNER_OVERRIDE", Path(__file__).parent / "private" / "owner_override.json"))
PLAN = Path(os.environ.get("MARKETING_PLAN", Path(__file__).parent / "private" / "marketing_plan.json"))
HQ_ORDERS = Path(__file__).parent / "private" / "hq_orders.json"


def hq_orders():
    """Headquarters' (the owner's) direct orders, above every team and never vetoed: {"until", "skus": [{sku, qty,
    follow}]}; [] once "until" has passed."""
    try:
        h = json.loads(HQ_ORDERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return h.get("skus") or [] if h.get("until", 0) > time.time() else []


def plan():
    """The marketing team's current plan (marketing_agent.py writes it after its gate), or {}."""
    try:
        p = json.loads(PLAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p = {}
    hq = {x["sku"]: x["follow"] for x in hq_orders() if x.get("follow") is not None}
    if hq:                                     # HQ's follow prices win over the plan's
        p["follow_skus"] = {**(p.get("follow_skus") or {}), **hq}
    return p


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
    try:                                       # the finance team's limits bind the sales plan
        import finance_agent
        fl = finance_agent.limits()
    except Exception:
        fl = {}
    if fl:
        t["min_margin"] = max(t.get("min_margin", 0), fl.get("min_margin", 0))
        t["warranty_goods_h"] = min(t.get("warranty_goods_h", 0), fl.get("max_warranty_h", 6))
        t["max_undercut"] = fl.get("max_undercut", 0.05)
    try:                                       # headquarters (the owner) overrides any plan
        t.update(json.loads(OVERRIDE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
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
    try:                                                 # the sales team's A/B experiments for this SKU
        import experiments
        exps = experiments.overrides(unit["sku"], unit.get("kind"))
    except Exception:
        exps = []
    for _, _, o in exps:
        for k in ("haggle_room", "min_margin", "warranty_goods_h"):
            if k in o:
                t[k] = o[k]
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
    follow = (plan().get("follow_skus") or {}).get(unit["sku"])
    for _, _, o in exps:
        if "price_share" in o:
            arm, price, why = o["price_share"], lp * o["price_share"], why + [f"experiment price_share {o['price_share']}"]
        if "undercut" in o:
            follow = o["undercut"]
    # 11-10: overstock -- a SKU we hold more than 48 h of our own sales of (a share bug bought 1,025 bedding sets that
    # sell 12 a day) follows the market like furniture: 4% under its grade median, down to 8% under cost
    over = False
    if stock_n > 1 and unit.get("kind") == "goods":
        rate_ = market_model.our_sales(c, unit["sku"])
        over = (stock_n / rate_ if rate_ > 0 else float("inf")) >= t.get("overstock_h", 48)
    dear = lp >= t.get("big_follow_list", 500000) or over
    if dear:                                    # 11-07: HQ big_follow is the least undercut on furniture, even for SKUs
        follow = max(follow or 0, t.get("big_follow", 0.02))   # the sales plan follows at 2%    # 11-07: furniture sells at ~0.70-0.74 of list market-wide; our arms (0.80+)
                                              # would never sell the dear SKUs restock now buys -- follow their median
    if follow is not None:
        follow = min(follow, t.get("max_undercut", 0.05))      # finance caps how far under the median we go
    median = None
    if follow is not None:          # 0 means "at the median", not "no follow" (11-04: a 0 cap sent mattresses out at 0.92)
        median, n = market_model.market_price(c, unit["sku"], grade=unit.get("grade") if lp >= 100000 else None)
        if median:
            price, why = min(price, median * (1 - follow)), why + [f"follow: {follow:.0%} under the 24 h {'grade ' + unit.get('grade') + ' ' if lp >= 100000 and unit.get('grade') else ''}median {median:.0f} (n={n})"]
    low = market_model.market_low(c, unit["sku"], me)
    if low and not exclusive and not follow and t.get("hero_undercut", True) and is_hero(c, unit["sku"], t["heroes"]):
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
    try:                                                 # the SCM team's clearance markdowns
        import scm_agent
        md = (scm_agent.scm_plan().get("clearance") or {}).get(unit["sku"])
        if unit["sku"] in ((plan().get("scm_veto") or {}).get("no_clearance") or []):
            md = None                                    # the sales team (top department) vetoed this markdown
    except Exception:
        md = None
    if md:
        price, why = price * (1 - md), why + [f"scm clear -{md:.0%}"]
    gb = groupbuy(c, unit["sku"])
    if gb:                                                # HQ 11-05 group buy: this hour's first units
        if lp * gb["share"] < price:
            price, why = lp * gb["share"], why + [f"group buy {gb['share']:.0%} of list, {gb['left']} left this hour"]
        arm = "gb"
    tier = next(((n, d) for n, d in t["tiers"] if bought >= n), None)
    if tier:
        price, why = price * (1 - tier[1]), why + [f"member {code} {bought} buys -{tier[1]:.0%}"]
    if vip and t.get("vip_discount"):
        price, why = price * (1 - t["vip_discount"]), why + [f"VIP -{t['vip_discount']:.0%}"]
    if any(w.startswith(("shelf", "in stock", "scm clear")) or " in stock" in w for w in why) and "clear_floor_margin" in t:
        floor = int(cost * (1 + min(t["min_margin"], t["clear_floor_margin"]))) + 1   # clearing may go lower
    if follow and median:
        fm = t["follow_margin"]
        if dear:                                      # 11-07: Book counts unsold stock at cost, so a dear unit whose cost
            fm = min(fm, 0.0 if over else t.get("big_follow_margin", 0.03))   # sits above the market median sells
                                                                              # near cost: Book
        floor = min(floor, int(cost * (1 + fm)) + 1)          # keeps +3%, revenue (0.3) gets the whole price
        if over:                                      # HQ 11-10 "악성재고는 원가 근처로, 또는 원가로 팔아": an
            price = min(price, max(cost * 1.01, median * 0.85))   # overstocked unit asks about its cost (never more
            why = why + [f"overstock: about cost {cost:.0f}"]     # than 15% under the market), floor below
        if dear and median * (1 - follow) < cost:
            # 11-08: a dear unit bought above its grade's market price costs 1% a day to hold (storage), ~9% to the
            # round's end, so it goes at the market price now if that loses at most big_loss_max of cost
            floor = min(floor, int(cost * (1 - t.get("big_loss_max", 0.08))) + 1)
    price = max(int(price), floor)
    least = max(int(price * (1 - t["haggle_room"])), floor)
    return {"price": price, "least": least, "arm": arm if isinstance(arm, str) else f"{arm:.2f}", "floor": floor,
            "tier": tier, "group_buy": gb,
            "why": "; ".join(why + [f"exp {e}:{a}" for e, a, _ in exps]), "market_median": median,
            "exps": [(e, a) for e, a, _ in exps], "warranty_goods_h": t.get("warranty_goods_h", 0)}


GROUPBUY = Path(__file__).parent / "private" / "groupbuy.json"


def groupbuy(c, sku, now=None):
    """HQ 11-05 group buy (the W09 lecture's "선착순 50명만 50% 할인"): a SKU in private/groupbuy.json sells at `share`
    of list to the first `per_hour` buyers each clock hour, then at the regular price. Returns
    {"share", "per_hour", "left", "price_share"} while units are left this hour, else None."""
    now = now or time.time()
    try:
        g = json.loads(GROUPBUY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if g.get("until", 0) < now or sku not in (g.get("skus") or {}):
        return None
    x = g["skus"][sku]
    hour = now - now % 3600
    sold = c.execute("select count(*) from quotes where sku=? and outcome='deal' and ts>=? and arm like 'gb%'",
                     (sku, hour)).fetchone()[0]
    left = int(x["per_hour"]) - sold
    return {"share": x["share"], "per_hour": int(x["per_hour"]), "left": left} if left > 0 else None


def record(c, task, unit, q, price, outcome):
    if q.get("exps"):
        import experiments
        for e, a in q["exps"]:
            experiments.log(c, task, e, a, unit["sku"], price or q["price"], unit.get("cost"), outcome)
    c.execute("insert into quotes values (?,?,?,?,?,?,?,?,?,?,?)",
              (time.time(), task, unit["sku"], unit.get("kind"), unit.get("grade"), q["arm"], unit["list_price"],
               unit["cost"], q["price"], price, outcome))
    c.commit()


def count_purchase(c, code, price):
    now = time.time()
    c.execute("insert into members values (?,1,?,?,?) on conflict(code) do update set purchases=purchases+1, "
              "spent=spent+excluded.spent, last=excluded.last", (code, price, now, now))
    c.commit()
