"""Shadow gate: same data for both policies, delta math, verdict rule, score estimate, CLI report."""
import json

import pytest

from r3lib import market, shadow_gate, twin
from tests.test_twin import CAT, T0, deal, make_db, market_prices, walk


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    db = make_db(tmp_path / "g.sqlite", visits=[deal(T0 - 3600 * k) for k in range(1, 6)] + [walk(T0 - 900)],
                 settles=market_prices(), stars=[(T0 - 100, 5), (T0 - 200, 4)])
    inv = {"balance": 5_000_000, "units": [{"sku": "G1", "cost": 60000}] * 6}
    return twin.load_window(db, hours=6, end=T0, inventory=inv, catalog=CAT, demand_rows=[])


BOARD = {"shops": [
    {"name": "a", "deals": 5, "revenue": 100, "stars": 4.0, "book": 10},
    {"name": "b", "deals": 5, "revenue": 300, "stars": 4.5, "book": 30},
    {"name": "c", "deals": 0, "revenue": 0, "stars": 0, "book": 0},
    {"name": "myeonggi", "deals": 5, "revenue": 200, "stars": 4.2, "book": 20},
]}


def test_live_vs_live_has_zero_delta(w):
    live = twin.live_policy()
    pair = shadow_gate.run_pair(w, live, twin.make_policy({}, name="same"), seed=4)
    assert pair["restock_mode"] == "actual"
    assert all(v == 0 for v in pair["delta"].values())


def test_both_policies_see_the_same_window(w):
    pair = shadow_gate.run_pair(w, twin.live_policy(), twin.make_policy({"ASK_ROOM": 0.2}), seed=0)
    assert pair["live"]["visits"] == pair["candidate"]["visits"] == 6
    assert pair["live"]["stars"] == pair["candidate"]["stars"] == 4.5


def test_delta_is_candidate_minus_live():
    d = shadow_gate.delta({"deals": 3, "revenue": 100, "policy": "live"}, {"deals": 5, "revenue": 90})
    assert d == {"deals": 2, "revenue": -10}


def test_restock_mode_auto():
    live = twin.live_policy()
    assert shadow_gate.restock_mode(live, twin.make_policy({"ASK_ROOM": 0.09})) == "actual"
    assert shadow_gate.restock_mode(live, twin.make_policy({"restock_adjust": {"김치": 0.5}})) == "plan"


def test_percentile_and_all_estimate():
    e = shadow_gate.estimate_all(BOARD)
    # ranked: a, b, me (c has no deal); one of the two others is below us on each metric -> 0.5
    assert e["parts"] == {"book": 0.5, "revenue": 0.5, "stars": 0.5}
    assert e["all"] == 0.5
    up = shadow_gate.estimate_all(BOARD, d_book=15)            # book 35 > both others
    assert up["parts"]["book"] == 1.0
    assert up["all"] == pytest.approx(0.5 * 1 + 0.3 * 0.5 + 0.2 * 0.5, abs=1e-4)


def test_board_ranking_uses_all_marker_and_stars_rank():
    board = {"shops": [dict(s, all=None) if s["name"] == "c" else dict(s, all=0.5) for s in BOARD["shops"]]}
    board["shops"][0]["stars_rank"] = 4.9                   # a: shrunk stars above ours
    e = shadow_gate.estimate_all(board)
    assert e["ranked_shops"] == 3 and e["parts"]["stars"] == 0.0
    assert shadow_gate.board_fit(board)["shops"] == 3


def test_board_book_falls_back_to_profit_plus_stock():
    assert shadow_gate.board_book({"profit": 5, "stock_cost": 7}) == 12
    assert shadow_gate.board_book({"profit": 5}) is None


def _pair(**d):
    base = {"book_delta": 0, "close_rate": 0, "stockouts": 0}
    base.update(d)
    return {"delta": base, "live": {"visits": 100}}


def test_verdict_rule():
    assert shadow_gate.verdict(_pair(book_delta=10), {"delta_all": 1})["verdict"] == "PASS"
    assert shadow_gate.verdict(_pair(book_delta=-1), {"delta_all": 1})["verdict"] == "FAIL"
    assert shadow_gate.verdict(_pair(book_delta=5), {"delta_all": -0.5})["verdict"] == "FAIL"
    assert shadow_gate.verdict(_pair(close_rate=-0.06), {})["verdict"] == "FAIL"
    assert shadow_gate.verdict(_pair(stockouts=10), {})["verdict"] == "PASS"
    assert shadow_gate.verdict(_pair(stockouts=11), {})["verdict"] == "FAIL"


def test_served_close_rate_rule():
    # deals / negotiations goes up, but deals / visits falls by more than 0.03: FAIL (MO 2026-11-05)
    v = shadow_gate.verdict(_pair(close_rate=0.04, served_close_rate=-0.031), {"delta_all": 0})
    assert v["verdict"] == "FAIL" and any("served close rate" in r for r in v["reasons"])
    assert shadow_gate.verdict(_pair(close_rate=0.04, served_close_rate=-0.03), {})["verdict"] == "PASS"
    assert shadow_gate.verdict(_pair(close_rate=-0.05), {})["verdict"] == "PASS"        # MAX_CLOSE_DROP stays 0.05
    assert shadow_gate.verdict(_pair(stockouts=11, served_close_rate=0), {})["verdict"] == "FAIL"   # cap kept
    assert "served-close-rate drop<=0.03" in v["rule"]


def test_served_close_rate_counts_stockouts(w):
    a = w.actual
    assert a["served_close_rate"] == round(a["deals"] / (a["visits"] + a["stockouts"]), 4)
    r = twin.replay(w, twin.live_policy(), seed=0)
    assert r["visits"] == r["served"] + r["stockouts"]
    assert r["served_close_rate"] == round(r["deals"] / r["visits"], 4)


def test_candidate_turning_buyers_away_fails_on_served_rate():
    live = {"deals": 100, "negotiations": 125, "visits": 200, "stockouts": 10, "book_delta": 0,
            "close_rate": 0.8, "served_close_rate": 0.5}
    cand = dict(live, deals=93, negotiations=110, close_rate=round(93 / 110, 4), served_close_rate=round(93 / 200, 4))
    pair = {"live": live, "candidate": cand, "delta": shadow_gate.delta(live, cand)}
    assert pair["delta"]["close_rate"] > 0 and pair["delta"]["served_close_rate"] < -0.03
    assert shadow_gate.verdict(pair, {"delta_all": 0})["verdict"] == "FAIL"


def test_lever1_still_fails_under_the_served_rule():
    # policies/candidates/lever1.json on the pinned window (--end 1793859780, restock plan, PYTHONHASHSEED=0):
    # twin live deals 722 / negotiations 874 / visits 1564, lever1 299 / 387 / 1564, book -12,651,971, ALL -0.1334
    cand_policy = shadow_gate.load_candidate(shadow_gate.WEEK / "policies" / "candidates" / "lever1.json")
    assert twin.restock_differs(twin.live_policy(), cand_policy)
    rate = lambda d, n, v: {"deals": d, "negotiations": n, "visits": v, "close_rate": round(d / n, 4),
                            "served_close_rate": round(d / v, 4)}
    live = dict(rate(722, 874, 1564), book_delta=25_370_649, stockouts=231)
    cand = dict(rate(299, 387, 1564), book_delta=12_718_678, stockouts=882)
    pair = {"live": live, "candidate": cand, "delta": shadow_gate.delta(live, cand)}
    v = shadow_gate.verdict(pair, {"delta_all": -0.1334})
    assert v["verdict"] == "FAIL"
    assert any("served close rate" in r for r in v["reasons"])


def test_evaluate_and_report(w, tmp_path):
    rep = shadow_gate.evaluate(w, [twin.make_policy({}, name="same")], seed=0, board=BOARD, board_path="b.json")
    r = rep["results"][0]
    assert r["gate"]["verdict"] == "PASS" and r["score"]["delta_all"] == 0
    assert rep["calibration"]["observed"]["deals"] == 5
    assert rep["calibration"]["observed"]["visits"] == 6
    path = shadow_gate.write_report(rep, tmp_path / "shadow")
    assert json.loads(path.read_text())["results"][0]["candidate"]["policy"] == "same"
    assert "GATE: PASS" in shadow_gate.render(rep)


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    db = make_db(tmp_path / "c.sqlite", visits=[deal(T0 - 3600)], settles=market_prices())
    inv = {"balance": 1_000_000, "units": [{"sku": "G1", "cost": 60000}]}
    monkeypatch.setattr(market, "read_json",
                        lambda name, default=None, md=None: inv if name == "inventory.json" else [])
    mon = tmp_path / "monitor" / "20261105"
    mon.mkdir(parents=True)
    (mon / "board-20261105T1443.json").write_text(json.dumps(BOARD))
    cand = tmp_path / "cand.json"
    cand.write_text(json.dumps({"name": "room09", "policy": {"ASK_ROOM": 0.09}}))
    code = shadow_gate.main(["--db", str(db), "--end", str(T0), "--candidate", str(cand),
                             "--monitor-dir", str(tmp_path / "monitor"), "--out", str(tmp_path / "shadow")])
    out = capsys.readouterr().out
    assert "live vs room09" in out and "estimated shop ALL" in out and "calibration" in out
    assert code in (0, 1)
    assert list((tmp_path / "shadow").glob("gate-*.json"))


def test_lever1_file_loads_and_keeps_the_stock_cap():
    from pathlib import Path
    p = shadow_gate.load_candidate(Path(__file__).resolve().parent.parent / "policies/candidates/lever1.json")
    live = twin.live_policy()
    assert p["name"] == "lever1" and p["STOCK_BUDGET_MAX"] == live["STOCK_BUDGET_MAX"]
    assert shadow_gate.restock_mode(live, p) == "plan"


def test_lever2_forum_is_estimated_from_its_dry_run_never_pass(w):
    from pathlib import Path
    cand = shadow_gate.load_candidate(Path(__file__).resolve().parent.parent / "policies/candidates/lever2-forum.json")
    assert cand["forum"]["posts_target"] == 345 and cand["forum"]["post_gap_s"] == 300
    rep = shadow_gate.evaluate(w, [cand], board=BOARD, calibrate=False)
    r = rep["results"][0]
    assert r["restock_mode"] == "actual"
    assert r["delta"]["book_delta"] == 0 and r["delta"]["deals"] == 0
    assert r["unsimulated"]["forum"]["simulated"] is False
    dry = r["forum_dry_run"]
    assert dry["sent"] is False and dry["llm_calls"] == 0
    assert r["gate"]["verdict"] == ("ESTIMATED" if dry["checks"]["ok"] else "FAIL")
    assert r["gate"]["verdict"] != "PASS"
    assert "forum dry run" in shadow_gate.render(rep)


def test_exit_code_ok_only_for_pass_or_estimated():
    assert shadow_gate.OK_VERDICTS == ("PASS", "ESTIMATED")


def test_lever1_carries_ma2_targets_and_cuts():
    from pathlib import Path
    p = shadow_gate.load_candidate(Path(__file__).resolve().parent.parent / "policies/candidates/lever1.json")
    assert p["STOCK_BUDGET_MAX"] == 16_000_000
    assert p["cut_skus"] == {"G0be9ed07": 97, "G200e39a3": 100}
    assert p["sku_targets"]["G1515d951"] == 12 and p["sku_targets"]["G5e3784e6"] == 10
    shares = p["category_budget_share"]
    assert shares["소파/의자/스툴"] + shares["침대/매트리스"] <= 1.0 + 1e-9
