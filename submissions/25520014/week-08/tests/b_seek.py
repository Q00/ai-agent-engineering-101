"""Checks restock.B_SEEK (11-13): a dear goods SKU held without a B is a candidate for B_SEEK_QTY units whatever
A it holds and whatever the A price check says; not with a B on the shelf, little cash, a cheap SKU, a slow SKU, a
SKU not held, or the flag off. The market, the demand and the playbook are stubbed: no network, no model, no DB.

    python tests/b_seek.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import model
import policy
import restock

NOW = 1_800_000_000
S = "G00000001"
RATE = {"v": 1.5}


class Est:
    db = None
    break_rate = {"A": 0.03, "B": 0.02, "C": 0.05}
    asked_recent = set()

    def __init__(self, cat):
        self.cat = cat

    def sku_rate(self, s): return 1.0
    def unproven(self, s): return False
    def shortage(self): return {}
    def price_ratio(self, c): return 0.70
    def own_sku_posterior(self, s): return 3.0, 2.0
    def own_sku_rate(self, s): return RATE["v"]


restock.sku_demand = lambda est, s: RATE["v"]
restock.cap_hours = lambda: 1.0
restock.surge_counts = lambda inv, now, db=None: {}
restock.old_c = lambda u, cat, now: False
restock.a_rates = lambda *a, **k: {}
policy.load_playbook = lambda: {}
policy.skipped = lambda pb, now: set()
# the market's A at 0.675 of the list: an A bought at A_COST (0.66) with the warranty premium (0.03 x 0.675) does
# not pay, so without B_SEEK the SKU is no candidate at all; over the grade mix a unit still pays its mean cost
MKT = {"A": 0.675}
OWN_A = {"n": 4}                                  # our A sales of the SKU over B_SEEK_A_H (B_SEEK_A_H, 11-16)
restock.own_a_sales = lambda est, now: Counter({S: OWN_A["n"]})
model.market_grade_prices = lambda db, now, cat=None: {S: {"A": MKT["A"], "B": 0.66, "C": 0.50}}

p = policy.defaults()


def run(lp=2_500_000, cash=300_000_000, grades="AAAA", rate=1.5, on=True, a_check=False, mkt_a=0.675, own_a=4):
    OWN_A["n"] = own_a
    restock.B_SEEK = on
    restock.B_SEEK_A_CHECK = a_check
    MKT["A"] = mkt_a
    RATE["v"] = rate
    cat = {S: {"category": "가구 > 소파", "list_price": lp, "kind": "goods"}}
    inv = {"balance": cash, "units": [{"unit": f"u{i}", "sku": S, "grade": g, "kind": "goods", "cost": 0.66 * lp,
                                       "list_price": lp} for i, g in enumerate(grades)]}
    out = restock.candidates(inv, cat, [{"category": "가구 > 소파", "median_price": lp}], Est(cat), p, NOW)[0]
    return (out[S]["room"], out[S]["have"]) if S in out else None


Q = restock.B_SEEK_QTY
rows = (
    ("held 4 A, no B, cash 300M", dict(), (Q, 0)),
    ("held A and C, no B", dict(grades="AACC"), (Q, 0)),
    ("a B on the shelf", dict(grades="AAAB"), None),
    ("cash under B_SEEK_CASH", dict(cash=90_000_000), None),
    ("list under B_SEEK_LIST", dict(lp=1_500_000), None),
    ("demand under B_SEEK_RATE", dict(rate=0.6), None),
    ("not held", dict(grades=""), None),
    ("B_SEEK off", dict(on=False), None),
    # B_SEEK_A_CHECK (11-15): the A must pay at the market's A price like any other unit
    ("A check on, A under its cost at the market", dict(a_check=True), None),
    ("A check on, A pays at 0.75", dict(a_check=True, mkt_a=0.75), "seek"),
    # B_SEEK_A_H (11-16): not while the A held pass 6 h of our A sales of the SKU (at least B_SEEK_A_MIN)
    ("10 A held, 2 A sold in 6 h", dict(grades="A" * 10, own_a=2), None),
    ("10 A held, 12 A sold in 6 h", dict(grades="A" * 10, own_a=12), (Q, 0)),
    ("3 A held, none sold (B_SEEK_A_MIN)", dict(grades="AAA", own_a=0), (Q, 0)),
    ("4 A held, none sold", dict(grades="AAAA", own_a=0), None),
)
good = True
for name, kw, want in rows:
    got = run(**kw)
    ok = (got is not None and got[1] == 0) if want == "seek" else got == want
    print("ok " if ok else "BAD", f"{name}: (room, have) {got} (want {want})")
    good &= ok

# FAIR_FOOD_MIN (11-14): a food SKU fair-furniture asked for 3+ times in 24 h and found nothing is kept at 4 units
F = "G00000002"
asks = {"n": 3}
model.read_jsonl = lambda path, since: [{"ts": NOW - 600 * i, "text": f"I want to buy {F}. Please quote a price in credits."}
                                        for i in range(asks["n"])] + [{"ts": NOW, "text": f"I want to buy {F} (another buyer)"}] * 5


def food(n_asks, held=0, rate=0.05, mn=4):
    restock.FAIR_FOOD_MIN = mn
    asks["n"] = n_asks
    RATE["v"] = rate
    cat = {F: {"category": "수산", "list_price": 28900, "kind": "food", "shelf_hours": 72}}
    inv = {"balance": 300_000_000, "units": [{"unit": f"f{i}", "sku": F, "grade": "A", "kind": "food", "cost": 19000,
                                              "list_price": 28900} for i in range(held)]}
    c = restock.candidates(inv, cat, [{"category": "수산", "median_price": 28900}], Est(cat), p, NOW)[0]
    n = sum(1 for u in restock.ranked(c, p, 1.0, 90.0) if u[2] == F)
    return (c[F]["room"] if F in c else None, n)


for name, kw, want in (
    ("asked 3 times, none held: 4 units, all ranked", dict(n_asks=3), (4, 4)),
    ("asked 3 times, 3 held: 1 more", dict(n_asks=3, held=3), (1, 1)),
    ("asked 3 times, 4 held: no more than the demand's", dict(n_asks=3, held=4), None),
    ("asked twice: as before, no kept units", dict(n_asks=2), "few"),
    ("FAIR_FOOD_MIN 0: as before", dict(n_asks=5, mn=0), "few")):
    got = food(**kw)
    ok = (got[0] is None or got[1] < 4) if want == "few" else (got[0] is None if want is None else got == want)
    print("ok " if ok else "BAD", f"{name}: (room, units ranked) {got} (want {want})")
    good &= ok
import importlib; fresh = importlib.reload(restock)
ok = fresh.B_SEEK_RATE == 1.0 and fresh.STOCK_MIN_SHARE == 0.85 and fresh.B_SEEK_A_CHECK
print("ok " if ok else "BAD", f"B_SEEK_RATE {fresh.B_SEEK_RATE}, STOCK_MIN_SHARE {fresh.STOCK_MIN_SHARE}, B_SEEK_A_CHECK {fresh.B_SEEK_A_CHECK}")
print("ALL OK" if good and ok else "FAILED")
