"""The shop's selling floor: one rule set per shop case (strategy.py decides the case).

  normal      max(cost, market average), or the aging markdown when lower
  cash_low    cost
  overstock   cost (x the overstock floor variant), or the aging markdown when lower
  endgame     the endgame floor (cost); no aging, since ALL ranks by book and an unsold unit keeps its cost
  always      food clearance when lower                       (an expired unit is worth nothing)

  aging       non-food on the shelf over AGING_AFTER_H: the average minus AGING_CUT of it every AGING_STEP_H,
              never below AGING_MIN_COST x cost                (storage costs 1%/day)
  clearance   food with under NEAR_EXPIRY_H of shelf life: the average minus CUT_PER_H of it every hour,
              down to (1 - MAX_CUT) of it; may go below cost
  endgame     from ENDGAME_H before the round ends (11/17 23:59): cost, minus ENDGAME_CUT of cost every
              ENDGAME_STEP_H, down to ENDGAME_MIN of cost (1.00: the endgame floor is the cost itself)
"""
import json
import os
import time

import policy
from depts.logistics import held_cost, load_inventory  # noqa: F401  (pricing.held_cost etc.)
from datetime import datetime

NEAR_EXPIRY_H = 12      # food with less shelf life than this is "near expiry" (12 h left -> avg, 3 h left -> 10%)
CUT_PER_H = 0.10
MAX_CUT = 0.90
AGING_AFTER_H = 24      # non-food older than this starts its markdown
AGING_STEP_H = 6
AGING_CUT = 0.10
AGING_MIN_COST = 0.90   # aging never goes below 90% of cost
from limits import END_RULES, ENDGAME_MIN, RESTOCK_STOP_H, ROUND_END  # noqa: E402,F401  hard limits (limits.py)
ENDGAME_H = float(END_RULES.get("endgame_h", 12)) if END_RULES else 48   # 11/12 ~16:20 (person): 12 h with end_rules.json
ENDGAME_STEP_H = 6
ENDGAME_CUT = 0.10


LOSS_RULE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "loss_rule.json")


def loss_rule():
    """11/12 ~14:40 (person), loss_rule.json (read on every call): below cost only by the stoploss -
      aging stops at cost; the overstock floor variant never under cost (food clearance and the endgame unchanged)
      stoploss share = days left to the round end x per_day (1%): cost at most that share above the market
      average -> sold at the average (high price and sofas too: a sofa floor above the average with cost above it);
      more above it: never under cost. The endgame (11/15 23:59 on) keeps its own rules first."""
    try:
        with open(LOSS_RULE, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return {}
    return r if r.get("enabled") else {}


def stoploss_share(now=None, r=None):
    """The share cost may be above the market average for the stoploss: days left x per_day while loss_rule.json is
    on, else the old 2 days of 1% storage."""
    r = loss_rule() if r is None else r
    if not r:
        return STOPLOSS_DAYS * STORAGE_PER_DAY
    return max(0.0, (ROUND_END - (now or time.time())) / 86400) * float(r.get("per_day", 0.01))


def within_stoploss(cost, avg, now=None):
    """loss_rule.json on: cost above the average by at most stoploss_share (of the average). None while off."""
    r = loss_rule()
    if not r or not avg:
        return None
    return avg < cost <= avg * (1 + stoploss_share(now, r))


def clearance(u, avg, now=None):
    """(floor, hours_left) for a unit near expiry, else (None, hours_left)."""
    NEAR_EXPIRY_H, CUT_PER_H, MAX_CUT = policy.values("logistics", "near_expiry_h", "clearance_cut_per_h",
                                                     "clearance_max_cut")
    if not u.get("expires_at"):
        return None, None
    left = (u["expires_at"] - (now or time.time())) / 3600
    if left >= NEAR_EXPIRY_H:
        return None, left
    base = avg if avg is not None else u["list_price"] * 0.7
    cut = min(MAX_CUT, CUT_PER_H * int(NEAR_EXPIRY_H - left))
    return int(base * (1 - cut)), left


def aging(u, avg, now=None):
    """(floor, step) for non-food on the shelf over AGING_AFTER_H, else (None, None). Step 0 = the average."""
    AGING_AFTER_H, AGING_STEP_H, AGING_CUT, AGING_MIN_COST = policy.values(
        "logistics", "aging_after_h", "aging_step_h", "aging_cut", "aging_min_cost")
    if u.get("kind") == "food" or u.get("expires_at") or not u.get("supplied_at"):
        return None, None
    age_h = ((now or time.time()) - u["supplied_at"]) / 3600
    if age_h < AGING_AFTER_H:
        return None, None
    step = int((age_h - AGING_AFTER_H) // AGING_STEP_H)
    base = avg if avg is not None else u["list_price"] * 0.7
    if loss_rule():                                   # 11/12 ~14:40 (person): aging stops at cost
        AGING_MIN_COST = max(1.0, AGING_MIN_COST)
    return max(int(u["cost"] * AGING_MIN_COST), int(base * (1 - AGING_CUT * step))), step


def endgame_step(now=None):
    """None before the endgame, else 0, 1, 2, ... (one step every ENDGAME_STEP_H)."""
    t = (now or time.time()) - (ROUND_END - ENDGAME_H * 3600)
    return None if t < 0 else int(t // (ENDGAME_STEP_H * 3600))


def endgame(u, now=None):
    step = endgame_step(now)
    if step is None:
        return None, None
    return int(u["cost"] * max(ENDGAME_MIN, 1 - ENDGAME_CUT * step)), step


def restock_stopped(now=None):
    return (now or time.time()) >= ROUND_END - RESTOCK_STOP_H * 3600


def floor(u, avg, case, now=None, overstock_mult=1.0):
    """(floor, rule) for one unit in the given shop case: the lowest of the rules that case turns on."""
    now = now or time.time()
    if case != "endgame":                            # sofas: their own floor (depts/sofa.py B), all cases but endgame
        from depts import sofa
        if sofa.applies(u):
            r = sofa.floor(u, now)
            if r:                                    # 11/12 ~14:40 (person): sofa floor above the average, cost above it
                s = stoploss(u, avg, case, now) if loss_rule() and avg and r[0] > avg else None
                return (s, "stoploss") if s is not None else r     # too: the stoploss rule like any high unit
    vol = None
    if case != "endgame":                            # every category: volume pricing (depts/volume.py), when on
        from depts import volume
        vol = volume.floor(u, now)
    if vol:
        cands = [vol]
    elif case == "endgame":
        e, step = endgame(u, now)
        cands = [(e, f"endgame step {step}")] if e is not None else [(u["cost"], "cost")]
    elif case == "cash_low":
        cands = [(u["cost"], "cash_low")]
    elif case == "overstock":                        # 11/12 ~14:40 (person): never under cost (loss_rule.json)
        cands = [(int(u["cost"] * (max(1.0, overstock_mult) if loss_rule() else overstock_mult)), "overstock")]
    else:
        cands = [(max(u["cost"], int(avg)), "normal") if avg is not None else (u["cost"], "cost")]
    if case in ("normal", "overstock"):              # endgame: no aging below cost (ALL uses book)
        a, step = aging(u, avg, now)
        if a is not None:
            cands.append((a, f"aging step {step}"))
    c, _ = clearance(u, avg, now)
    if c is not None:
        cands.append((c, "clearance"))
    s = stoploss(u, avg, case, now)
    if s is not None:
        cands.append((s, "stoploss"))
    return min(cands)


STORAGE_PER_DAY = 0.01      # goods pay 1% of their cost a day in storage (the market's rule); food pays none
STOPLOSS_DAYS = 2


def stoploss(u, avg, case, now=None):
    """The market average as the floor of a unit under water (person's decision 11/07 ~18:20), not in the endgame
    (book counts stock at cost there):
      goods  cost > average and cost - average < STOPLOSS_DAYS days of storage: a small loss now beats paying
             storage while it waits
      food   cost > average and near expiry (policy logistics.near_expiry_h): sold at the market before it is thrown
             away at cost (the clearance markdown then goes lower)"""
    if case == "endgame" or avg is None or u.get("cost", 0) <= avg:
        return None
    if u.get("kind") == "food" or u.get("expires_at"):
        near = policy.get("logistics", "near_expiry_h") or NEAR_EXPIRY_H
        left = ((u.get("expires_at") or 0) - (now or time.time())) / 3600
        return int(avg) if u.get("expires_at") and left < near else None
    w = within_stoploss(u["cost"], avg, now)          # 11/12 ~14:40: days left x 1% while loss_rule.json is on
    if w is not None:
        return int(avg) if w else None
    return int(avg) if u["cost"] - avg < STOPLOSS_DAYS * STORAGE_PER_DAY * u["cost"] else None


WARRANTY_H = 48
WARRANTY_MAX_BREAK = 0.05
WARRANTY_MIN_N = 5          # the SKU needs this many market sales before its break rate counts


def warranty_hours(u, case, stats):
    """The rules below (_rule_hours), plus 11/13 ~10:40 (person): a high-price A grade unit of a low-break SKU
    (depts/a3_warranty.py, list recomputed hourly) gets its 48 h when the rules below give none."""
    h = _rule_hours(u, case, stats)
    if h:
        return h
    from depts import a3_warranty
    return a3_warranty.hours(u, case) or h


def _rule_hours(u, case, stats):
    """[16] 48 h for A-grade goods whose market break rate is known and at most 5%, in the normal case only."""
    import policy
    WARRANTY_H, WARRANTY_MAX_BREAK, WARRANTY_MIN_N = policy.values("sales", "warranty_h", "warranty_max_break",
                                                                   "warranty_min_n")
    from depts import top_high                       # 11/11 ~20:10: cost > 1.5M: A grade in a category with market
    h = top_high.warranty(u, case, WARRANTY_H, stats)   # break <= 6% only; B / C none (before the sofa / B rules)
    if h is not None:
        return h
    from depts import sofa
    if case != "endgame" and sofa.applies(u):        # sofas: 72 h while sofa_rules.json says warranty (C)
        return sofa.warranty_for(u)                  # 11/09: none on a C grade sofa
    if not policy.get("sales", "warranty_enabled"):
        return 0
    from depts import b_warranty                     # 11/09 ~11:30: B grade high-price (TV / 냉방, 침대/매트리스 trial)
    h = b_warranty.hours(u, case, WARRANTY_H)
    if h is not None:
        return h
    if case != "normal" or u.get("kind") == "food" or u.get("grade") != "A":
        return 0
    if (stats.get("sku_n") or {}).get(u["sku"], 0) < WARRANTY_MIN_N:
        return 0
    if (stats.get("sku_break") or {}).get(u["sku"], 0) > WARRANTY_MAX_BREAK:
        return 0
    return WARRANTY_H


def first_ask(u, avg, reserve, rule, deal):
    """normal: market average x the variant (1.00 / 1.05 / 1.10), at most the list price;
    any markdown: the market average (the seller never asks more during a markdown); never below the floor."""
    if rule == "sofa":                               # sofas: the market median (depts/sofa.py A)
        from depts import sofa
        return sofa.first_ask(u, reserve)
    if rule == "volume":                             # every category: the market median (depts/volume.py)
        from depts import volume
        return volume.first_ask(u, reserve)
    base = avg if avg is not None else u["list_price"] * 0.9
    if rule == "normal" and deal["key"] == "shop_normal_ask":
        ask = min(u["list_price"], base * float(deal["variant"]))
    else:
        ask = min(u["list_price"], base)
    return max(reserve, int(ask))
