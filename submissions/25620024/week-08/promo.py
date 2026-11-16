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
        rate_ = market_model.our_sales(c, unit["sku"], hours=24)   # audit 11-10: was 6 h
        over = (stock_n / rate_ if rate_ > 0 else float("inf")) >= t.get("overstock_h", 48)
    dear = lp >= t.get("big_follow_list", 500000) or over
    if dear:                                    # 11-07: HQ big_follow is the least undercut on furniture, even for SKUs
        follow = max(follow or 0, t.get("big_follow", 0.02))   # the sales plan follows at 2%
        if t.get("big_ask_over", 0) > 0 and not over:   # MST TF 11-14 benchmark: inextro asks 1.04 x the grade median,
            follow = -t["big_ask_over"]                # nocha12 sells at 1.05; we asked 1.00 (margin 6% vs their 9-16%)    # 11-07: furniture sells at ~0.70-0.74 of list market-wide; our arms (0.80+)
                                              # would never sell the dear SKUs restock now buys -- follow their median
    if follow is not None:
        follow = min(follow, t.get("max_undercut", 0.05))      # finance caps how far under the median we go
    median = None
    if follow is not None:          # 0 means "at the median", not "no follow" (11-04: a 0 cap sent mattresses out at 0.92)
        median, n = market_model.market_price(c, unit["sku"], grade=unit.get("grade") if lp >= 100000 else None,
                                               grade_fallback=GRADE_FALLBACK)
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
        rate = market_model.our_sales(c, unit["sku"], hours=24)    # audit 11-10: was 6 h (night = "inf h")
        cover = stock_n / rate if rate > 0 else float("inf")
        cut = max([m for h, m in t["clear_cover_h"] if cover >= h], default=0)
        if cut:
            price, why = price * (1 - cut), why + [f"{stock_n} in stock, {cover:.0f} h of sales -{cut:.0%}"]
    try:                                                 # the SCM team's clearance markdowns
        import scm_agent
        md = (scm_agent.scm_plan().get("clearance") or {}).get(unit["sku"])
        # MST TF 11-14: the SCM plan was last written 11-10 02:11 and still took 30% off 20 SKUs, new furniture and
        # the expanded kimchi among them (Samsung TV A asked 395k on a 430k cost). A plan older than scm_max_age_h is off.
        if md and time.time() - os.path.getmtime(scm_agent.PLAN) > t.get("scm_max_age_h", 12) * 3600:
            md = None
        if unit["sku"] in ((plan().get("scm_veto") or {}).get("no_clearance") or []):
            md = None                                    # the sales team (top department) vetoed this markdown
    except Exception:
        md = None
    if md:
        price, why = price * (1 - md), why + [f"scm clear -{md:.0%}"]
    # buyer-team audit 11-11: G021aae64 (5 held, 43 unmet in 3 h) went at 363k, under cost 395k and the median 403k;
    # a SKU we hold only a few of needs no group-buy pull
    gb = groupbuy(c, unit["sku"]) if stock_n > t.get("gb_min_stock", 5) else None
    if gb:                                                # HQ 11-05 group buy: this hour's first units
        gbp = lp * gb["share"]
        gmed, _ = market_model.market_price(c, unit["sku"], grade=unit.get("grade") if lp >= 100000 else None)
        if gmed:                                          # audit 11-10: never more than 15% under the grade median
            gbp = max(gbp, gmed * 0.85)
        if gbp < price:
            price, why = gbp, why + [f"group buy {gbp / lp:.0%} of list, {gb['left']} left this hour"]
        arm = "gb"
    tier = next(((n, d) for n, d in t["tiers"] if bought >= n), None)
    if tier:
        price, why = price * (1 - tier[1]), why + [f"member {code} {bought} buys -{tier[1]:.0%}"]
    if vip and t.get("vip_discount"):
        price, why = price * (1 - t["vip_discount"]), why + [f"VIP -{t['vip_discount']:.0%}"]
    if any(w.startswith(("shelf", "in stock", "scm clear")) or " in stock" in w for w in why) and "clear_floor_margin" in t:
        floor = int(cost * (1 + min(t["min_margin"], t["clear_floor_margin"]))) + 1   # clearing may go lower
    if follow is not None and median:   # 11-13 benchmark: follow 0 ("at the median") skipped this and fell to cost + 1
        fm = t["follow_margin"]
        if dear:                                      # 11-07: Book counts unsold stock at cost, so a dear unit whose cost
            fm = min(fm, 0.0 if over else t.get("big_follow_margin", 0.03))   # sits above the market median sells
                                                                              # near cost: Book
        floor = min(floor, int(cost * (1 + fm)) + 1)          # keeps +3%, revenue (0.3) gets the whole price
        if over:                                      # HQ 11-10 "악성재고는 원가 근처로, 또는 원가로 팔아": an
            price = min(price, max(cost * 1.01, median * 0.85))   # overstocked unit asks about its cost, and
            price = max(price, median * 0.85)                     # (audit 11-10) never more than 15% under the
            why = why + [f"overstock: about cost {cost:.0f}"]     # market after the markdowns stacked; floor below
        if dear and median * (1 - follow) < cost:
            # 11-08: a dear unit bought above its grade's market price costs 1% a day to hold (storage), ~9% to the
            # round's end, so it goes at the market price now if that loses at most big_loss_max of cost
            floor = min(floor, int(cost * (1 - t.get("big_loss_max", 0.08))) + 1)
    if over and median and t.get("grade_hedge", True):
        # HQ 11-11 (2) the hedge inside a SKU: cheap B/C units carry the volume at deep discounts; a dear unit (often
        # grade A) asks up to 97% of its own grade's market median, capped at cost + 15% -- still under the market
        hedge = min(median * 0.97, cost * 1.15)
        if hedge > price:
            price, why = hedge, why + [f"grade hedge {hedge:.0f}"]
    if over and t.get("round_end"):
        # HQ 11-11 (4) Book counts unsold stock at cost and storage is 1%/day: selling under cost x (1 - 1% x days
        # left) loses more than holding the unit to the round's end, so no overstocked unit goes under that
        days = max(0.0, (t["round_end"] - time.time()) / 86400)
        hold = int(cost * (1 - 0.01 * days)) + 1
        if hold > floor:
            floor, why = hold, why + [f"hold floor {hold}"]
    nm = unit.get("name") or ""
    if t.get("mattress_sell_vs_hold") and t.get("round_end") and unit.get("kind") == "goods" and "매트리스" in nm \
            and "커버" not in nm and not over:
        # owner 11-11: a mattress bought above its grade's market median sat at cost x 1.12 (G07ec17d5: ask 229k vs
        # median 195k, 1 sale in 3 h while the market sold 56). Sell near the market only when that beats holding:
        # Book after selling = market - cost; after holding to the end = - cost x 1% x days left (storage).
        gmed = median or market_model.market_price(c, unit["sku"], grade=unit.get("grade"))[0]
        if gmed and cost > gmed:
            days = max(0.0, (t["round_end"] - time.time()) / 86400)
            hold = int(cost * (1 - 0.01 * days)) + 1
            target = int(gmed * (1 - t.get("mattress_undercut", 0.02)))
            if target >= hold:
                floor, price = target, min(price, int(gmed))
                why = why + [f"mattress sell>hold: market {target} >= hold {hold}"]
            else:
                why = why + [f"mattress hold>sell: market {target} < hold {hold}"]
    # HQ 11-11 20:30 cycle (stock -> errors -> market -> interviews). Book counts unsold stock at cost, so a unit only
    # needs to sell when the market pays at least its cost; none of these sells a goods unit under its cost.
    gmk = None
    if unit.get("kind") in ("goods", "food") and t.get("market_fix", True):
        gmk = median or market_model.market_price(c, unit["sku"], grade=unit.get("grade") if lp >= 100000 else None,
                                                  grade_fallback=GRADE_FALLBACK)[0]
    if gmk and unit.get("kind") == "goods":
        tgt = int(gmk * (1 - t.get("market_fix_undercut", 0.02)))
        if over and tgt >= cost * 1.05:
            # (1) overstock the market pays >= 5% over cost for: G75f11c19 A sold at 77k (cost 79.6k, others 89k,
            # median 87k) -- 156 of 212 sales under cost in 24 h -- because the near-cost cap and the hold floor let
            # haggling go 6% under cost. Ask 2% under the grade median and never close under cost.
            if tgt > price:
                price, why = tgt, why + [f"overstock market {tgt} (median {gmk:.0f} >= cost+5%)"]
            floor = max(floor, int(cost) + 1)
        elif not over and max(price, floor) > gmk * (1.02 if not (dear and t.get("big_ask_over", 0) > 0)
                                                      else 1.005 + t["big_ask_over"]) and gmk > cost * 1.003:
            # live 21:19: a G30604071 A unit bought at ~227k (median 229,950, 2%-under target 225k) still asked
            # cost x 1.12 = 254k; a unit bought under the median asks max(target, cost) and never closes under cost
            tgt = max(tgt, int(cost) + 1)
            # (2) an ask above a market that still pays our cost: G30604071 A asked cost x 1.12 = 254k against a
            # 229,950 median and fell from ~15 sales an hour to 1 in 3 h (2 deals, 14 misses in 6 h). The ask is
            # max(price, floor): the margin floor, not the price, held it up
            price, why = min(price, tgt), why + [f"market follow {tgt} (median {gmk:.0f}, ask was over it)"]
            floor = min(floor, max(int(cost) + 1, int(tgt * (1 - t["haggle_room"]))))
    if gmk and unit.get("kind") == "food" and unit.get("expires_at") and unit.get("shelf_hours") \
            and max(price, floor) > gmk * 1.02 and floor > gmk * 0.95:
        # (3) food priced over its market by its cost floor dies on the shelf and loses the whole cost: G4a998429 B
        # (cost up to 39.5k, market 30.7k) asked 38-44k, 1 sale and 9 misses in 6 h, 74-107 h of shelf left
        left = (unit["expires_at"] - now) / (unit["shelf_hours"] * 3600)
        if left < t.get("food_fix_left", 0.7):
            tgt = int(gmk * 0.98)
            # MST TF 11-14 audit: 13 food deals in 1 h closed 1-12% under cost (G50d60e61 B cost 39.7k at 36.7k) on
            # lines that sell out long before expiry (39 h of sales vs ~30% shelf left). Under cost only when our
            # stock would not sell in the hours left (cover > food_fix_cover x hours left); otherwise never under cost.
            hrs = (unit["expires_at"] - now) / 3600
            rate_f = market_model.our_sales(c, unit["sku"], hours=24)
            cover = stock_n / rate_f if rate_f > 0 else float("inf")
            risk = cover > hrs * t.get("food_fix_cover", 0.8)
            price, why = min(price, tgt), why + [f"food at market {tgt} ({left:.0%} shelf left, {cover:.0f} h of "
                                                 f"sales vs {hrs:.0f} h; {'expiry risk' if risk else 'sells in time'})"]
            floor = min(floor, int(gmk * 0.9) if risk else max(int(cost) + 1, int(gmk * 0.9)))
    fcap = t.get("food_market_cap")
    if fcap and gmk and unit.get("kind") == "food" and max(price, floor) > gmk * fcap:
        # MST TF 11-14 (3rd): food conversion falls from 65% at 0.95-1.00 x the market median to 19% over 1.10; in 6 h
        # 1,405 food quotes asked >= 1.03 x and 58% of those units cost <= 0.95 x the median -- the 12% margin floor,
        # not the cost, held the ask over the market. Ask the median when cost + food_cap_margin still fits under it.
        tgt, fl = int(gmk * fcap), int(cost * (1 + t.get("food_cap_margin", 0.03))) + 1
        if tgt >= fl:
            price, why = min(price, tgt), why + [f"food cap {tgt} ({fcap:.2f} x median {gmk:.0f})"]
            floor = min(floor, max(fl, int(tgt * (1 - t["haggle_room"]))))
    ladder = t.get("big_cost_ladder")
    if ladder and unit.get("kind") == "goods" and lp >= t.get("big_follow_list", 500000) and unit.get("supplied_at"):
        # MST TF 11-14 (2nd): in 3 h 54 big sales, 85% of revenue, made a gross of 0 and an EV of -0.49M/h. A line's
        # cost is a random draw (ours 0.99 x the market's); a unit drawn over its grade median took the dear path
        # (median, big_loss_max 8% under cost; a new SKU with no sales of ours counts as overstock) and went under cost
        # (sofa G0e4deffc: cost 1,552k, least 1,460k). A fresh big unit now keeps cost x (1 + m) by its hours in stock
        # ([[24, 0.04], [48, 0.01]]: +4% to 24 h, +1% to 48 h); after the last step the rules above clear it as before
        age = (now - unit["supplied_at"]) / 3600
        m = next((m for h, m in sorted(ladder) if age < h), None)
        flex = t.get("big_ladder_flex")
        if m is not None and flex is not None and gmk and m > flex and cost * (1 + flex) < gmk:
            # HQ 11-14 agenda #2 (B, sales team): the +4% step held asks over the median (41% conversion under it,
            # 8% at 1.00-1.03 x); when cost + flex still fits under the grade median, the step is cost + flex
            m, why = flex, why + [f"ladder flex +{flex:.0%} (cost+{flex:.0%} under median {gmk:.0f})"]
        if m is not None and int(cost * (1 + m)) + 1 > floor:
            floor, why = int(cost * (1 + m)) + 1, why + [f"cost ladder {age:.0f} h: floor cost +{m:.0%}"]
    if t.get("slow_clear") and t.get("round_end") and unit.get("kind") in ("goods", "food") and (gmk or median) \
            and (unit.get("kind") == "goods" or (unit.get("expires_at") or 0) > t["round_end"]):
        # HQ 11-14 agenda #2 (C): a line our pace cannot sell before the round ends (cover > hours left) is counted at
        # cost at the end and pays 1%/day storage meanwhile, so selling at the hold line cost x (1 - 1% x days left)
        # loses no Book against holding and adds revenue. Ask max(hold line, slow_clear_at x market median).
        hrs_left = max(0.0, (t["round_end"] - now) / 3600)
        rate_s = market_model.our_sales(c, unit["sku"], hours=24)
        cover_s = stock_n / rate_s if rate_s > 0 else float("inf")
        # HQ 11-15 16:30 (audit: 347 of 489 big quotes in 3 h asked at or over the grade median, the cliff where big
        # goods stop selling): a big unit whose ladder floor reaches 0.99 x its grade median asks max(hold line,
        # cliff_clear_at x median) like a slow line -- the hold line already pays the storage left, so no Book is lost.
        cliff = bool(t.get("cliff_clear") and unit.get("kind") == "goods" and lp >= t.get("big_follow_list", 500000)
                     and gmk and max(price, floor) >= gmk * 0.99)
        if cover_s > hrs_left or cliff:
            hold_s = int(cost * (1 - 0.01 * hrs_left / 24)) + 1
            at_s = t.get("slow_clear_at", 0.97) if cover_s > hrs_left else t.get("cliff_clear_at", 0.99)
            tgt_s = max(hold_s, int((gmk or median) * at_s))
            if tgt_s < price:
                price = tgt_s
            floor = max(hold_s, min(floor, int(tgt_s * (1 - t["haggle_room"]))))
            why = why + [f"slow clear: {cover_s:.0f} h of stock vs {hrs_left:.0f} h left, "
                                        f"ask {tgt_s} floor hold {hold_s}"]
    mc = t.get("median_clear_at")
    # under 100k list the median is all grades together, so an A unit sits above it by nature: those clear only past
    # median_clear_small_over (the audit's 1.2 x line)
    mthr = mc if lp >= 100000 else t.get("median_clear_small_over", 1.2)
    if mc and unit.get("kind") in ("goods", "food") and gmk and price > gmk * mthr:
        # HQ 11-16 15:20 (audit interview, sales + buyer; HQ "원가 지키며 적용"): quotes under the grade median
        # converted 31 of 52, over 1.03 x 10 of 96; 28 lines of 5+ units asked over 1.2 x the median (G0be9ed07: 118
        # quotes, 3 deals in 6 h at 1.34 x). Any unit asking over median_clear_at x its grade median asks that much
        # instead, only while it still clears cost x (1 + median_clear_margin): never under cost.
        mf = int(cost * (1 + t.get("median_clear_margin", 0.01))) + 1
        tgt_m = int(gmk * mc)
        if tgt_m >= mf:
            price = tgt_m
            floor = max(mf, min(floor, int(tgt_m * (1 - t["haggle_room"]))))
            why = why + [f"median clear: ask {tgt_m} ({mc:.2f} x grade median {gmk:.0f}), floor {floor} >= cost + 1%"]
    eg = t.get("endgame_at")
    if eg and now >= eg and unit.get("kind") in ("goods", "food"):
        # HQ 11-14 endgame: Book counts unsold stock at cost, so in the last hours a sale near cost loses no Book and
        # adds revenue (0.3). Ask the lower of today's price and max(cost + endgame_margin, 0.97 x market median);
        # the floor is cost + endgame_margin for every unit (it replaces the ladder, hold and clearance floors).
        egf = int(cost * (1 + t.get("endgame_margin", 0.02))) + 1
        price = min(price, max(egf, int((gmk or median or 0) * 0.97)))
        floor, why = egf, why + [f"endgame: floor cost +{t.get('endgame_margin', 0.02):.0%}"]
    price = max(int(price), floor)
    least = max(int(price * (1 - t["haggle_room"])), floor)
    return {"price": price, "least": least, "arm": arm if isinstance(arm, str) else f"{arm:.2f}", "floor": floor,
            "tier": tier, "group_buy": gb,
            "why": "; ".join(why + [f"exp {e}:{a}" for e, a, _ in exps]), "market_median": median,
            "exps": [(e, a) for e, a, _ in exps], "warranty_goods_h": t.get("warranty_goods_h", 0)}


# a grade with under 10 settles in 24 h prices from the all-grade median times this (G30604071 C, cost ~97k, was priced
# from the 229,950 all-grade median; C trades about 20% under A: G75f11c19 C/A 0.82, G0be9ed07 0.62)
GRADE_FALLBACK = {"A": 1.0, "B": 0.9, "C": 0.8}


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
