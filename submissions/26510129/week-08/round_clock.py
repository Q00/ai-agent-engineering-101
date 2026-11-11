"""Where in Round 3 we are. The round ends 2026-11-17 23:59 KST. Round 3 patch 3.4 (11-11) ranks the shop by its
Book - balance minus the start plus unsold stock at cost - so stock keeps its cost value after the close and is
never sold at a loss; the last day only stops buying (storage, spoilage) and lets thin margins close (restock.py
and seller_r3.py read hours_left()); the nightly tuner is told the same so it does not raise the asks at the end."""
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROUND_END = datetime(2026, 11, 17, 23, 59, tzinfo=KST)
STOP_FOOD_HOURS = 36      # no more food: it would not sell before it spoils or the close
STOP_ALL_HOURS = 24       # no more stock of any kind
SELL_DOWN_HOURS = 24      # asks at the market price or below, floor at cost


def hours_left(now=None):
    now = now or datetime.now(KST)
    return (ROUND_END - now).total_seconds() / 3600
