"""Checks the seller's Thompson sampling offline: one category, a made-up demand curve, 300 talks.

    MARKET_DIR=<empty folder> python bandit_sim.py

Buyers here take a deal less often the higher we open (other shops sit near 0.80 of list). The bandit is
not told this curve; it should end up opening mostly with the arm of the highest expected profit.
"""
import collections
import random

import seller_r3 as s

s.log = lambda m: None
random.seed(1)
u = {"unit": "u", "category": "X>cat", "list_price": 100000, "cost": 50000, "kind": "goods", "grade": "A",
     "supplied_at": 0}
pdeal = lambda share: max(0.02, min(0.95, 0.9 - 4.0 * (share - 0.72)))
base = 0.85
for arm in s.ARMS:
    sh = base * arm
    print(f"arm {arm}: share {sh:.3f}, deal chance {pdeal(sh):.2f}, "
          f"expected profit {pdeal(sh) * (100000 * sh * s.TYPICAL_CLOSE - 50000):.0f}")
picks = collections.Counter()
for t in range(300):
    arm, _ = s.pick_arm(u, base, 52500)
    if t >= 200:
        picks[arm] += 1
    s.learn("X>cat", arm, random.random() < pdeal(base * arm))
print("last 100 talks picked:", dict(sorted(picks.items())))
print("counts [deals, no deal]:", s.BANDIT["cats"]["X>cat"])
