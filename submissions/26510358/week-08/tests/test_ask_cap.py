"""First ask stays within ASK_MAX_OVER_MARKET of the same-grade SKU market ratio (2026-11-06 close-rate cliff)."""
from r3lib import config, pricing


def test_cap_lowers_ask_above_market_plus_room():
    ask = pricing.cap_ask(360_000, 400_000, 0.70, floor=200_000)
    assert ask == int(400_000 * (0.70 + config.ASK_MAX_OVER_MARKET))


def test_cap_keeps_ask_already_inside():
    assert pricing.cap_ask(290_000, 400_000, 0.70, floor=200_000) == 290_000


def test_cap_never_goes_below_floor():
    assert pricing.cap_ask(320_000, 400_000, 0.50, floor=300_000) == 300_000


def test_no_market_evidence_leaves_ask():
    assert pricing.cap_ask(320_000, 400_000, None, floor=200_000) == 320_000


def test_grade_price_ratio_separates_grades(tmp_path):
    import time
    from r3lib.memory import Memory
    m = Memory(tmp_path / "m.sqlite")
    now = time.time()
    with m.db() as c:
        for i in range(10):
            c.execute("INSERT INTO settles (seq, ts, sku, grade, list_price, amount) VALUES (?,?,?,?,?,?)",
                      (i, now, "S", "A", 1000, 850))
            c.execute("INSERT INTO settles (seq, ts, sku, grade, list_price, amount) VALUES (?,?,?,?,?,?)",
                      (100 + i, now, "S", "C", 1000, 640))
    assert m.grade_price_ratio("S", "A")[0] == 0.85
    assert m.grade_price_ratio("S", "C")[0] == 0.64
    assert m.grade_price_ratio("S", "B") == (None, 0)
