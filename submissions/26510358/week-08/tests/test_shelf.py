"""2026-11-11: 34 grade C couches had no quote in a day (the shop shows A or B of a SKU while it has them) while the
market sold 268 C of that SKU in 48 h; the C units we did sell closed at 0.82 of the market's C median. The CEO tick
now judges, unprompted, which grade a mixed-grade SKU shows first (r3lib/shelf.py), and a grade C unit's first ask
starts from the SKU's grade A market price."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shop_r3
from r3lib import config, market, score_model, shelf
from r3lib.memory import Memory
from r3lib.org import departments

SKU, LIST, OURS = "Gaaaaaaaa", 500_000, "https://ours.example"
RATIO = {"A": 0.75, "B": 0.66, "C": 0.60}
COST = {"A": 0.70, "B": 0.515, "C": 0.37}


def setup(tmp_path, monkeypatch, ours=None, market_deals=None, stars=(4.6, 100), days_left=6.5):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: days_left * 24)
    m = Memory(tmp_path / "m.sqlite")
    monkeypatch.setattr(m, "grade_stars", lambda grade, now=None: stars)
    now = time.time()
    with m.db() as c:
        c.execute("INSERT INTO shops VALUES (?,?,?,?,1,?)", (OURS, "myeonggi", "pk", "me", now))
        seq = 0
        for shop, counts in (("https://other", market_deals or {"A": 40, "B": 40, "C": 40}),
                             (OURS, ours or {"A": 5, "B": 5})):
            for g, n in counts.items():
                for i in range(n):
                    seq += 1
                    c.execute("INSERT INTO settles (seq, ts, shop, sku, category, kind, list_price, amount, grade) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (seq, now - 3600 - i, shop, SKU, "가구>침대", "goods", LIST,
                                                             int(LIST * RATIO[g]), g))
    return m, now


def stock(now, age_h=20, **grades):
    return [{"unit": f"{g}{i}", "sku": SKU, "name": "couch", "category": "가구>침대", "kind": "goods", "grade": g,
             "cost": int(LIST * COST[g]), "list_price": LIST, "supplied_at": now - age_h * 3600 - i}
            for g, n in grades.items() for i in range(n)]


def test_a_grade_the_market_buys_and_we_never_sell_is_shown_first(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    units = stock(now, A=2, B=6, C=12)
    rows = shelf.tick(m, units, now=now, board=None)
    r = rows[0]
    assert len(rows) == 1 and (r["goal"], r["decision"], r["grade"], r["shown_first"]) == \
        ("shop_grade_first", "act", "C", "B")
    assert r["p"] == 1.0 and r["sold_24h"] == 0 and r["held"] == 12 and r["B"] > r["C"] > 0
    assert r["price"] == int(LIST * 0.60) and r["stars"] == 4.6
    assert shelf.live(m, now) == {SKU: "C"} and m.get("shelf")[SKU]["until"] == round(now + config.SHELF_TTL_H * 3600)
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: {"round": 3, "units": units})
    ask = "I want to buy couch (Gaaaaaaaa)"
    assert shop_r3.find_unit(ask, set(), m.grade_price_ratio)[0]["unit"] == "B5"          # the shop's own order
    u = shop_r3.find_unit(ask, set(), m.grade_price_ratio, shelf.live(m, now))[0]
    assert (u["unit"], u["pushed"], u["sku_units"]) == ("C11", "C", 20)                      # the oldest C
    assert shop_r3.find_unit(ask, {f"C{i}" for i in range(12)}, m.grade_price_ratio, shelf.live(m, now))[0]["grade"] == "B"
    assert shelf.tick(m, units, now=now + 60, board=None) == []                              # live: not judged again
    assert shelf.live(m, now + config.SHELF_TTL_H * 3600 + 1) == {}


def test_a_grade_that_sells_out_in_time_at_our_own_pace_stays_where_it_is(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, ours={"A": 5, "B": 5, "C": 10})
    assert shelf.tick(m, stock(now, A=2, B=6, C=12), now=now, board=None) == [] and shelf.live(m, now) == {}
    slow, now = setup(tmp_path / "slow", monkeypatch, ours={"A": 5, "B": 5, "C": 1})
    r = shelf.tick(slow, stock(now, A=2, B=6, C=12), now=now, board=None)[0]
    assert (r["decision"], r["sell_through"]) == ("act", round(6.5 / 12, 3))                 # 1 a day leaves 5.5 unsold


def test_fresh_units_a_thin_market_and_a_price_under_our_floor_are_not_candidates(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    assert shelf.tick(m, stock(now, age_h=2, A=2, B=6, C=12), now=now, board=None) == []
    assert shelf.tick(m, stock(now, A=2, B=6, C=2), now=now, board=None) == []
    assert shelf.tick(m, stock(now, C=12), now=now, board=None) == []                        # one grade: already shown
    thin, now = setup(tmp_path / "thin", monkeypatch, market_deals={"A": 40, "B": 40, "C": 3})
    assert shelf.tick(thin, stock(now, A=2, B=6, C=12), now=now, board=None) == []
    dear = [dict(u, cost=int(LIST * 0.58)) if u["grade"] == "C" else u for u in stock(now, A=2, B=6, C=12)]
    assert shelf.tick(m, dear, now=now, board=None) == []                                    # floor 0.626 > market 0.60


def test_a_grade_whose_deals_earn_low_stars_is_not_pushed(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, stars=(4.1, 50))
    r = shelf.tick(m, stock(now, A=2, B=6, C=12), now=now, board=None)[0]
    assert r["decision"] == "skip" and "4.10 stars over 50 reviews" in r["reason"] and shelf.live(m, now) == {}
    few, now = setup(tmp_path / "few", monkeypatch, stars=(3.0, 5))                          # 5 reviews say nothing yet
    assert shelf.tick(few, stock(now, A=2, B=6, C=12), now=now, board=None)[0]["decision"] == "act"


def test_grade_stars_reads_the_reviews_of_our_goods_deals_at_that_grade(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "catalog", lambda: {SKU: {"kind": "goods"}, "Gfood": {"kind": "food"}})
    m = Memory(tmp_path / "m.sqlite")
    now = time.time()
    reviews = [(OURS, SKU, "C", 5), (OURS, SKU, "C", 4), (OURS, SKU, "A", 1), (OURS, "Gfood", "C", 1),
               ("https://other", SKU, "C", 1), (OURS, SKU, "C", None)]
    with m.db() as c:
        c.execute("INSERT INTO shops VALUES (?,?,?,?,1,?)", (OURS, "myeonggi", "pk", "me", now))
        for i, (shop, sku, claim, stars) in enumerate(reviews):
            c.execute("INSERT INTO ledger VALUES (?,?,?,?,?)", (i + 1, now - 3600 + i, "result", 3, json.dumps(
                {"shop": shop, "stars": stars, "receipt": {"item": sku, "claim": claim}})))
    assert m.grade_stars("C", now=now) == (4.5, 2)
    assert m.grade_stars("B", now=now) == (0.0, 0)


def test_the_ceo_tick_runs_the_shelf_pass_at_most_every_shelf_every_s(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(score_model, "load", lambda *a, **k: None)
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None:
                        {"round": 3, "units": stock(now, A=2, B=6, C=12)})
    out = departments.marketing_shelf(m, {}, None, now=now)
    assert (out["due"], out["judged"], out["acted"], out["live"]) == (True, 1, 1, {SKU: "C"})
    assert departments.marketing_shelf(m, {}, None, now=now + config.SHELF_EVERY_S - 1) == {"due": False}
    assert "shelf" in departments.SKILLS["marketing"]


class Fixed:
    def __init__(self, arm):
        self.arm, self.paid = arm, []

    def choose(self, context, **note):
        self.context = context
        return self.arm, 1.0

    def update(self, context, arm, reward, **note):
        self.paid.append((context, arm, reward, note.get("outcome")))


def shop(tmp_path, monkeypatch, arm, market_deals=None):
    m, now = setup(tmp_path, monkeypatch, market_deals=market_deals)
    monkeypatch.setattr(shop_r3.scout, "ask_premium", lambda mem, cat: 0.0)
    monkeypatch.setattr(score_model, "cached", lambda: None)
    s = shop_r3.Shop(m)
    s.anchor_bandit, ask_b = Fixed(arm), Fixed(1.0)
    s.ask_bandits = {False: ask_b, True: ask_b}
    return s, ask_b, stock(now, C=1)[0]


def test_a_grade_c_unit_asks_a_share_of_the_grade_a_market_price_and_keeps_its_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "C_ANCHOR_ENABLED", False)
    old, ask_b, c = shop(tmp_path / "old", monkeypatch, 0.9)
    rule_ask, floor, _ = old._terms(dict(c))
    assert old._choice[0] is ask_b                                       # the +-10% bandit priced it before
    monkeypatch.setattr(config, "C_ANCHOR_ENABLED", True)
    for arm in config.C_ANCHOR_ARMS:
        s, ask_b, c = shop(tmp_path / str(arm), monkeypatch, arm)
        ask, f, _ = s._terms(dict(c))
        assert (ask, f) == (max(rule_ask, int(LIST * 0.75 * arm)), floor)
        assert s._choice == (s.anchor_bandit, ("goods", "C", "-"), arm)
    assert int(LIST * 0.75 * 0.8) > rule_ask                             # 0.60 of list against the C median's cap
    s.open["t"] = {"unit": dict(c), "first_ask": ask, "offers": [], "t0": time.time(), "choice": s._choice}
    s._end("t", "deal", 330_000)
    assert s.anchor_bandit.paid == [(("goods", "C", "-"), 1.0, score_model.sale_value(None, 330_000, c["cost"]), "deal")]


def test_without_a_grade_a_market_or_under_a_markdown_the_anchor_stands_aside(tmp_path, monkeypatch):
    s, ask_b, c = shop(tmp_path / "noA", monkeypatch, 1.0, market_deals={"B": 40, "C": 40})
    s._terms(dict(c))
    assert s._choice[0] is ask_b
    s, ask_b, c = shop(tmp_path / "md", monkeypatch, 1.0)
    s.mem.put("markdown", {SKU: {"arm": "cost", "grade": "C", "until": time.time() + 3600}})
    ask, floor, _ = s._terms(dict(c))
    assert s._choice is None and ask < int(LIST * 0.75 * 0.8)
    b = dict(c, grade="B", cost=int(LIST * COST["B"]))
    s.mem.put("markdown", {})
    s._terms(b)
    assert s._choice[0] is ask_b                                         # other grades keep the +-10% bandit
