"""Hard limits. Fixed in code: no policy file, department or CEO rule can change them.

Departments read them from here only; policy.json has no keys for them, and policy values outside
POLICY_RANGES are clipped (policy.py). A change here is a code change with its own commit.
"""
from datetime import datetime

# money and stock
CASH_RESERVE = 1_000_000      # restock spends only cash above this; under it the shop case is cash_low
CASH_WARN = 1_000_000         # under this: warning in the log and an urgent alert
STOCK_CAP = 15_000_000         # stock at cost at or over this: overstock, no restock (11/06 16:46: was 5.0M; 15:54: 4.0M -> 5.0M)
STOCK_EXIT = 13_200_000        # overstock ends only under this (hysteresis; 88% of the cap, was 4.4M)
# every change of STOCK_CAP: (epoch seconds, new cap). Comparisons that span a change (the CEO guard, the
# daily before/after section, the 24 h review alert) read it so a cap change is not taken for a policy effect.
STOCK_CAP_CHANGES = [(1793948058, 5_000_000),   # 11/06 15:54 KST, 4.0M -> 5.0M
                     (1793951213, 9_000_000)]   # 11/06 16:46 KST, 5.0M -> 9.0M

# round timing (local time, KST)
ROUND_END = datetime(2026, 11, 17, 23, 59).timestamp()
RESTOCK_STOP_H = 36           # no restock in the last 36 h
# end of round (person's decision 11/07 ~22:00), scheduled only:
#   at CAP_END_AT (11/15 23:59, when the endgame starts) STOCK_CAP goes to CAP_END (depts/cap_brake.py, analyst process,
#   commit + push + Telegram; nothing happens if the cap is already at or under it)
#   from FOOD_TO_END_FROM, food is bought only if its shelf life lasts past ROUND_END (depts/purchasing.py)
#   goods: unchanged until the restock stop (RESTOCK_STOP_H, 11/16 11:59)
CAP_END_AT = datetime(2026, 11, 15, 23, 59).timestamp()
CAP_END = 15_000_000


def _end_rules():
    """11/12 ~16:20 (person), end_rules.json (read when a process imports limits): sell to the end - restock stops
    restock_stop_h (6) before ROUND_END, the scheduled end cap and the automatic cap's end move to the same time;
    depts/sales.py reads endgame_h (12) from it. Food (FOOD_TO_END_FROM) unchanged. Off -> the values above."""
    import json, os
    try:
        path = os.environ.get("W08_END_RULES") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "end_rules.json")
        with open(path, encoding="utf-8") as f:                 # W08_END_RULES: tests only
            r = json.load(f)
    except (OSError, ValueError):
        return {}
    return r if r.get("enabled") else {}


END_RULES = _end_rules()
if END_RULES:
    RESTOCK_STOP_H = float(END_RULES.get("restock_stop_h", 6))
    CAP_END_AT = ROUND_END - RESTOCK_STOP_H * 3600

# automatic STOCK_CAP (person's decision 11/09 ~23:10): while stock_cap_auto.json is enabled (and before CAP_END_AT,
# which keeps priority), the cap every run is cash + stock at cost - AUTO_RESERVE, STOCK_EXIT 88% of it; the fixed
# STOCK_CAP above is the value when it is off (the cap brake's two stock trips turn it off and keep 20M).
# "paused" (set by the cap brake when cash < its cash_min, cleared once cash > resume_cash): no buying, case unchanged.
import json as _json  # noqa: E402
import os as _os  # noqa: E402
AUTO_CAP_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "stock_cap_auto.json")
AUTO_RESERVE = 3_000_000
EXIT_RATIO = 0.88


def auto_cap_rules():
    try:
        with open(AUTO_CAP_PATH, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {"enabled": False}


def auto_cap_on(r=None, now=None):
    import time as _t
    r = auto_cap_rules() if r is None else r
    return bool(r.get("enabled")) and (now or _t.time()) < CAP_END_AT


def stock_cap_now(cash, stock, now=None, r=None):
    """(cap, exit, source): the automatic cap while it is on, else the fixed STOCK_CAP / STOCK_EXIT of this module."""
    r = auto_cap_rules() if r is None else r
    if auto_cap_on(r, now):
        cap = int(cash + stock - r.get("reserve", AUTO_RESERVE))
        return cap, int(cap * r.get("exit_ratio", EXIT_RATIO)), "auto"
    return STOCK_CAP, STOCK_EXIT, "fixed"


def buy_paused(r=None, now=None):
    r = auto_cap_rules() if r is None else r
    return auto_cap_on(r, now) and bool(r.get("paused"))
FOOD_TO_END_FROM = datetime(2026, 11, 15, 23, 59).timestamp()
ENDGAME_MIN = 1.00            # the endgame floor never goes under cost x this (ALL ranks shops by book)

# when the other limits changed (epoch, new value); HIGH_MAX itself lives in depts/qc.py (sales_report.HIGH_MAX)
HIGH_MAX_CHANGES = [(1793951213, 1_000_000)]     # 11/06 16:46 KST, 500,000 -> 1,000,000
CASH_RESERVE_CHANGES = []

# how far an approved analyst proposal may move the hard limits above (person's decision 11/06). Fixed here:
# neither the analyst nor the CEO can change these maxima.
ANALYST_MAX = {"STOCK_CAP": 40_000_000,   # 11/09 ~19:00 person: 30M -> 40M (brake condition 2 needs share AND 3M unsold); 11/07 ~20:40 person: 15M -> 20M (with the cap brake, depts/cap_brake.py); 11/08 ~20:35: -> 30M (brake back to 20M)
                "HIGH_MAX": 3_000_000, "CASH_RESERVE_MIN": 1_000_000}   # 11/11 ~20:10 person: HIGH_MAX max 1.5M -> 3.0M

# market rules for one restock run (agent.md): at most 10 lines, 1-20 units a line
MAX_QTY = 20
MAX_LINES = 10

# the range each CEO policy value may take (policy.py clips into it); keys not listed have no range
POLICY_RANGES = {
    "purchasing.spend_share": (0.2, 0.5),
    "purchasing.high_share": (0.2, 0.85),         # 11/09 ~23:10 person: max 0.7 -> 0.85; 11/09 ~14:35: 0.6 -> 0.7 (high focus); 11/07 ~16:05: max 0.5 -> 0.6 (share 0.4 -> 0.6)
    "purchasing.high_max": (0, 200),              # 11/13 ~00:00 person: 100 -> 200 (high SKU 1 -> 2); 11/09 ~21:20 person: 60 -> 100 (more aggressive); 11/09 ~14:35 person: 40 -> 60 (high focus); 11/09 ~11:00 person: 30 -> 40; 11/06: 5 -> 10; 11/07 ~14:45: 10 -> 20 (units 8 -> 18); ~16:05: -> 30 (units 28); 11/08 ~13:30: -> 60; ~17:00: back to 30
    "purchasing.min_margin": (0.05, 0.5),
    "purchasing.my_share": (0.1, 0.5),
    "purchasing.wanted_qty": (0, 5),
    "purchasing.min_sold_30m": (1, 10),
    "purchasing.food_min_shelf_h": (12, 168),
    "purchasing.max_break": (0.0, 0.3),
    "purchasing.perishable_trial_market_30m": (1, 20),
    "purchasing.perishable_trial_qty": (0, 3),
    "sales.warranty_h": (0, 72),
    "sales.warranty_max_break": (0.0, 0.1),
    "sales.warranty_min_n": (3, 50),
    "logistics.near_expiry_h": (6, 24),
    "logistics.clearance_cut_per_h": (0.05, 0.2),
    "logistics.clearance_max_cut": (0.5, 0.9),
    "logistics.aging_after_h": (12, 72),
    "logistics.aging_step_h": (3, 24),
    "logistics.aging_cut": (0.05, 0.2),
    "logistics.aging_min_cost": (0.9, 1.0),     # aging never under 90% of cost
    "cs.post_every_s": (300, 3600),             # the market allows one post per 5 min per role
    "buyer.top_k": (1, 5),
    "buyer.min_offers": (1, 5),
    "buyer.first_offer": (0.4, 0.9),
}

# dislikes (Patch 3.4, from 11/06 12:00; free-only confirmed 11/06): per hour the first 3 new dislike stances on buyers are free, the k-th
# costs 20,000 x (k - 3) from shop cash (it lowers Book). Our code casts no votes today; if it ever does, it may
# only use the free ones: at most DISLIKE_FREE_PER_HOUR an hour and only while inventory.json's next fee is <= this.
DISLIKE_FREE_PER_HOUR = 3
DISLIKE_MAX_FEE = 0
CASH_RESERVE_CHANGES.append((1794013201, 1_000_000))   # 11/07 10:00 analyst #b2, approved
STOCK_CAP_CHANGES.append((1794030435, 12_000_000))   # 11/07 14:47 analyst #2, approved
STOCK_CAP_CHANGES.append((1794041326, 15_000_000))   # 11/07 17:48 analyst #6, approved
STOCK_CAP_CHANGES.append((1794054851, 20_000_000))   # 11/07 21:34 analyst #7, approved
STOCK_CAP_CHANGES.append((1794090562, 15_000_000))   # 11/08 07:29 analyst #brake, approved
STOCK_CAP_CHANGES.append((1794103348, 20_000_000))   # 11/08 11:02 analyst #13, approved
HIGH_MAX_CHANGES.append((1794112448, 1_500_000))   # 11/08 13:34 analyst #15, approved
STOCK_CAP_CHANGES.append((1794124113, 20_000_000))   # 11/08 16:48 analyst #16, approved
HIGH_MAX_CHANGES.append((1794124912, 1_000_000))   # 11/08 17:01 analyst #17, approved
STOCK_CAP_CHANGES.append((1794138017, 30_000_000))   # 11/08 20:40 analyst #18, approved
STOCK_CAP_CHANGES.append((1794149013, 20_000_000))   # 11/08 23:43 analyst #18, approved
STOCK_CAP_CHANGES.append((1794193505, 30_000_000))   # 11/09 12:05 analyst #33, approved
STOCK_CAP_CHANGES.append((1794217635, 20_000_000))   # 11/09 18:47 analyst #brake, approved
STOCK_CAP_CHANGES.append((1794219768, 40_000_000))   # 11/09 19:22 analyst #38, approved
HIGH_MAX_CHANGES.append((1794226437, 1_500_000))   # 11/09 21:13 analyst #39, approved
STOCK_CAP_CHANGES.append((1794230694, 20_000_000))   # 11/09 22:24 analyst #38, approved
STOCK_CAP_CHANGES.append((1794233530, 48_767_244))   # 11/09 23:12 automatic cap on (cash + stock - 3M; person 11/09 ~23:10)
STOCK_CAP_CHANGES.append((1794279355, 58_042_837))   # 11/10 11:55 automatic cap on again (cash + stock - 3M; person 11/10 ~11:50)
HIGH_MAX_CHANGES.append((1794396910, 3_000_000))   # 11/11 20:35 person's decision (1.5M -> 3.0M, pinned to 11/13 12:00)
HIGH_MAX_CHANGES.append((1794551555, 2_000_000))   # 11/13 person's decision (3.0M -> 2.0M, held units sold as is)
HIGH_MAX_CHANGES.append((1794590142, 1_500_000))   # 11/14 person's decision (2.0M -> 1.5M: over 1.5M 24 h profit 0.79M vs refunds 5.96M; held units sold as is)
STOCK_CAP_CHANGES.append((1794905978, 15_000_000))   # 11/17 17:59 analyst #end, approved
