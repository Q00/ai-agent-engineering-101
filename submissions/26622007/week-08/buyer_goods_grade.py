"""Goods: price a unit by its grade's chance of breaking, not its SKU's (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md §5).

saved_cap and value_limit take market_model.break_chance, one rate per SKU over every grade sold. The grade is most of
the risk (market 48 h to 11/09 07:44: grade C broke 31.7%, B 11.3%, A 2.8%), so a grade C unit passes with an A-like
cap: our last 12 h grade C goods kept .242 of list on average against .356 for every deal, at a .407 discount, because
41% broke and a broken unit is refunded and keeps 0.

Arm B takes max(SKU chance, market_model.unit_break_chance(stats, sku, grade)) when the chosen shop's quote names one
grade, so saved_cap's list (1 - 0.30 / (1 - b)) asks a deeper discount of a grade B or C unit (the short warranty cap
reads the grade the same way: "grade C", "Grade C", one grade only). The chance never goes down, so a grade A unit is
priced as today. Arm A keeps the SKU chance. Both arms are recorded in orders.jsonl ("goods_grade"), with the chance B
uses and arm A's limit (limit_a), so the eligible set is the same in both; the record's break_chance is the chance the
limit used (arm B: the grade's). buyer_shop_profile and buyer_rank_pg leave out arm B talks whose limit went down
(lowered()), so the lowered limits set neither a shop's accept band nor its rank for both arms (independent reviews
d85a9e9 I1, 8ca80f5 I1). Only the limit moves, and a lower limit can only mean fewer or cheaper deals. Until the
goods-ladder 809 I3 follow-up (main 11/09 14:58) the buyer memory's trades carried no arm, so arm B's cheaper deals
reached the prices rule_choose, gem_names, my_avg_price, overpriced and item_stats read for both arms, and the shop
chosen next could move with them; from that fix the trade carries trade_mark and priced leaves arm B's lowered deals
out, and hide_unmarked those written before it, without writing the memory again.

Eligible: a round 3 goods order with an id, the chosen shop's quote naming one grade, a SKU chance (enough goods sold),
and no short warranty expected (buyer_short_warranty: its own cap with the grade's chance). Everything else is
untouched and unrecorded. Disjoint from buyer_food_ladder and buyer_food_shelf (food only); buyer_quote_deadline is on
for all goods (both arms). The goods no-fit A/B (buyer_rank_pg, nofit-ab-goods-fixed-0025) ended 11/09 13:29 with its
switch set to nofit false, which leaves rk["nofit"] false on every order, so it is no condition here.

Switch: .market/buyer_goods_grade.json {"enabled": true, "study": "<id>", "salt": "<salt>", "start": <epoch>,
"end": <epoch>}: on only from start until end (the study's ON time and its registry maximum), so arm B stops at end
even if nobody turns the switch off (goods-ladder review 809 I1, same shape). Missing, unreadable, without the window
or anything else is off, and off is the current limit exactly. BUYER_GOODS_GRADE=0 in the environment is off too. Read
at every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import math
import os
import re
import time

import market_model

SWITCH = "buyer_goods_grade.json"
GRADE = re.compile(r"(?i)\bgrade ([abc])\b")      # market_buyer.short_cap's reading of a quote
# the first study's trades before the mark was written into them (see hide_unmarked)
UNMARKED_STUDY = "goods-grade-b-1033"
UNMARKED_FROM = "2026-11-09T13:52:05"     # its switch went on at 13:52:05.458; trade times are local, to the second
_HIDDEN = {"orders": frozenset()}           # order ids hide_unmarked set for this run


def _epoch(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None   # Infinity: no stop


def _switch(md):
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def config(md, env=None, now=None):
    """{"study", "salt", "start", "end"} with the switch on and now in [start, end), else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_GOODS_GRADE") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    d = _switch(md)
    if d is None or d.get("enabled") is not True:
        return None
    study, salt, start, end = d.get("study"), d.get("salt"), _epoch(d.get("start")), _epoch(d.get("end"))
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    now = time.time() if now is None else now
    if start is None or end is None or not start <= now < end:
        return None
    return {"study": study, "salt": salt, "start": start, "end": end}


def arm(cfg, order_id):
    """"A" or "B" for an order id under the study's salt; None without a study or an id."""
    if not cfg or not isinstance(order_id, str) or not order_id:
        return None
    return "B" if hashlib.sha256(f"{cfg['salt']}:{order_id}".encode("utf-8", "surrogatepass")).digest()[0] % 2 else "A"


def grade(text):
    """The one grade a quote names ("grade C"), else None (none named, or more than one)."""
    found = {g.upper() for g in GRADE.findall(text or "")}
    return found.pop() if len(found) == 1 else None


def lowered(row):
    """Whether an orders.jsonl row is an arm B talk whose limit this study lowered: the shop learning that both arms
    read leaves it out."""
    g = row.get("goods_grade") if isinstance(row, dict) else None
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(g, dict) and g.get("arm") == "B" and num(g.get("limit_a")) and num(row.get("limit")) \
        and row["limit"] < g["limit_a"]


def _p(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1


def adjust(md, order, q, stats, sku, chance, sw_h, env=None, now=None):
    """(chance for this order's limit, record or None). Never raises: any failure is the SKU chance, unrecorded."""
    try:
        order = order or {}
        if order.get("kind") != "goods" or sw_h is not None or not stats or not _p(chance):
            return chance, None
        g = grade((q or {}).get("text"))
        if g is None:
            return chance, None
        cfg = config(md, env, now)
        a = arm(cfg, order.get("order"))
        if a is None:
            return chance, None
        unit = market_model.unit_break_chance(stats, sku, g)
        if not _p(unit):
            return chance, None
        b = max(chance, unit)
        return (b if a == "B" else chance), {"study": cfg["study"], "arm": a, "grade": g, "sku_chance": round(chance, 4),
                                             "grade_chance": round(unit, 4), "raised": unit > chance}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return chance, None


def trade_mark(order, limit):
    """What market_buyer writes into the order's trade: {"arm", "lowered"} (lowered: arm B's limit under arm A's, as
    lowered() reads an orders.jsonl row), None outside the study."""
    g = (order or {}).get("_goods_grade")
    if not isinstance(g, dict) or g.get("arm") not in ("A", "B"):
        return None
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    return {"arm": g["arm"], "lowered": g["arm"] == "B" and num(limit) and num(g.get("limit_a")) and limit < g["limit_a"]}


def hide_unmarked(md, trades):
    """Order ids of the trades written from 13:52:05 without a mark (before trades carried it) that may be arm B: by
    the first study's salt, read from the switch even when it is off; when it does not read, every such trade. The
    memory is not written again: priced leaves them out for this run. Set once a run, after the memory loads.
    Conservative: a hashed-B trade is hidden whether or not its limit went down, food and other goods of that time
    with it; keep the 1033 salt in the switch file after the study ends."""
    out = set()
    try:
        d = _switch(md) if md else None
        salt = d.get("salt") if d and d.get("study") == UNMARKED_STUDY and isinstance(d.get("salt"), str) else None
        for t in trades or []:
            if not isinstance(t, dict) or "goods_grade" in t or not isinstance(t.get("order"), str) or not t["order"] \
                    or not isinstance(t.get("time"), str) or t["time"] < UNMARKED_FROM:
                continue
            if salt is None or arm({"salt": salt}, t["order"]) == "B":
                out.add(t["order"])
    except Exception:  # noqa: BLE001 - a run that cannot tell keeps the memory as it reads
        out = set()
    _HIDDEN["orders"] = frozenset(out)
    return _HIDDEN["orders"]


def priced(trades):
    """The trades whose prices arm A may learn from: without arm B's lowered deals (marked, or hidden)."""
    hidden = _HIDDEN["orders"]
    return [t for t in trades if (not (isinstance(t["goods_grade"], dict) and t["goods_grade"].get("lowered"))
                                  if "goods_grade" in t                         # a marked trade: its mark
                                  else not (hidden and t.get("order") in hidden))]
