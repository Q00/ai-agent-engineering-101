"""Long-term profiles (buyer styles, shops), recency x importance recall, and their use in pricing."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import pricing
from test_r3_core import mem


def test_buyer_style_ignores_product_and_numbers():
    a = shop_r3.buyer_style("I want to buy 의자 (G1). Need at least 8 h.", {"name": "의자", "sku": "G1"})
    b = shop_r3.buyer_style("I want to buy 생수 2L (F1). Need at least 30 h.", {"name": "생수 2L", "sku": "F1"})
    c = shop_r3.buyer_style("hello, how much is 의자?", {"name": "의자", "sku": "G1"})
    assert a == b and a != c


def test_style_profile_is_recency_weighted(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    for price in (80000, 82000, 84000):
        m.record_negotiation("shop", "t", None, "G1", 95000, price, [70000, price], "deal", style="s1")
    m.record_negotiation("shop", "t", None, "G1", 95000, None, [50000], "reject", style="s1")
    p = m.buyer_style_profile("s1")
    assert p["deals"] == 3 and p["close_ratio"] == 0.82 and p["min_close_ratio"] == 0.8 and p["deal_rate"] == 0.75
    assert m.buyer_style_profile("other")["deals"] == 0


def test_hold_slows_concession_but_keeps_acceptance():
    _, free = pricing.seller_reply(85000, 70000, 60000, 3)       # 85000 - 0.4 * (85000 - 70000) = 79000
    _, held = pricing.seller_reply(85000, 70000, 60000, 3, hold=82000)
    assert free == 79000 and held == 82000
    assert pricing.seller_reply(95000, 70000, 72000, 4, hold=82000) == ("accept", 72000)   # still sells above floor


def test_memory_opening_from_shop_profile():
    assert pricing.memory_opening(100000, 90000, {"deals": 1, "min_close_ratio": 0.7}) is None
    assert pricing.memory_opening(100000, 90000, {"deals": 3, "min_close_ratio": 0.7}) == 67900
    assert pricing.buyer_next_offer(100000, 90000, None, None, 0, opening=67900) == ("offer", 67900)


def test_recall_ranks_by_recency_importance_relevance(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    m.note("payment_refused", "https://s1", "kit refused to pay: grade C", 0.7)
    m.note("goods_broke", "https://s2", "의자 broke; warranty covered: False", 0.9)
    with m.db() as c:                                       # an old, important note fades
        c.execute("INSERT INTO notes (ts, kind, subject, text, importance, refs) VALUES (?,?,?,?,?,?)",
                  (time.time() - 48 * 3600, "goods_broke", "s3", "old 의자 broke", 0.9, "[]"))
    top = m.recall("broke")
    assert top[0]["subject"] == "https://s2" and top[-1]["subject"] == "s3"
    assert [r["kind"] for r in m.recall("refused")] == ["payment_refused"]
    assert len(m.recall("")) == 3
