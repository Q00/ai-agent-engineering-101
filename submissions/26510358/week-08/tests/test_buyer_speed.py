"""2026-11-11: our buyer finished 109 orders an hour against 163 for the top buyers; 14.4 s of each 33 s cycle went to
candidates(), which asked the same per-shop questions for each of 236 offers from 34 shops, each a full table scan.
The answers are now asked once per call, and the shop columns are indexed."""
import json
import sqlite3
from unittest.mock import patch

import buyer_r3
from r3lib.memory import Memory

CAT = {f"S{i}": {"list_price": 100_000 + i, "kind": "food", "category": "c" if i % 2 else "d"} for i in range(6)}


class Counting:
    def __init__(self):
        self.calls = []

    def break_table(self):
        return {"grade": {}, "cat": {}, "shop": {}}

    def shop_reputation(self, url):
        self.calls.append(("reputation", url))
        return {"lie_rate": 0.0, "our_buys": 0, "our_paid": 0, "our_bad": 0, "talks_24h": 0, "deals_24h": 0}

    def price_ratio(self, sku=None, category=None, **k):
        self.calls.append(("price_ratio", sku))
        return 0.6 + int(sku[1:]) / 100, 10

    def shop_price_ratio(self, url, category=None, **k):
        self.calls.append(("shop_price_ratio", url, category))
        return {"https://a": 0.62, "https://b": 0.66}[url]

    def shop_profile(self, url):
        self.calls.append(("shop_profile", url))
        return {"n": 5, "deals": 3, "deal_rate": 0.6, "close_ratio": 0.7, "min_close_ratio": 0.65, "rounds": 2}


def run(mem):
    shops = [{"url": u, "name": u[-1], "offers": [{"sku": s, "qty": 1} for s in CAT]} for u in ("https://a", "https://b")]
    order = {"acceptable": list(CAT), "budget": 1_000_000}
    with patch.object(buyer_r3.market, "catalog", return_value=CAT):
        return buyer_r3.candidates(order, shops, mem, set(), theta=0.2)


def test_each_question_is_asked_once_per_shop_and_the_answer_is_unchanged():
    mem = Counting()
    out = run(mem)
    assert len(out) == 12
    count = lambda name: sum(1 for c in mem.calls if c[0] == name)
    assert count("shop_profile") == 2 and count("reputation") == 2
    assert count("shop_price_ratio") == 4                     # two shops x two categories
    assert count("price_ratio") == 6                          # one per SKU, not per offer
    by = {(c["shop"], c["sku"]): c for c in out}
    assert by[("https://a", "S3")]["expected"] != by[("https://b", "S3")]["expected"]   # per-shop answers kept apart
    assert json.dumps(out, sort_keys=True) == json.dumps(run(Counting()), sort_keys=True)


def test_the_shop_columns_the_buyer_reads_are_indexed(tmp_path):
    Memory(tmp_path / "m.sqlite")
    c = sqlite3.connect(tmp_path / "m.sqlite")
    plan = lambda q, *a: " ".join(r[-1] for r in c.execute("EXPLAIN QUERY PLAN " + q, a))
    assert "settles_shop" in plan("SELECT amount FROM settles WHERE shop=? AND ts>?", "x", 0)
    assert "breaks_shop" in plan("SELECT COUNT(*) FROM breaks WHERE shop=?", "x")
    assert "purchases_shop" in plan("SELECT COUNT(*) FROM purchases WHERE shop_url=?", "x")
    assert "negotiations_cp" in plan("SELECT ts FROM negotiations WHERE role='buyer' AND counterpart=?", "x")
