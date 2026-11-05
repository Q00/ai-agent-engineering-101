"""Paths and strategy constants for Round 3. Every tunable number lives here so REPORT.md can cite one place."""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent              # the agent folder (week-08)
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ROOT / ".market"))
STATE_DIR = MARKET_DIR / "r3"                              # private runtime state (gitignored with .market/)
DB_PATH = STATE_DIR / "memory.sqlite"
LOG_DIR = ROOT / "logs" / "r3"                             # decision logs we submit as evidence
MARKET_URL = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")

SHOP_NAME = os.environ.get("R3_SHOP_NAME", "Ssadassa Shop")   # receipt "shop" field and card name
MY_NAMES = {"myeonggi", "SUNGMYEONGGI", "Ssadassa Shop"}      # how our agents appear in the ledger

KST = timezone(timedelta(hours=9))
ROUND_END = datetime.fromisoformat(os.environ.get("R3_END", "2026-11-17T23:59:00+09:00")).timestamp()

# --- shop: capital and restock --------------------------------------------------------------------
START_CAPITAL = 5_000_000
CASH_RESERVE = 1_200_000            # never let a restock take the balance below this
MAX_STOCK_COST = 3_000_000          # stock budget floor, and the base of the coverage (explore) budget
STOCK_BUDGET_SHARE = 0.40           # stock on hand may cost up to this share of the balance (2026-11-04: balance
STOCK_BUDGET_MAX = 16_000_000       #   15.8M held 1.6M of stock while mattress EV said 20 units and the cap said 3)
                                    # 8M -> 16M 2026-11-05: stock sat at the 8M cap from 11-04 18:00 with balance 57M,
                                    #   sofa turned away 8-15 req/h; 12 h market 7.75B, our share 1.4%, top shops buy 450-880M
EXPLORE_SHARE = 0.25                # share of MAX_STOCK_COST for coverage of categories with no evidence yet
EXPLORE_MAX_LIST = 60_000           # coverage buys only SKUs at or below this list price
RESTOCK_LEAD_S = 60                 # emit a buy list only when supply is open or opens within this (2026-11-04:
                                    #   505 supply calls refused with 429 vs 634 bought; the kit skips a line after 90 s)
STOCKOUT_HOURS = 6                  # requests our shop turned away for lack of stock, counted over this window,
                                    #   are demand addressed to us: added to the category's expected sales per hour
STOP_BUYING_HOURS = 72              # no restock in the last 72 h of the round
CLEARANCE_HOURS = 36                # in the last 36 h, prices fall toward a salvage floor
DEMAND_HALF_LIFE_H = 8              # recent settles weigh more
HORIZON_H = 12                      # stock for the sales we expect in the next 12 h
MIN_FOOD_SHELF_FOR_EXPLORE = 720    # coverage only with long-life food (no expiry inside the round)

# --- shop: expected-value restock (pricing.ev_quantity) -------------------------------------------
CATEGORY_MAX_SHARE = 0.25           # one category's stock cost stays under this share of the stock budget
DEEP_CATEGORY_RATE = 100            # market settles per hour above which a category is deep (many buyers)
DEEP_CATEGORY_SHARE = 0.35          # a deep category may take this share: concentration risk is lower there
EV_COST_RATIO = 0.62                # expected unit cost / list price when the grade is not known yet (A-C mix)
EV_DEFAULT_PRICE_RATIO = 0.75       # expected sale price / list price without market evidence
GOODS_END_RISK = 0.10               # share of cost an unsold goods unit is expected to lose (clearance, round end)
FOOD_LEFTOVER_RISK = 0.15           # same for long-life food that does not sell in the horizon

# --- shop: premium pilot (2026-11-04): goods listed at 800k+ sell 36/h across only 9 shops at 0.80 of list,
#     we sold none. A separate small budget, so a slow pilot cannot starve the main stock.
PREMIUM_MIN_LIST = 800_000          # a goods SKU at or above this list price is premium
PREMIUM_BUDGET = 8_000_000          # worst-case cost of premium stock on hand, outside the normal stock budget
PREMIUM_MAX_UNITS = 10              # premium units on hand at most (2.5M/3 until 2026-11-05: 98 sold in 24 h, 0 held)
PREMIUM_HOURS = 12                  # market sales window for picking premium SKUs
PREMIUM_MIN_SOLD = 10               # a premium SKU needs this many market sales in the window

# --- shop: competitor scout (r3lib/scout.py) ------------------------------------------------------
SCOUT_MIN_SOLD = 20                 # a category needs this many market settles in 6 h before the scout has an opinion
SCOUT_LOW_PRESSURE = 0.6            # competitors restock under 60% of what sells: a gap
SCOUT_HIGH_PRESSURE = 1.4           # competitors restock 140% of what sells: crowded
GAP_MAX, GAP_MIN = 1.3, 0.7         # demand-target scaling at the two ends
SCOUT_MIN_OUR_SOLD = 5              # our realised share replaces the 1/(1+shops) prior only on this many own sales
SCARCITY_PREMIUM = 0.03             # first ask +3% of list in an under-supplied category

# --- shop: pricing ---------------------------------------------------------------------------------
START_ASK_RATIO = 0.90              # first ask as a share of list price, before memory has prices
ASK_ROOM = 0.07                     # first ask = market ratio + this (0.05 until 2026-11-04: our asks sat ~5 pts under
                                    #   the market; profit per visit 0.052 at ask 0.70-0.75 vs 0.072 at 0.75-0.80)
                                    # 0.09 -> 0.07 2026-11-05: 2 h close rate under 0.5 for ~9 h overnight (low 0.26)
MIN_MARGIN = 0.08                   # normal floor: cost * (1 + MIN_MARGIN)
GRADE_FACTOR = {"food": {"A": 1.0, "B": 0.95, "C": 0.82}, "goods": {"A": 1.0, "B": 0.90, "C": 0.75}}
WARRANTY_HOURS = 6                  # goods break up to 6 h after the sale (agent.md)
WARRANTY_MAX_EXPECTED_COST = 0.5    # offer a warranty while p_break * price stays under half the margin
WARRANTY_GRADES = ("A", "B")        # never on C: 27% of C goods broke market-wide (A 2.4%, B 9.1%, 2026-11-04)
WARRANTY_PRICE_IN = True            # a warranty unit's ask carries its expected refund (p_break x ask), like a paid
                                    #   warranty; refunds were 4.2% of our stock cost vs 0.8% at dae-yangkim
BREAK_MIN_N = 200                   # use the grade x category break rate once it rests on this many goods sales

# --- shop: repeat customers and review defence ----------------------------------------------------
LOYALTY_ENABLED = False             # off since 2026-11-04 16:40: 332 codes issued, 0 quoted. Buyer agents start fresh
                                    #   on every order and never see an earlier deal's message; the 3% also ran
                                    #   against the price raise. Code and the members table stay as evidence.
LOYALTY_DISCOUNT = 0.03             # a quoted member code takes 3% off the first ask (the floor does not move)
LOYALTY_PREFIX = "SSD-"
HARSH_MIN_REVIEWS = 15              # a buyer style needs this many star ratings of our deals before we judge it
HARSH_MAX_STARS = 3.3               # styles averaging at or below this are declined (2026-11-04: one buyer left 64
HARSH_EVERY_MIN = 10                #   3-star reviews with an ad for its own shop in each)

# --- organisation (r3lib/org): departments over internal signed A2A --------------------------------
ORG_A2A = True                      # route the restock cycle through the internal A2A server (falls back in-process)
KPI_EVERY_MIN = 30                  # ceo.kpi snapshot to logs/r3/kpi.jsonl

# --- forum (pro-activeness, see initiative.py) -----------------------------------------------------
FORUM_MIN_GAP_MIN = 20              # never two buyer posts closer than this (the kit allows one per 5 min)
FORUM_MAX_CHARS = 800
REPORT_GAP_MIN = 10                 # report lane: one fact post per forum run at most (the kit allows one per 5 min)
REPORT_DEDUPE_H = 6                 # never report the same SKU, category or purchase twice within this
REPORT_ORDER = ("purchase", "sku", "category")   # first-hand facts first, then market-wide numbers
REPORT_PAUSE_DISLIKE = 0.5          # pause the lane while this share of counted votes on our posts are dislikes
REPORT_MAX_MANIPULATION = 0.5       # Jev self-check: drop our own draft when P(manipulation) reaches this
DECISION_MAX_PER_HOUR = 60          # decision-model calls per rolling hour (price unpublished)
DECISION_RESERVE = 10               # of those, kept for priority calls (the self-check of our own posts)
INJECTION_P = 0.9                   # downvote only when the decision model is this sure (honest report scored 0.78)
REACT_MAX_STEPS = 6
REACT_BUDGET_S = 240                # one ReAct run, all steps together
RESTOCK_REVIEW_EVERY_MIN = 30       # background ReAct review of the restock plan
ADJUST_TTL_H = 2                    # a review's scaling expires unless renewed
ADJUST_MIN, ADJUST_MAX = 0.0, 1.5   # clamp on a category's demand target scaling
PROTECT_SELL_THROUGH = 0.9          # a review may not scale a category below 1 while we sell this share of what we
                                    #   receive, or while buyers are turned away for lack of stock (2026-11-04: the
                                    #   review halved mattresses, kimchi, fruit and bedding on a wrong sell-through)

BUYER_GRADES = ["A", "B", "C"]     # goods keep full value unless they break; max_pay already prices the break risk

# --- buyer -----------------------------------------------------------------------------------------
MIN_SURPLUS_RATIO = 0.05            # never pay more than 95% of list price
FIRST_OFFER_RATIO = 0.55            # first offer as a share of list price
MAX_OFFERS = 6
SHOPS_TO_TRY = 3
HOURS_MARGIN = 0.25                 # food must last use_within_hours + this (2 h refused 8 deals worth paying)


def stock_budget(balance):
    """Cost of stock on hand we allow: STOCK_BUDGET_SHARE of the balance, between MAX_STOCK_COST and STOCK_BUDGET_MAX."""
    return int(min(STOCK_BUDGET_MAX, max(MAX_STOCK_COST, STOCK_BUDGET_SHARE * (balance or 0))))


def hours_left_in_round(now=None):
    import time
    return (ROUND_END - (now or time.time())) / 3600
