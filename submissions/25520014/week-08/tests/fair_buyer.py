"""Checks seller.fair_lift (11-13): for the buyer whose first message is FAIR_BUYER the ask is at least FAIR_ASK x
list and its bid, min(0.90 x ask, 0.70 x list), closes when it is at or over the floor. No network, no model.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market python tests/fair_buyer.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seller

STEPS = 4
good = True


def talk(ask, floor, lp=1000000, cost=600000, fair=True):
    o = {"unit": {"list_price": lp, "cost": cost}, "ask": ask, "floor": floor, "step": 0}
    if fair:
        seller.fair_lift(o, STEPS)
    first = seller.next_move(o, None, STEPS, True, False)
    bid = round(min(0.90 * first["price"], 0.70 * lp))
    return first, bid, seller.next_move(o, bid, STEPS, False, False)


for label, args, want in (
        ("A at the market's price: asked 0.78, the 0.70 bid closes", dict(ask=728000, floor=665000), (780000, 700000, True, 700000)),
        ("without the rule: the 0.655 bid is countered", dict(ask=728000, floor=665000, fair=False), (728000, 655200, False, None)),
        ("ask already over 0.78: kept, the bid closes", dict(ask=900000, floor=665000), (900000, 700000, True, 700000)),
        ("a dear A, floor 0.74: the bid does not close, the floor is the final price", dict(ask=745000, floor=740000, cost=735000), (780000, 700000, False, 740000)),
        ("a cheap item: ask rounds up, never over the list", dict(ask=700, floor=600, lp=1000, cost=500), (780, 700, True, 700))):
    first, bid, m = talk(**args)
    got = (first["price"], bid, m["close"], m["price"] if m["close"] or args.get("fair", True) else None)
    ok = got == want and (not first["close"])
    print("ok " if ok else "BAD", label, "| first ask, bid, closed, price:", got, "want", want)
    good &= ok
ok = bool(seller.FAIR_BUYER.search("I want to buy G5b96e0fd. Please quote a price in credits.")) and not seller.FAIR_BUYER.search(
    "I want to buy G2c023a8c. Please quote this exact SKU, certificate, grade and warranty hours.")
print("ok " if ok else "BAD", "the first message is recognised, another buyer's is not")
good &= ok

# 11-13 21 h: with the policy the floor is the unit's cost x (1 + FAIR_MARGIN), plus the refund under a warranty
import policy
p = policy.defaults()


def lifted(cost, floor, lp=1000000, kind="goods", grade="A", warranty=0):
    o = {"unit": {"list_price": lp, "cost": cost, "kind": kind, "grade": grade}, "ask": floor, "floor": floor,
         "step": 0, "warranty": warranty}
    seller.fair_lift(o, STEPS, p)
    first = seller.next_move(o, None, STEPS, True, False)
    bid = round(min(0.90 * first["price"], 0.70 * lp))
    return o["floor"], bid, seller.next_move(o, bid, STEPS, False, False)["close"]


refund = round(seller.BREAK["A"] * 700000)
M = 1 + seller.FAIR_MARGIN                       # 0.99: a deal may lose 1% of the cost
MF = 1 + seller.FAIR_MARGIN_FOOD                 # 0.95 for food
for label, args, want in (
        ("goods A that cost 0.69, floor 0.75: the floor is 0.99 x cost, the 0.70 bid closes", dict(cost=690000, floor=750000), (round(690000 * M), 700000, True)),
        ("food that cost 16,640 of 23,900 (11-13 12:57): 16,730 closes", dict(cost=16640, floor=17971, lp=23900, kind="food"), (round(16640 * MF), 16730, True)),
        ("food that cost 0.717 of list (11-13 22:36, 9,253 of 12,900): 9,030 closes, 2.4% under its cost", dict(cost=9253, floor=9160, lp=12900, kind="food"), (round(9253 * MF), 9030, True)),
        ("food that cost 0.75 of list: 0.70 is over 5% under, no deal", dict(cost=9675, floor=10000, lp=12900, kind="food"), (round(9675 * MF), 9030, False)),
        ("an A that cost 0.705: closes 1% under its cost at most", dict(cost=705000, floor=740000), (round(705000 * M), 700000, True)),
        ("a dear A that cost 0.72: 0.70 is over 1% under the cost, no deal", dict(cost=720000, floor=740000), (round(720000 * M), 700000, False)),
        ("under a warranty the floor carries the refund", dict(cost=650000, floor=750000, warranty=72), (round(650000 * M + refund), 700000, True)),
        ("a floor already lower (clearance) stays", dict(cost=690000, floor=600000), (600000, 700000, True))):
    got = lifted(**args)
    ok = got == want
    print("ok " if ok else "BAD", label, "| floor, bid, closed:", got, "want", want)
    good &= ok
print("ALL OK" if good else "FAILED")
