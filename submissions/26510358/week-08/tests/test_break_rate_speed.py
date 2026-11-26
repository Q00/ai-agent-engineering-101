"""2026-11-10: a couch quote took 14.5 s in the live shop (chair 5.9 s, kimchi 0.3 s). break_rate_for joined settles
to breaks without an index on breaks.order_id, on every quote, inside the shop's event loop."""
import time

from r3lib import memory
from r3lib.memory import Memory


def fill(m, n=300, broke=30):
    now = time.time()
    with m.db() as c:
        for i in range(n):
            c.execute("INSERT INTO settles (seq, ts, order_id, sku, category, kind, grade, list_price, amount) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (i, now, f"o{i}", "S", "가구>침대", "goods", "A", 1000, 700))
        for i in range(broke):
            c.execute("INSERT INTO breaks (seq, ts, order_id) VALUES (?,?,?)", (1000 + i, now, f"o{i}"))


def test_breaks_has_an_index_on_order_id_and_the_join_uses_it(tmp_path):
    m = Memory(tmp_path / "m.sqlite")
    fill(m)
    with m.db() as c:
        plan = " ".join(r["detail"] for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM settles s LEFT JOIN breaks b ON b.order_id = s.order_id "
            "WHERE s.kind='goods' AND s.grade='A' AND s.category='가구>침대'"))
    assert "breaks_order" in plan and "SCAN b" not in plan


def test_break_rate_for_is_cached_and_refreshes_after_the_ttl(tmp_path, monkeypatch):
    m = Memory(tmp_path / "m.sqlite")
    fill(m)
    p = m.break_rate_for("A", "가구>침대")
    assert round(p, 3) == round(31 / 310, 3)
    calls = []
    monkeypatch.setattr(m, "_break_rate_for", lambda g, k: calls.append((g, k)) or 0.5)
    assert m.break_rate_for("A", "가구>침대") == p and calls == []          # from memory: no query
    assert m.break_rate_for("B", "가구>침대") == 0.5 and len(calls) == 1    # another cell is computed once
    monkeypatch.setattr(memory, "BREAK_CACHE_S", 0)
    assert m.break_rate_for("A", "가구>침대") == 0.5
