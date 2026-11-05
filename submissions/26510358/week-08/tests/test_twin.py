"""Market twin on a synthetic memory.sqlite (no network, no decision API)."""
import json
import sqlite3

import pytest

from r3lib import config, market, twin
from r3lib.memory import Memory

CAT = {
    "G1": {"sku": "G1", "name": "chair", "kind": "goods", "list_price": 100000,
           "category": "가구/침구>가구>소파/의자/스툴"},
    "G2": {"sku": "G2", "name": "big chair", "kind": "goods", "list_price": 120000,
           "category": "가구/침구>가구>소파/의자/스툴"},
    "B1": {"sku": "B1", "name": "duvet", "kind": "goods", "list_price": 50000,
           "category": "가구/침구>침구>이불"},
}
T0 = float(int(config.ROUND_END) - 5 * 86400)     # before the clearance hours
SHOP = "https://me.example"


def make_db(path, visits=(), settles=(), supplies=(), stockouts=(), stars=()):
    Memory(path)                                            # the real schema
    c = sqlite3.connect(path)
    c.execute("INSERT OR REPLACE INTO shops(shop_url, name, mine) VALUES (?, ?, 1)", (SHOP, "myeonggi"))
    for i, v in enumerate(visits):
        c.execute("INSERT INTO negotiations(ts, role, ref, counterpart, sku, category, list_price, first_ask, "
                  "final_price, offers, outcome, style) VALUES (?, 'shop', ?, 'b', ?, ?, ?, ?, ?, ?, ?, ?)",
                  (v["ts"], f"r{i}", v["sku"], CAT[v["sku"]]["category"], CAT[v["sku"]]["list_price"],
                   v["ask"], v.get("final"), json.dumps(v["offers"]), v["outcome"], v.get("style", "s1")))
    for i, s in enumerate(settles):
        c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, "
                  "grade) VALUES (?, ?, ?, 'b', ?, ?, ?, 'goods', ?, ?, 'A')",
                  (1000 + i, s["ts"], f"o{i}", s.get("shop", SHOP), s["sku"], CAT[s["sku"]]["category"],
                   CAT[s["sku"]]["list_price"], s["amount"]))
    for i, (ts, sku, qty, total) in enumerate(supplies):
        c.execute("INSERT INTO ledger(seq, ts, kind, round, data) VALUES (?, ?, 'supply', 3, ?)",
                  (5000 + i, ts, json.dumps({"shop": "myeonggi", "sku": sku, "qty": qty, "total": total})))
    for ts, sku in stockouts:
        c.execute("INSERT INTO stockouts(ts, category, sku) VALUES (?, ?, ?)", (ts, CAT[sku]["category"], sku))
    for i, (ts, st) in enumerate(stars):
        c.execute("INSERT INTO ledger(seq, ts, kind, round, data) VALUES (?, ?, 'result', 3, ?)",
                  (9000 + i, ts, json.dumps({"shop": SHOP, "stars": st})))
    c.commit()
    c.close()
    return path


def market_prices(n=5, sku="G1", ratio=0.8):
    return [{"ts": T0 - 3600 * 10 - i, "sku": sku, "amount": int(CAT[sku]["list_price"] * ratio),
             "shop": "https://other"} for i in range(n)]


def deal(ts, sku="G1", ask=87000, offers=(70000, 78000, 84000), final=84000, style="s1"):
    return {"ts": ts, "sku": sku, "ask": ask, "offers": list(offers), "final": final, "outcome": "deal", "style": style}


def walk(ts, sku="G1", ask=87000, offers=(50000, 55000)):
    return {"ts": ts, "sku": sku, "ask": ask, "offers": list(offers), "final": None, "outcome": "cancel"}


@pytest.fixture
def cat(monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    return CAT


def window(tmp_path, cat, units, **kw):
    db = make_db(tmp_path / "w.sqlite", **kw)
    inv = {"balance": 5_000_000, "units": [{"sku": s, "cost": c, "grade": "A"} for s, c in units]}
    return twin.load_window(db, hours=6, end=T0, inventory=inv, catalog=cat, demand_rows=[])


def test_load_window_is_read_only_and_rebuilds_start_stock(tmp_path, cat):
    db = make_db(tmp_path / "w.sqlite", visits=[deal(T0 - 3600)],
                 settles=market_prices() + [{"ts": T0 - 3600, "sku": "G1", "amount": 84000}],
                 supplies=[(T0 - 7200, "G1", 2, 120000)])
    before = db.read_bytes()
    w = twin.load_window(db, hours=6, end=T0, inventory={"balance": 1, "units": [{"sku": "G1", "cost": 60000}]},
                         catalog=cat, demand_rows=[])
    assert db.read_bytes() == before
    # end 1 unit + 1 sold - 2 supplied = 0 at the start
    assert w.start_stock == {}
    assert w.unit_cost["G1"] == 60000
    assert w.actual["deals"] == 1 and w.actual["visits"] == 1
    assert w.market_ratio("G1", CAT["G1"]["category"], T0) == pytest.approx(0.8)


def test_replay_is_deterministic(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)] * 5,
               visits=[deal(T0 - 3600 * k) for k in range(1, 5)] + [walk(T0 - 1800)], settles=market_prices())
    live = twin.live_policy()
    a = twin.replay(w, live, seed=7, keep_visits=True)
    b = twin.replay(w, live, seed=7, keep_visits=True)
    assert a == b


def test_live_replay_reproduces_observed_deals(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)] * 5,
               visits=[deal(T0 - 3600 * k) for k in range(1, 4)] + [walk(T0 - 1800)], settles=market_prices())
    r = twin.replay(w, twin.live_policy(), seed=1, keep_visits=True)
    assert r["visits"] == 4 and r["deals"] == 3
    assert r["first_ask_ratio"] == pytest.approx(0.87, abs=0.005)    # observed asks replayed via the multiplier


def test_stock_decrements_and_runs_out(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)],
               visits=[deal(T0 - 3600 * 3), deal(T0 - 3600 * 2), deal(T0 - 3600)], settles=market_prices())
    # end stock 1 unit, no sales logged in settles for our shop -> start stock 1
    r = twin.replay(w, twin.live_policy(), seed=0)
    assert r["deals"] == 1 and r["stockouts"] == 2 and r["stock_units_end"] == 0


def test_substitute_within_band(tmp_path, cat):
    w = window(tmp_path, cat, [("G2", 70000)], visits=[deal(T0 - 3600)], settles=market_prices())
    r = twin.replay(w, twin.live_policy(), seed=0, keep_visits=True)
    assert r["served"] == 1 and r["visit_log"][0]["sku"] == "G2"


def test_metric_math(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)] * 3,
               visits=[deal(T0 - 3600 * 2), deal(T0 - 3600), walk(T0 - 600)], settles=market_prices())
    r = twin.replay(w, twin.live_policy(), seed=0)
    assert r["book_delta"] == r["revenue"] - r["cogs"] - r["storage"]
    assert r["close_rate"] == pytest.approx(r["deals"] / r["served"], abs=1e-4)
    assert r["profit_per_visit"] == pytest.approx((r["revenue"] - r["cogs"]) / r["visits"], abs=0.1)
    assert r["cogs"] == 60000 * r["deals"]
    assert r["stock_cost_end"] == 60000 * (3 - r["deals"])
    assert r["storage"] > 0                                   # goods pay 1% of cost per day


def test_silent_buyer_never_buys(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)], visits=[walk(T0 - 600, offers=())], settles=market_prices())
    for p in (twin.live_policy(), twin.make_policy({"ASK_ROOM": -0.5})):
        r = twin.replay(w, p, seed=0)
        assert r["deals"] == 0 and r["silent"] == 1


def test_negotiate_buyer_takes_reachable_ask_and_walks_above_reservation():
    assert twin.negotiate(80000, 60000, [70000], 85000, 0)[0] == "deal"
    out, price, n = twin.negotiate(95000, 60000, [70000, 72000], 74000, 0)
    assert out == "walk" and price is None
    assert twin.negotiate(95000, 60000, [], 90000, 2)[0] == "walk"


def test_higher_ask_room_same_buyers(tmp_path, cat):
    w = window(tmp_path, cat, [("G1", 60000)] * 6,
               visits=[deal(T0 - 3600 * k) for k in range(1, 6)], settles=market_prices())
    lo = twin.replay(w, twin.live_policy(), seed=3, keep_visits=True)
    hi = twin.replay(w, twin.make_policy({"ASK_ROOM": 0.3}), seed=3, keep_visits=True)
    assert [v["id"] for v in lo["visit_log"]] == [v["id"] for v in hi["visit_log"]]
    assert hi["first_ask_ratio"] >= lo["first_ask_ratio"]


def test_policy_patch_is_restored():
    before = config.ASK_ROOM
    with twin.patched(twin.make_policy({"ASK_ROOM": 0.5})):
        assert config.ASK_ROOM == 0.5
    assert config.ASK_ROOM == before


def test_unknown_policy_key_is_refused():
    with pytest.raises(KeyError):
        twin.make_policy({"ASK_ROM": 0.1})


def test_category_match():
    assert twin.match_category("침구", "가구/침구>침구>이불")
    assert not twin.match_category("침구", "가구/침구>가구>침대/매트리스")
    assert twin.match_category("매트리스", "가구/침구>가구>침대/매트리스")


def test_style_profile_matches_memory(tmp_path, cat):
    m = Memory(tmp_path / "p.sqlite")
    recs = []
    for i, (final, outcome) in enumerate([(84000, "deal"), (None, "cancel"), (90000, "deal"), (80000, "deal")]):
        ts = T0 - 3600 * (i + 1)
        with m.db() as c:
            c.execute("INSERT INTO negotiations(ts, role, ref, counterpart, sku, category, list_price, first_ask, "
                      "final_price, offers, outcome, style) VALUES (?, 'shop', ?, 'b', 'G1', ?, 100000, 90000, ?, ?, "
                      "?, 's9')", (ts, f"x{i}", CAT["G1"]["category"], final, json.dumps([1, 2]), outcome))
        recs.append((ts, final, 100000, 2, outcome))
    got = m._profile("role='shop' AND style=?", ("s9",), now=T0)       # what buyer_style_profile reads
    mine = twin.style_profile(recs, T0)
    for k in ("deals", "deal_rate", "close_ratio", "min_close_ratio"):
        assert got[k] == mine[k]


def test_plan_mode_restocks_with_category_shares(tmp_path, cat):
    w = window(tmp_path, cat, [], visits=[deal(T0 - 600)], settles=market_prices())
    w.fill_rate = 1.0
    w.unit_cost = {"G1": 60000, "B1": 30000}
    calls = []

    def plan_fn(inv, ts, adjust, policy):
        calls.append((ts, adjust, config.CATEGORY_MAX_SHARE))
        return [{"sku": "G1", "qty": 3}, {"sku": "B1", "qty": 20}]
    p = twin.make_policy({"category_budget_share": {"침구": 0.01, "소파/의자/스툴": 0.35},
                          "restock_adjust": {"침구": 0.5}})
    r = twin.replay(w, p, seed=0, restock="plan", plan_fn=plan_fn)
    assert calls and calls[0][2] == 0.35                       # plan saw the raised global share
    assert calls[0][1] == {"가구/침구>침구>이불": {"factor": 0.5}}
    stock_max = min(p["STOCK_BUDGET_MAX"], max(p["MAX_STOCK_COST"], p["STOCK_BUDGET_SHARE"] * 5_000_000))
    b1_cap = int(0.01 * stock_max // (50000 * 0.75))
    assert r["deals"] == 1
    assert r["restock_units"] >= 3 and r["restock_units"] <= 3 * len(calls) + b1_cap
    assert config.CATEGORY_MAX_SHARE == 0.25 or config.CATEGORY_MAX_SHARE == twin.live_policy()["CATEGORY_MAX_SHARE"]


def test_cut_skus_skip_planned_units_across_ticks():
    lines = [{"sku": "B1", "qty": 4}, {"sku": "G1", "qty": 2}]
    st = {}
    p = {"cut_skus": {"B1": 6}}
    assert twin._apply_cuts(lines, p, st) == [{"sku": "G1", "qty": 2}]
    assert twin._apply_cuts(lines, p, st) == [{"sku": "B1", "qty": 2}, {"sku": "G1", "qty": 2}]
    assert st == {"B1": 6}


def test_sku_targets_top_up_inside_the_stock_cap(tmp_path, cat):
    w = window(tmp_path, cat, [], visits=[deal(T0 - 600)], settles=market_prices())
    p = twin.make_policy({"sku_targets": {"G2": 5, "G1": 1}, "STOCK_BUDGET_MAX": 200_000,
                          "MAX_STOCK_COST": 200_000})
    stock = twin._Stock({}, w.catalog)
    out = twin._apply_sku_targets([], stock, 0, p, w)
    # G1 first (sorted): 1 x 75k; then G2 at 90k each: only one more fits under 200k
    assert out == [{"sku": "G1", "qty": 1}, {"sku": "G2", "qty": 1}]
    assert twin.restock_differs(twin.live_policy(), p)
