"""Where in Round 3 we are. The round ends 2026-11-17 23:59 KST. Round 3 patch 3.4 (11-11) ranks the shop by its
Book - balance minus the start plus unsold stock at cost - so stock keeps its cost value at the close and is never
sold at a loss; the last day lets thin margins close (seller_r3.py reads hours_left()).

11-17: until today the last day also stopped all buying (STOP_ALL_HOURS = 24, food 36 h before the close). That was
a rule for the board that counted cash only (before patch 3.3): under Book a unit bought and not sold is still
worth its cost at 23:59, so stopping only gave up the last day's margin - the shop sold 101 units on 11-17 from
stock with a median age of 170 h. Now buying goes on to the close, narrowed in the last day to what the agent's
own log says still sells (restock.py), and to food whose shelf life outlasts the close (expired food leaves the
Book at its cost)."""
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROUND_END = datetime(2026, 11, 17, 23, 59, tzinfo=KST)
LAST_DAY_HOURS = 24       # restock only what sold here in the last six hours; food only if it outlasts the close
SELL_DOWN_HOURS = 24      # asks at the market price or below, floor at cost


def hours_left(now=None):
    now = now or datetime.now(KST)
    return (ROUND_END - now).total_seconds() / 3600
