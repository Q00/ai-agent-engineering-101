"""Checks the round's end (11-13): with policy.END_FLOOR_TO_COST off the floor keeps its margin in the last hours
and restock stops only in the last hour; on, the old slide to the cost and the 6 h stop. No network, no model.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market python tests/end_floor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import policy
import seller

p = policy.defaults()
u = {"cost": 100000, "kind": "goods", "grade": "B", "_no_premium": True}
full = round(100000 * (1 + p["min_margin"]))
good = True


def at(hours_left):
    policy.hours_left = lambda now=None: hours_left
    return seller.floor_price(u, p, False, 150000), policy.restock_stops(hours_left)


for flag, rows in ((False, ((30, full, False), (12, full, False), (3, full, False), (0.5, full, True))),
                   (True, ((30, full, False), (15, 100000 + round((full - 100000) * 0.5), False), (3, 100000, True)))):
    policy.END_FLOOR_TO_COST = flag
    for h, want_floor, want_stop in rows:
        got = at(h)
        ok = got == (want_floor, want_stop)
        print("ok " if ok else "BAD", f"END_FLOOR_TO_COST {flag}, {h} h left: floor {got[0]} (want {want_floor}), restock stops {got[1]} (want {want_stop})")
        good &= ok
print("ALL OK" if good else "FAILED")
