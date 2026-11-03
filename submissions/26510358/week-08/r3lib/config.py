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
MAX_STOCK_COST = 3_000_000          # cost value of stock on hand we allow at most
EXPLORE_SHARE = 0.25                # share of MAX_STOCK_COST for coverage of categories with no evidence yet
EXPLORE_MAX_LIST = 60_000           # coverage buys only SKUs at or below this list price
STOP_BUYING_HOURS = 72              # no restock in the last 72 h of the round
CLEARANCE_HOURS = 36                # in the last 36 h, prices fall toward a salvage floor
DEMAND_HALF_LIFE_H = 8              # recent settles weigh more
HORIZON_H = 12                      # stock for the sales we expect in the next 12 h
MIN_FOOD_SHELF_FOR_EXPLORE = 720    # coverage only with long-life food (no expiry inside the round)

# --- shop: pricing ---------------------------------------------------------------------------------
START_ASK_RATIO = 0.90              # first ask as a share of list price, before memory has prices
MIN_MARGIN = 0.08                   # normal floor: cost * (1 + MIN_MARGIN)
GRADE_FACTOR = {"food": {"A": 1.0, "B": 0.95, "C": 0.82}, "goods": {"A": 1.0, "B": 0.90, "C": 0.75}}
WARRANTY_HOURS = 6                  # goods break up to 6 h after the sale (agent.md)
WARRANTY_MAX_EXPECTED_COST = 0.5    # offer a warranty while p_break * price stays under half the margin

# --- forum (pro-activeness, see initiative.py) -----------------------------------------------------
FORUM_MIN_GAP_MIN = 20              # never two buyer posts closer than this (the kit allows one per 5 min)
FORUM_MAX_CHARS = 800
REACT_MAX_STEPS = 6
REACT_BUDGET_S = 240                # one ReAct run, all steps together
RESTOCK_REVIEW_EVERY_MIN = 30       # background ReAct review of the restock plan
ADJUST_TTL_H = 2                    # a review's scaling expires unless renewed
ADJUST_MIN, ADJUST_MAX = 0.0, 1.5   # clamp on a category's demand target scaling

# --- buyer -----------------------------------------------------------------------------------------
MIN_SURPLUS_RATIO = 0.05            # never pay more than 95% of list price
FIRST_OFFER_RATIO = 0.55            # first offer as a share of list price
MAX_OFFERS = 6
SHOPS_TO_TRY = 3
HOURS_MARGIN = 2                    # food must last use_within_hours + this


def hours_left_in_round(now=None):
    import time
    return (ROUND_END - (now or time.time())) / 3600
