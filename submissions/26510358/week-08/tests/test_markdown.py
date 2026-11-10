"""RL_PROACTIVE.md 3: the shop marks down an overstocked SKU unprompted, only when p > C/(B+C) in board score.
2026-11-10 dry run on the live shelf: G2cb8041b (108 units, 8 sold in 24 h, 13.5 days of cover with 7.2 days left)
is marked down; G021aae64 (90 units, 23 sold a day) sells out in time and is left alone."""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import bandit, config, markdown, pricing, score_model
from r3lib.memory import Memory
from r3lib.org import departments

LIST = 300_000
OURS = "https://ours.example"


def setup(tmp_path, monkeypatch, own_sales, market_ratio=0.60, days_left=7.0, market_deals=40):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: days_left * 24)
    m = Memory(tmp_path / "m.sqlite")
    now = time.time()
    with m.db() as c:
        c.execute("INSERT INTO shops VALUES (?,?,?,?,1,?)", (OURS, "myeonggi", "pk", "me", now))
        seq = 0
        for i in range(market_deals):
            seq += 1
            c.execute("INSERT INTO settles (seq, ts, shop, sku, category, kind, list_price, amount, grade) VALUES "
                      "(?,?,?,?,?,?,?,?,?)", (seq, now - 3600 - i, "https://other", "G1", "가구>의자", "goods", LIST,
                                              int(LIST * market_ratio), "B"))
        for i in range(own_sales):
            seq += 1
            c.execute("INSERT INTO settles (seq, ts, shop, sku, category, kind, list_price, amount, grade) VALUES "
                      "(?,?,?,?,?,?,?,?,?)", (seq, now - 7200 - i, OURS, "G1", "가구>의자", "goods", LIST,
                                              int(LIST * market_ratio), "B"))
    return m, now


def shelf(n, cost=150_000, age_h=10):
    return [{"unit": f"u{i}", "sku": "G1", "kind": "goods", "grade": "B", "cost": cost, "list_price": LIST,
             "category": "가구>의자", "supplied_at": time.time() - age_h * 3600} for i in range(n)]


def always(arm):
    b = bandit.Bandit("clearance", markdown.ARMS, arm, mem=None, explore=0.0, rng=random.Random(1), log=False)
    return b


def test_units_that_would_outlast_the_round_are_marked_down_to_cost_plus_margin(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, own_sales=2)
    rows = markdown.tick(m, shelf(60), now=now, board=None, policy=always("cost"))
    r = rows[0]
    regular = int(LIST * (0.60 - config.SLOW_UNDERCUT))
    assert r["decision"] == "act" and r["arm"] == "cost" and r["goal"] == "shop_markdown"
    price = int(int(150_000 * (1 + config.MIN_MARGIN)) / 0.97) + 1          # an offer 3% under it is still the floor
    assert r["price"] == price and r["regular"] == regular
    assert r["p"] == 1.0 and r["sell_through"] == round(2 * 7 / 60, 3) and r["B"] > r["C"] > 0
    live = m.get("markdown")["G1"]
    assert live["arm"] == "cost" and live["until"] == round(now + config.MARKDOWN_TTL_H * 3600)
    u = dict(shelf(1)[0])
    floor = pricing.unit_floor(u)
    assert markdown.apply(m, u, regular, floor) == (price, "cost")
    assert markdown.apply(m, dict(u, sku="G2"), regular, floor) == (regular, None)
    assert markdown.apply(m, u, regular, floor, now=now + 3 * 3600) == (regular, None)      # expired: price is back


def test_a_sku_that_sells_out_in_time_is_left_alone(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, own_sales=20)
    rows = markdown.tick(m, shelf(60), now=now, board=None, policy=always("cost"))
    assert rows[0]["decision"] == "skip" and rows[0]["B"] == 0 and rows[0]["sell_through"] == 1.0
    assert "G1" not in (m.get("markdown") or {})                  # a vetoed arm did not run: nothing to learn from


def test_the_cut_is_capped_and_never_goes_under_the_floor(tmp_path, monkeypatch):
    u = dict(shelf(1, cost=100_000)[0])
    floor = pricing.unit_floor(u)
    assert markdown.arm_price("cost", u, floor, 200_000) == int(200_000 * (1 - config.MARKDOWN_MAX_CUT))
    assert markdown.arm_price("dump", u, floor, 120_000) == int(floor / 0.97) + 1
    assert markdown.arm_price("dump", u, floor, 100_000) == 100_000          # never above the regular ask


def test_a_finished_window_pays_the_bandit_with_the_sales_inside_it(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, own_sales=2)
    pol = always("cost")
    pol.mem = m
    markdown.tick(m, shelf(60), now=now - 3 * 3600, board=None, policy=pol)   # window (now-3h, now-1h]: both sales
    rows = markdown.tick(m, shelf(58), now=now, board=None, policy=pol)
    stat = pol._stats("goods|over_round", now)["cost"]
    assert stat[0] > 0.8 and abs(stat[1] - 2 * score_model.sale_value(None, int(LIST * 0.6), 150_000)) < 1e-12
    assert rows and m.get("markdown")["G1"]["ts"] == round(now)              # judged again for the next window


def test_keep_and_small_shelves_do_nothing(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, own_sales=2)
    assert markdown.tick(m, shelf(config.OVERSTOCK_SKU_UNITS), now=now, board=None, policy=always("keep")) == []
    rows = markdown.tick(m, shelf(60), now=now, board=None, policy=always("keep"))
    assert rows[0]["decision"] == "skip" and m.get("markdown")["G1"]["arm"] == "keep"
    u = shelf(1)[0]
    assert markdown.apply(m, u, 171_000, pricing.unit_floor(u)) == (171_000, None)


def test_the_ceo_tick_runs_it_without_an_order_and_at_most_every_ten_minutes(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch, own_sales=2)
    monkeypatch.setattr(departments.market, "read_json", lambda name, default=None, md=None: {
        "round": 3, "units": shelf(60), "dislike_power_unit": 8_675_070, "next_dislike_fee": 0})
    monkeypatch.setattr(departments.score_model, "load", lambda *a, **k: None)
    out = departments.marketing_markdown(m, {}, None, now=now)
    assert out["due"] and out["judged"] == 1
    assert departments.marketing_markdown(m, {}, None, now=now + 60) == {"due": False}
    import json
    goals = [json.loads(x)["goal"] for x in (tmp_path / "logs" / "initiative.jsonl").read_text().splitlines()]
    assert goals == ["shop_markdown", "paid_dislike", "loan_or_bond"]         # refusals are written once a day
