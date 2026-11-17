"""2026-11-10: goods break by grade alone on the ledger (A 2.9%, B 11.9%, C 33.9%; the same in every category, shops
10-15% on B). A break is 0 under a warranty and -1 without (/agent.md). The buyer limited every goods offer as an
average unit without warranty (42% discount needed), so goods fell to 20 of 442 deals in 9 h, while 9 grade B deals
without warranty at a 0.449 discount realised 0.129."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3
from r3lib import config, memory, pricing
from r3lib.memory import Memory

P = {"A": 0.03, "B": 0.12, "C": 0.34}
ORDER = {"kind": "goods", "budget": 900_000, "acceptable": ["G1"]}


def cand(theta=0.75, **kw):
    return {"list": 1_000_000, "budget": 900_000, "kind": "goods", "theta": theta, "p_grade": P,
            "max_pay": 600_000, **kw}


def rec(price, **kw):
    return {"item": "G1", "price": price, **kw}


M = {"food": 1 - 0.508, "A": 1 - 0.618, "B": 1 - 0.561, "C": 1 - 0.495}   # margin / list (config.COST_RATIO)


def test_limits_follow_the_saved_rule():
    """Patch 3.6: a deal scores (L - price) / (L - cost), so the limit is L (1 - margin x need)."""
    L = 1_000_000
    assert pricing.buyer_limits(L, 10**7, "food", min_surplus=0.75) == int(L * (1 - M["food"] * 0.75))
    a_w = pricing.buyer_limits(L, 10**7, "goods", 0.03, warranty=True, min_surplus=0.75, grade="A")
    a = pricing.buyer_limits(L, 10**7, "goods", 0.03, min_surplus=0.75, grade="A")
    b = pricing.buyer_limits(L, 10**7, "goods", 0.12, min_surplus=0.75, grade="B")
    c = pricing.buyer_limits(L, 10**7, "goods", 0.34, min_surplus=0.75, grade="C")
    assert a_w == int(L * (1 - M["A"] * 0.75 / 0.97)) and a == int(L * (1 - M["A"] * 0.78 / 0.97))
    assert a_w > 0.70 * L                                    # grade A under a warranty at the market's 0.70 of list
    assert a_w > a > b and c == 0                            # C without warranty can never reach the threshold
    assert pricing.buyer_limits(L, 500_000, "goods", 0.03, warranty=True, grade="A") == 500_000   # the order's budget


def test_margin_share_is_capped_at_the_whole_margin():
    assert abs(pricing.margin_share(700_000, 1_000_000, "goods", "A") - 0.30 / M["A"]) < 1e-12
    assert pricing.margin_share(400_000, 1_000_000, "goods", "A") == 1.0                   # below cost
    assert abs(pricing.margin_share(600_000, 1_000_000, "food") - 0.40 / M["food"]) < 1e-12


def test_threshold_follows_the_model_inside_bounds():
    lo, hi = config.BUYER_THETA_BOUNDS
    assert pricing.buyer_threshold(None) == config.MIN_SAVED_SHARE
    assert abs(pricing.buyer_threshold(0.75) - (0.75 + config.BUYER_THETA_MARGIN)) < 1e-12
    assert pricing.buyer_threshold(0.30) == lo                # a pre-3.6 value never drags it to a share of list
    assert pricing.buyer_threshold(0.95) == hi


def test_quote_hints_read_grade_and_warranty_in_the_formats_shops_use():
    h = buyer_r3.quote_hints
    assert h("sofa (G1), grade B with the market's certificate, warranty 72 h. My price is 600000.") == ("B", 72.0)
    assert h("sku=G1, unit=u-1, grade=A, cert=true, list_price=7900: asking price 5472") == ("A", 0.0)
    assert h("SKU G1; grade A at quote; certificate included.") == ("A", 0.0)
    assert h("소파 A등급, 보증 6시간 포함. 가격 500000") == ("A", 6.0)
    assert h("grade C, 6-hour warranty included") == ("C", 6.0)
    assert h("Grade B. No warranty on this unit, 24 h delivery.") == ("B", 0.0)
    assert h("a premium unit with the market's certificate") == (None, 0.0)
    assert h(None) == (None, 0.0)


def test_limit_moves_to_the_named_grade_and_warranty():
    c = cand()
    assert buyer_r3.limit_for(c, "A", 72) == int(1_000_000 * (1 - M["A"] * 0.75 / 0.97))
    assert buyer_r3.limit_for(c, "A", 72) > buyer_r3.limit_for(c, "A", 0) > buyer_r3.limit_for(c, "B", 0)
    assert buyer_r3.limit_for(c, "B", 3) == buyer_r3.limit_for(c, "B", 0)          # 3 h does not cover a break
    assert buyer_r3.limit_for(cand(p_grade={}), "A", 72) == 600_000                # no table: the limit stays


def test_the_receipt_decides_by_expected_saved():
    c = cand(max_pay=900_000)
    assert buyer_r3.valid(rec(700_000, claim="A", warranty_hours=72), c, ORDER) is None        # 0.97 x 0.785 = 0.762
    assert "expected saved" in buyer_r3.valid(rec(700_000, claim="A"), c, ORDER)                # 0.762 - 0.03
    assert "expected saved" in buyer_r3.valid(rec(600_000, claim="B"), c, ORDER)                # 0.88 x 0.911 - 0.12
    assert buyer_r3.valid(rec(560_000, claim="B"), c, ORDER) is None                            # 0.88 x 1 - 0.12
    assert "expected saved" in buyer_r3.valid(rec(495_000, claim="C", warranty_hours=6), c, ORDER)   # 0.66 x 1
    assert "grade C" in buyer_r3.valid(rec(100_000, claim="C"), c, ORDER)
    assert buyer_r3.valid(rec(680_000, claim="A"), c, {**ORDER, "kind": "food"}) is None


def test_a_promised_warranty_is_required_of_the_sellers_copy_for_every_grade():
    assert buyer_r3.accept_policy(ORDER, rec(1, claim="A", warranty_hours=72)) == \
        {"cert": True, "grades": ["A", "B"], "warranty_hours": 6}
    assert buyer_r3.accept_policy(ORDER, rec(1, claim="A", warranty_hours=2)) == {"cert": True, "grades": ["A", "B"]}


def fill(m, rows):
    """rows: (grade, category, shop url, sold, broke)"""
    now, seq = time.time(), 0
    with m.db() as c:
        c.execute("INSERT INTO shops (shop_url, name, mine, ts) VALUES ('https://s1', 'bad-shop', 0, ?)", (now,))
        for g, cat, shop, n, k in rows:
            for i in range(n):
                seq += 1
                c.execute("INSERT INTO settles (seq, ts, order_id, shop, category, kind, grade) VALUES (?,?,?,?,?,?,?)",
                          (seq, now, f"o{seq}", shop, cat, "goods", g))
                if i < k:
                    c.execute("INSERT INTO breaks (seq, ts, order_id) VALUES (?,?,?)", (seq, now, f"o{seq}"))


def test_p_break_is_the_grade_rate_moved_only_by_much_evidence(tmp_path):
    m = Memory(tmp_path / "m.sqlite")
    fill(m, [("A", "가구", "https://x", 1000, 30), ("B", "가구", "https://x", 1000, 120),
             ("B", "침구", "https://s1", 400, 200), ("C", "가구", "https://x", 500, 170)])
    t = m.break_table()
    assert t["grade"]["A"] == [1000, 30] and t["shop"]["B|bad-shop"] == [400, 200]
    assert abs(m.p_break("A") - 0.03) < 0.001 and abs(m.p_break("C") - 0.34) < 0.002
    base = m.p_break("B")                                               # (120 + 200) / 1400 = 0.229
    assert abs(base - 320 / 1400) < 0.002
    assert m.p_break("B", shop="bad-shop") > base > m.p_break("B", "가구")
    assert m.p_break("B", shop="never-seen") == base and m.p_break(None) is None
    assert m.p_break_mix() < m.p_break_mix(shop="bad-shop")              # that shop sells only B
    m.put("break_table", {"ts": time.time(), "grade": {"A": [10, 10]}, "cat": {}, "shop": {}})
    assert m.p_break("A") > 0.5                                          # the kept table is used until it is old
    assert m.break_table(max_age=0)["grade"]["A"] == [1000, 30]


def test_without_a_table_the_priors_price_the_risk(tmp_path):
    m = Memory(tmp_path / "m.sqlite")
    assert m.p_break("B") == memory.BREAK_PRIOR["B"]
    assert abs(m.p_break_mix() - (0.7 * 0.03 + 0.2 * 0.12 + 0.1 * 0.34)) < 1e-9
