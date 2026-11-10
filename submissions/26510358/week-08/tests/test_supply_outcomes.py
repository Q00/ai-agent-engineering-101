"""2026-11-10, units supplied to us 30-72 h before, traced on the ledger: fresh fruit 563 units, 81% sold, 19%
expired, spend 9.15M against revenue 8.41M; fresh vegetables 62% / 31%, 3.82M against 2.90M; shellfish 65% / 35%.
Fruit also showed 4.7 stockouts/h with 31 units on hand: requests no unit lasted long enough for, counted as an
empty shelf, which sized more fruit."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import restock_r3
import shop_r3
from r3lib import config, market, score_model
from r3lib.memory import Memory

CAT = {"F1": {"name": "사과", "category": "식품>야채/과일>신선과일", "list_price": 30000, "kind": "food", "shelf_hours": 48},
       "G1": {"name": "의자", "category": "가구>의자", "list_price": 100000, "kind": "goods"}}


def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    return Memory(tmp_path / "m.sqlite")


def ledger(m, rows):
    with m.db() as c:
        for i, (ts, kind, data) in enumerate(rows, 1):
            c.execute("INSERT INTO ledger (seq, ts, kind, round, data) VALUES (?,?,?,3,?)", (i, ts, kind, json.dumps(data)))


def test_units_are_traced_from_supply_to_settle_or_expire(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    h = lambda x: now - x * 3600
    ledger(m, [
        (h(100), "supply", {"shop": "myeonggi", "sku": "F1", "qty": 1, "total": 9000, "units": ["old"]}),      # too old
        (h(40), "supply", {"shop": "myeonggi", "sku": "F1", "qty": 4, "total": 60000, "units": ["a", "b", "c", "d"]}),
        (h(40), "supply", {"shop": "other", "sku": "F1", "qty": 1, "total": 15000, "units": ["x"]}),
        (h(38), "settle", {"unit": "a", "amount": 20000, "shop": "https://ours"}),
        (h(37), "settle", {"unit": "b", "amount": 18000, "shop": "https://ours"}),
        (h(36), "settle", {"unit": "x", "amount": 99999, "shop": "https://other"}),
        (h(20), "expire", {"shop": "myeonggi", "units": ["c"], "skus": ["F1"], "cost": 15000}),
        (h(2), "supply", {"shop": "myeonggi", "sku": "F1", "qty": 1, "total": 15000, "units": ["new"]}),        # too young
    ])
    o = m.category_outcomes(now=now)
    assert o == {CAT["F1"]["category"]: {"n": 4, "sold": 2, "expired": 1, "held": 1, "spend": 45000,
                                         "revenue": 38000, "expired_cost": 15000}}
    ledger_calls = []
    monkeypatch.setattr(m, "_seq_at", lambda c, ts: ledger_calls.append(ts) or 0)
    assert m.category_outcomes(now=now + 60) == o and ledger_calls == []            # kept in meta for 30 min


def test_outcome_factor_keeps_cuts_or_stops():
    f = restock_r3.outcome_factor
    ok = {"sold": 200, "expired": 8, "spend": 1_482_000, "revenue": 1_776_000, "expired_cost": 58_000}      # dessert
    fruit = {"sold": 455, "expired": 108, "spend": 9_147_000, "revenue": 8_408_000, "expired_cost": 1_766_000}
    veg = {"sold": 304, "expired": 154, "spend": 3_817_000, "revenue": 2_896_000, "expired_cost": 1_215_000}
    assert f(ok) == 1.0 and f(fruit) == config.OUTCOME_CUT and f(veg) == 0.0
    assert f({"sold": 5, "expired": 20, "spend": 100, "revenue": 0, "expired_cost": 100}) == 1.0    # too few to judge
    assert f(None) == 1.0
    b = score_model.Board(shops={"book": [100e6, 300e6, 400e6], "revenue": [1e9, 3e9, 4e9], "stars_rank": [4.0, 4.5]},
                          buyers={"saved": [0.2], "forum": [0.9], "deals": [10], "posts": [1]},
                          me_shop={"book": 250e6, "revenue": 2e9, "stars_rank": 4.4},
                          me_buyer={"saved": 0.3, "forum": 0.88, "deals": 100, "posts": 5})
    assert f(ok, b) == 1.0 and f(veg, b) == 0.0
    # a small book loss that large revenue outweighs on the board is kept
    assert f({"sold": 90, "expired": 2, "spend": 10_000_000, "revenue": 9_990_000, "expired_cost": 100_000}, b) == 1.0


def test_stale_misses_weigh_a_quarter_in_the_stockout_rate(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    k = CAT["F1"]["category"]
    for stale in (True, True, True, True, False):
        m.record_stockout(k, "F1", stale=stale)
    assert abs(m.stockout_rates(hours=1)[k] - (4 * config.STALE_MISS_WEIGHT + 1)) < 1e-9


def test_the_shop_knows_a_stale_miss_from_an_empty_shelf(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    now = time.time()
    inv = {"round": 3, "units": [{"unit": "u1", "sku": "F1", "name": "사과", "category": CAT["F1"]["category"],
                                  "kind": "food", "grade": "C", "cost": 9000, "list_price": 30000,
                                  "expires_at": now + 5 * 3600}]}
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: inv)
    u, got = shop_r3.find_unit("I want to buy 사과 (F1). Need at least 24 h of shelf life.", set())
    assert u is None and got["units"]                               # the caller sees the shelf and marks it stale
