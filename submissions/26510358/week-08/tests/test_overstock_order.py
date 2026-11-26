"""2026-11-10: best grade first left 53 B + 34 C couches (20M) unsold while A, the thinnest margin, kept selling and
being restocked. An overstocked SKU offers its widest-margin grade first, C last, and a slow one asks under market."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import config, market, pricing

RATIO = {"A": 0.75, "B": 0.66, "C": 0.60}
COST = {"A": 0.70, "B": 0.515, "C": 0.37}


def stock(**grades):
    out = []
    for g, n in grades.items():
        out += [{"unit": f"{g}{i}", "sku": "Gaaaaaaaa", "name": "couch", "category": "가구>침대", "kind": "goods",
                 "grade": g, "cost": int(500_000 * COST[g]), "list_price": 500_000, "supplied_at": 1000 + i}
                for i in range(n)]
    return out


def pick(monkeypatch, units, ratio=lambda sku, grade: (RATIO[grade], 50)):
    inv = {"round": 3, "units": units}
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: inv)
    return shop_r3.find_unit("I want to buy couch (Gaaaaaaaa)", set(), ratio)[0]


def test_small_stock_keeps_best_grade_first(monkeypatch):
    u = pick(monkeypatch, stock(A=2, B=3, C=3))
    assert (u["grade"], u["sku_units"]) == ("A", 8)


def test_overstock_offers_widest_margin_grade_but_c_last(monkeypatch):
    assert pick(monkeypatch, stock(A=2, B=6, C=6))["unit"] == "B0"        # B 0.145 > A 0.05; C 0.23 waits
    assert pick(monkeypatch, stock(A=2, C=12))["grade"] == "A"
    assert pick(monkeypatch, stock(C=12))["unit"] == "C0"


def test_grade_without_market_evidence_goes_after_known_ones(monkeypatch):
    u = pick(monkeypatch, stock(A=6, B=6), ratio=lambda sku, grade: (None, 0) if grade == "B" else (0.75, 50))
    assert u["grade"] == "A"


def test_no_memory_keeps_best_grade_first(monkeypatch):
    assert pick(monkeypatch, stock(A=2, B=6, C=6), ratio=None)["grade"] == "A"


def test_slow_sku_needs_overstock_and_more_than_two_days_of_own_sales():
    assert pricing.is_slow_sku(93, 25)
    assert not pricing.is_slow_sku(93, 60)
    assert not pricing.is_slow_sku(config.OVERSTOCK_SKU_UNITS, 0)


def test_slow_sku_asks_under_the_same_grade_market_but_never_under_the_floor():
    assert pricing.cap_ask(400_000, 500_000, 0.66, floor=280_000, over=-config.SLOW_UNDERCUT) == int(500_000 * 0.63)
    assert pricing.cap_ask(400_000, 500_000, 0.66, floor=350_000, over=-config.SLOW_UNDERCUT) == 350_000


def test_my_sku_sales_counts_only_our_shop(tmp_path):
    import time
    from r3lib.memory import Memory
    m = Memory(tmp_path / "m.sqlite")
    now = time.time()
    with m.db() as c:
        c.execute("INSERT INTO shops (shop_url, name, mine, ts) VALUES ('https://ours', 'myeonggi', 1, ?)", (now,))
        for i, shop in enumerate(["https://ours", "https://ours", "https://other", "myeonggi"]):
            c.execute("INSERT INTO settles (seq, ts, sku, shop, list_price, amount) VALUES (?,?,?,?,?,?)",
                      (i, now, "S", shop, 1000, 700))
        c.execute("INSERT INTO settles (seq, ts, sku, shop, list_price, amount) VALUES (9,?,?,?,?,?)",
                  (now - 30 * 3600, "S", "https://ours", 1000, 700))
    assert m.my_sku_sales("S") == 3
