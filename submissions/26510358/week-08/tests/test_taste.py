"""F: a buyer agent that rates by grade gets the grade it rates well first (r3lib/taste.py).
2026-11-12, market-wide stars by grade over 72 h: kcwarni food C 3.0, everything else 5.0 (we sold it 157 food C);
dyishappy A 3.2-3.4, B 2.6-2.7; DY-76 C 4.0-4.3, A 3.1. stars_rank is one mean per account, and we sat at 4.36 under
a cluster of 25 shops at 4.37."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import config, market, taste
from test_r3_core import mem


def ledger_rows(now, buyer, kind, grade, stars, n, start, shop="https://other"):
    out = []
    for i in range(n):
        seq, order = start + 2 * i, f"o-{buyer}-{kind}-{grade}-{start + i}"
        out.append({"seq": seq, "ts": now - 3600, "kind": "settle", "round": 3,
                    "data": {"order": order, "buyer": buyer, "shop": shop, "item": "F1" if kind == "food" else "G1",
                             "amount": 1, "grade": grade, "claim": grade, "claim_honest": True}})
        out.append({"seq": seq + 1, "ts": now - 3500, "kind": "result", "round": 3,
                    "data": {"order": order, "buyer": buyer, "shop": shop, "stars": stars, "verified": True,
                             "receipt": {"task": f"t-{order}"}}})
    return out


def setup(tmp_path, monkeypatch):
    m, now = mem(tmp_path, monkeypatch), time.time()
    with m.db() as c:
        c.execute("INSERT OR REPLACE INTO shops (shop_url, mine) VALUES (?, 1)", ("https://me",))
    rows = (ledger_rows(now, "kc", "food", "C", 3, 12, 1000) + ledger_rows(now, "kc", "food", "B", 5, 12, 2000) +
            ledger_rows(now, "flat", "food", "C", 4, 12, 3000) + ledger_rows(now, "flat", "food", "B", 4, 12, 4000))
    ours = ledger_rows(now, "kc", "food", "C", 3, 3, 5000, shop="https://me") + \
        ledger_rows(now, "flat", "food", "B", 4, 3, 6000, shop="https://me")
    m._ingest(rows + ours)
    for r in ours:                                          # our shop's side: the buyer's message style per task
        if r["kind"] == "result":
            m.record_negotiation("shop", r["data"]["receipt"]["task"], None, "F1", 1, 1, [], "deal",
                                 style="style-" + r["data"]["buyer"])
    return m, now


def test_a_buyer_that_rates_grades_apart_gets_a_preference(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    prefs = taste.build(m, now=now)
    assert prefs["style-kc"]["account"] == "kc" and prefs["style-kc"]["food"][0] == "B"
    assert "style-flat" not in prefs                         # 4.0 for both grades: nothing to prefer


def test_the_ceo_tick_stores_it_at_most_every_taste_every_s(tmp_path, monkeypatch):
    from r3lib.org import departments
    m, now = setup(tmp_path, monkeypatch)
    out = departments.marketing_taste(m, {}, None, now=now)
    assert out["due"] and out["styles"] == 1 and taste.prefer(m, "style-kc", "food")[0] == "B"
    assert departments.marketing_taste(m, {}, None, now=now + config.TASTE_EVERY_S - 1) == {"due": False}
    assert "taste" in departments.SKILLS["marketing"]
    assert taste.prefer(m, "nobody", "food") is None


def _inv(monkeypatch, units):
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: {"round": 3, "units": units})


def test_find_unit_serves_the_preferred_food_grade_that_lasts(tmp_path, monkeypatch):
    mem(tmp_path, monkeypatch)
    now = time.time()
    c = {"unit": "c", "sku": "F1", "name": "생수 2L", "category": "식품>음료>주스/코코아/생수", "kind": "food",
         "grade": "C", "cost": 1, "list_price": 10000, "expires_at": now + 100 * 3600}
    b = dict(c, unit="b", grade="B", expires_at=now + 300 * 3600)
    _inv(monkeypatch, [c, b])
    assert shop_r3.find_unit("I want 생수 2L (F1). Need 24 h", set())[0]["unit"] == "c"          # soonest first
    assert shop_r3.find_unit("I want 생수 2L (F1). Need 24 h", set(), prefer=["B", "A", "C"])[0]["unit"] == "b"
    short_b = dict(b, expires_at=now + 10 * 3600)
    _inv(monkeypatch, [c, short_b])                          # the preferred grade must still last the need
    assert shop_r3.find_unit("I want 생수 2L (F1). Need 24 h", set(), prefer=["B", "A", "C"])[0]["unit"] == "c"


def test_find_unit_serves_the_preferred_goods_grade(tmp_path, monkeypatch):
    mem(tmp_path, monkeypatch)
    a = {"unit": "a", "sku": "G1", "name": "의자", "category": "가구/침구>가구>소파/의자/스툴", "kind": "goods",
         "grade": "A", "cost": 1, "list_price": 100000, "supplied_at": 1}
    cc = dict(a, unit="cc", grade="C", supplied_at=2)
    _inv(monkeypatch, [a, cc])
    assert shop_r3.find_unit("의자 (G1)", set())[0]["unit"] == "a"
    assert shop_r3.find_unit("의자 (G1)", set(), prefer=["C", "B", "A"])[0]["unit"] == "cc"
    assert shop_r3.find_unit("의자 (G1)", set(), first={"G1": "A"}, prefer=["C", "B", "A"])[0]["unit"] == "cc"
