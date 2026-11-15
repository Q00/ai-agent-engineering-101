"""The limit and the pick read the unit's grade: Saved is the share of the shop's margin kept (Patch 3.6, 11/12).

Patch 3.6 scores a deal (list - price) / (list - cost), cost the shop's for that unit: 1 at or under cost, 0 for a
break the warranty refunded, -1 for one it did not, and Saved is the sum over deals / (deals + 100), over all of
round 3. The cost is drawn by grade, A 55-75%, B 40-60%, C 25-45% of list, so the same share of list kept is a
different score by grade: at 60% of list a grade A unit keeps all of the margin, a B unit .80, a C unit .62.
market_buyer.saved_cap holds every deal to one share of list (0.32) and best_quote ranks by the share of list kept,
which reads a cheap grade C ask as the best deal. Checked against the board with the public ledger (settles with the
grade, supply batches for the cost): 61 buyers' Saved to a mean error of .032, ours .7309 against the board's .7264
(review/W-20261114-0810-AX가게선별/result.md). Our 24 h to 11/14 08:00 by segment: food A .97, goods A .90, food B
.86, food C .74, goods B .70 (11% refunded), goods C .49 (26% refunded), against our Saved .726; a deal moves Saved by
(score - Saved) / (deals + 101), so the last two lower it at any price we were paying.

expected(p, grade, b) is the score a price p of list is expected to keep, (1 - b) E[min(1, (1 - p) / (1 - c))] with
the cost c even over its grade's range (RANGE), plus TIE (1 - p) so a cheaper ask of the same grade always ranks first
(the middle cost alone made every ask under it 1.0: re-review W-20261114-1038 I1); cap() is the price that keeps cfg["target"] of it, list (1 - target (1 - MID) /
(1 - b)), and 0 when the grade cannot reach the target even at cost (goods grade C: 70% survive). The grade is the
one the quote names (buyer_goods_grade.grade: the named grade was the settled one in 2,194 of 2,195 of our deals to
11/14); a quote that names none keeps today's limit and is ranked as grade B. Goods take the grade's chance of
breaking (market_model.unit_break_chance, never under the SKU's), food none.

Only ever lowers the limit (market_buyer takes the min with value_limit and saved_cap), so the budget, the hard
limits, the warranty rules and the cash checks stay where they are; a lower limit can only mean fewer or cheaper deals.

Take (11/15, W-20261115-1330-AX순위개선): with "take": true, the cap is also how far a shop's final ask is taken where
the talk would walk away (market_buyer main: the accept band's top, and the receipt check of the asked SKU). My own
offers stay at the limit as before, so a deal I closed already is not paid more; only a talk that ended on an ask over
my limit and within the cap is closed at that ask. 24 h to 11/15 13:30: 708 goods and 87 food orders ended so (goods
converted 7.7%, food 81%), their last asks keeping .81 of the margin on average against our Saved .739.

Switch: .market/buyer_margin_cap.json {"enabled": true, "target": 0.75} (+ "take": true). Missing, unreadable or anything else is off,
and off is the limit and the pick as before. BUYER_MARGIN_CAP=0 in the environment is off too. Read at every order (a
buyer run is one order), so no restart either way. A talk whose limit the cap lowered (lowered()) has no accept band
and stays out of buyer_shop_profile, so the lower limits do not widen the band of the talks the cap leaves alone.
"""
import json
import math
import os

import buyer_goods_grade
import market_model

SWITCH = "buyer_margin_cap.json"
MID = {"A": 0.65, "B": 0.50, "C": 0.35}       # the middle of each grade's cost range, as a share of list (/rules)
RANGE = {g: (m - 0.10, m + 0.10) for g, m in MID.items()}   # A 55-75%, B 40-60%, C 25-45% of list
TIE = 1e-6                                    # the same score: the cheaper ask first, never a score change that matters
BREAK = {"A": 0.03, "B": 0.115, "C": 0.30}    # goods: the market's rate by grade, until the ledger has its own
TARGET = 0.75
TARGET_MIN, TARGET_MAX = 0.50, 0.95
UNNAMED = "B"                                 # a quote that names no grade is ranked as the commonest one

grade = buyer_goods_grade.grade


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def config(md, env=None):
    """{"target"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_MARGIN_CAP") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    target = d.get("target", TARGET)
    if not _num(target) or not TARGET_MIN <= target <= TARGET_MAX:
        return None
    return {"target": float(target), "take": d.get("take") is True}


def chance(kind, stats, sku, g, sku_chance):
    """The chance a unit of this grade breaks: goods the grade's (never under the SKU's), food 0."""
    if kind != "goods":
        return 0.0
    unit = None
    try:
        unit = market_model.unit_break_chance(stats, sku, g) if stats else None
    except Exception:  # noqa: BLE001 - a ledger it cannot read: the market's rate
        unit = None
    if not _num(unit):
        unit = BREAK.get(g, BREAK[UNNAMED])
    return min(max(unit, sku_chance if _num(sku_chance) else 0.0, 0.0), 1.0)


def expected(p, g, b):
    """The Saved score a price p (share of list) is expected to keep for a unit of grade g that breaks b of the time."""
    lo, hi = RANGE.get(g, RANGE[UNNAMED])
    p = min(max(p, 0.0), 1.0)
    if p <= lo:                         # at or under every cost: all of it
        kept = 1.0
    elif p >= hi:                       # over every cost: (1 - p) / (1 - c), c even over [lo, hi]
        kept = (1 - p) * math.log((1 - lo) / (1 - hi)) / (hi - lo)
    else:                               # under the costs above p, (1 - p) / (1 - c) over the ones below
        kept = ((hi - p) + (1 - p) * math.log((1 - lo) / (1 - p))) / (hi - lo)
    return (1 - min(max(b or 0, 0.0), 1.0)) * kept + TIE * (1 - p)


def cap(cfg, list_price, g, b):
    """The most a unit of grade g may cost and still be expected to keep cfg's target; 0 when no price does.
    None when off, without a grade or a list price: the limit as before."""
    if not cfg or g not in MID or not _num(list_price) or list_price <= 0:
        return None
    b = min(max(b or 0, 0.0), 1.0)
    if b >= 1 or cfg["target"] / (1 - b) > 1:
        return 0
    return int(round(list_price * (1 - cfg["target"] * (1 - MID[g]) / (1 - b)), 6))


def take_top(cfg, cap, limit, most):
    """take on: the most a shop's final ask is taken at where the talk would walk away. The cap is the price that still
    keeps the target, so an ask over my limit and within it is a deal that keeps the target, not a walk. Never over
    most (market_buyer.value_limit: the budget and the deal cap), and None when take is off, the cap is not over the
    limit or a number is missing: the talk as before."""
    if not cfg or cfg.get("take") is not True:
        return None
    if not all(_num(x) for x in (cap, limit, most)) or cap <= limit:
        return None
    top = int(min(cap, most))
    return top if top > limit else None


def note_take(m, note):
    """The orders row's margin_cap records the ask a take closed at ("taken") when the band's turn fired under a take
    ceiling (independent review fcb5fc4 I1: with plan A's band off the row had no other trace of it). m is
    order["_margin_cap"], note buyer_accept_band.turn's note; anything else is left alone."""
    if isinstance(m, dict) and m.get("take_top") and isinstance(note, dict) and _num(note.get("taken")):
        m["taken"] = note["taken"]


def lowered(row):
    """Whether an orders.jsonl row is a talk whose limit the cap lowered: it had no accept band, so the shop profile
    (the band's learning) leaves it out."""
    m = row.get("margin_cap") if isinstance(row, dict) else None
    return isinstance(m, dict) and _num(m.get("cap")) and _num(m.get("limit_before")) and m["cap"] < m["limit_before"]
