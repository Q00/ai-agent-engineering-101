"""Week 08 -- the seller's price tool: floor, opening ask and terms for one unit.

The harness negotiates, but between two numbers this module sets in code:

  floor   the lowest price the shop may accept for this unit right now. It is
          the unit's cost plus a margin while the unit is fresh, and it falls
          as the unit loses value to the shop: food close to its date (thrown
          away at cost when it expires), and everything in the last hours of
          the round (unsold stock counts as a loss of its whole cost). Expected
          warranty refunds are added on top.
  ask     where to open: the list price, pulled toward what buyers in this
          category have actually paid (market_memory), never below the floor.
  terms   the claim (the true grade, always), the certificate (always shown)
          and a warranty for goods when the market's break rate for that grade
          is low enough to be worth carrying.
"""
import os
import time
from datetime import datetime, timezone

import market_memory as mm

ROUND_END = float(os.environ.get("ROUND_END", datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc).timestamp()))
MARGIN = 1.04                  # fresh unit: at least cost + 4 % (11-10: was 1.12; grade A costs ~0.66 of list,
                               # so its floor sat at 0.74, above what buyers pay -- 61 deals from 715 openings)
ASK_CAP = {"A": 0.70, "B": 0.70}   # opening ask / list. Deals per opening, 11-08..11-10: A 35 % at 0.70,
                               # 12 % at 0.75, 2 % at 0.85; B 48 % at 0.70, 25 % at 0.75. C converts ~25 % at
                               # any ask from 0.65 to 0.80, so C keeps the higher ask.
STAR_SHARE = 0.95              # 11-11 02:39 rolled back from 0.90: marginal book/revenue 0.059 (22:39-00:39) and
                               # 0.003 (00:39-02:39), below the 0.08 rule twice; seller margin 9.0 % then 11.9 %
                               # of sales, 3 warranty refunds 1.04M. Deals/visit 0.63 is kept by the stock cap.
                               # 11-10 21:00 volume strategy: was 0.95. Buyers rank shops by what they paid there
                               # before (olstjr: ~0.58 of list, 150 units, 39.6k visits, margin 0.14, stars 4.52).
                               # Open at most at 0.95 x the market's median for this SKU and grade. Ledger, 11-10,
                               # 2,297 reviewed deals: stars 4.84 at 0.8x the median, 4.79 at 0.9x, 4.71 at 1.0x,
                               # 4.57 at 1.2x; shops' stars sit within 4.31-4.46, so +0.05 moves several ranks.
                               # YAHO1212 settled at 1.02x the median. Experiment from 11-10 20:30.
WARRANTY_HOURS = 6             # goods break up to 6 h after the sale
WARRANTY_MAX_RATE = 0.12       # offer a warranty only below this break rate


def hours_left(unit, now=None):
    exp = unit.get("expires_at")
    return None if not exp else (exp - (now or time.time())) / 3600


def floor(unit, mem, now=None):
    now = now or time.time()
    cost = unit.get("cost") or 0
    f = cost * MARGIN
    h = hours_left(unit, now)
    if h is not None:                                      # food: what is it worth if it does not sell soon?
        if h < 6:
            f = cost * 0.3
        elif h < 12:                                       # 11-11 04:39: was h < 24. 545 catalog foods have a
            f = cost * 0.7                                 # 24 h shelf, so they arrived already under cost x 0.7:
                                                           # 02:45-04:39, 124 of 417 deals sold below cost (-218k),
                                                           # 80 of them 24 h rice (Gb2392fa9); 0 expired since 20:35
        elif str(unit.get("grade")) == "C":
            f = cost * 1.0
    to_end = (mm_round_end() - now) / 3600
    # 11-12 13:51: was "under 36 h to the end: cost x 0.85, under 12 h: cost x 0.4", built on the belief that unsold
    # stock is lost. agent.md: "Book = cash + what the unsold units cost" -- a unit still on the shelf at the end
    # keeps its whole cost in Book, so selling it below cost only cuts Book (27.7M of stock at cost on 11-12).
    # Only food that would expire before the end loses its cost; the shelf-life tiers above already handle it.
    # In the last 12 h a unit that keeps its value opens to break-even: revenue up, Book unchanged.
    if to_end < 12 and (h is None or h > to_end):
        f = min(f, cost * 1.0)
    if warranty(unit, mem):
        f += mm.break_rate(mem, unit.get("grade")) * unit.get("list_price", 0) * 0.9
    return max(1, int(round(f)))


def mm_round_end():
    return ROUND_END


def ask(unit, mem, now=None):
    lp = unit.get("list_price") or 0
    ratio = mm.paid_ratio(mem, unit.get("category"), default=0.9)
    a = lp * min(1.0, ratio + 0.08, ASK_CAP.get(str(unit.get("grade")), 1.0))
    med = mm.sku_median(mem, unit.get("sku"), unit.get("grade"))
    if med:
        a = min(a, med * STAR_SHARE)
    return max(floor(unit, mem, now), int(round(a)))


WARRANTY_MAX_LIST = 200_000    # 11-13 00:11: was 500k. 22:15-00:11 refunds 1.12M, all under 500k list: mattress
                               # G021aae64 (list 546k) 431,716 + 405,361, G1515d951 (list 424k) 283,125; Book fell
                               # 0.59M in that hour on +1.1M revenue. 11-12 21:11 (first cap): refunds return the whole price and the unit is gone. Sofa G8ec51935
                               # (list 2.32M) refunded 1,350,139 at 20:52; 11-11 sofas/mattresses refunded 1.04M in
                               # 2 h. The floor adds break_rate(grade) x list x 0.9 (A: 3 %, ~62k on that sofa), but
                               # big furniture breaks far more often than the grade average, so no warranty above this.


def warranty(unit, mem):
    if unit.get("kind") != "goods" or str(unit.get("grade")) == "C":
        return 0
    if (unit.get("list_price") or 0) > WARRANTY_MAX_LIST:
        return 0
    return WARRANTY_HOURS if mm.break_rate(mem, unit.get("grade")) <= WARRANTY_MAX_RATE else 0


def describe(unit, now=None):
    """What the shop tells the buyer about the unit: true, and checkable against the certificate."""
    g = str(unit.get("grade"))
    if unit.get("kind") == "food":
        h = hours_left(unit, now)
        return f"grade {g}, {h:.0f} h of shelf life left, market certificate attached" if h is not None \
            else f"grade {g}, certificate attached"
    words = {"A": "new (A)", "B": "box damaged (B)", "C": "refurbished (C)"}.get(g, f"grade {g}")
    return f"{words}, market certificate attached"
