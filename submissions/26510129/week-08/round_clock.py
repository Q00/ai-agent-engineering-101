"""Where in Round 3 we are. The round ends 2026-11-17 23:59 KST and stock is worth nothing after that, so the
shop stops buying a day before the close and sells its stock down to any price in the last hours (restock.py and
seller_r3.py read hours_left()); the nightly tuner is told the same so it does not raise the asks at the end."""
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROUND_END = datetime(2026, 11, 17, 23, 59, tzinfo=KST)
STOP_FOOD_HOURS = 36      # no more food: it would not sell before it spoils or the close
STOP_ALL_HOURS = 24       # no more stock of any kind
SELL_DOWN_HOURS = 24      # asks at the market price or below, floor 90% of cost
DUMP_HOURS = 12           # asks under cost, floor 60% of cost
LAST_HOURS = 3            # anything above one credit beats nothing


def hours_left(now=None):
    now = now or datetime.now(KST)
    return (ROUND_END - now).total_seconds() / 3600
