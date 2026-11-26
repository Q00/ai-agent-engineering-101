"""score_model: the value of an action in board units (RL_PROACTIVE.md section 1)."""
import json

from r3lib import score_model as sm


def board(saved=0.30, deals=1000):
    return sm.Board(
        shops={"book": [100e6, 200e6, 300e6, 400e6], "revenue": [1e9, 2e9, 3e9, 4e9], "stars_rank": [4.0, 4.3, 4.5, 4.7]},
        buyers={"saved": [0.20, 0.28, 0.32, 0.34], "forum": [0.8, 0.9, 0.9, 0.93], "deals": [100, 500, 2000, 9000],
                "posts": [10, 100, 1000, 2000]},
        me_shop={"book": 250e6, "revenue": 2.5e9, "stars_rank": 4.4},
        me_buyer={"saved": saved, "forum": 0.88, "deals": deals, "posts": 500})


def test_pct_is_the_rank_share_with_a_line_between_neighbours():
    others = [10, 20, 30]
    assert sm.pct(others, 5) == 0.0
    assert sm.pct(others, 10) == 1 / 4 and sm.pct(others, 20) == 2 / 4
    assert sm.pct(others, 15) == 1.5 / 4
    assert sm.pct(others, 99) == 3 / 4
    assert sm.pct([], 1) == 0.0
    assert [sm.pct(others, v) for v in range(0, 40)] == sorted(sm.pct(others, v) for v in range(0, 40))


def test_a_deal_above_saved_raises_the_saved_term_and_one_below_lowers_it():
    b = board()
    assert sm.saved_after(b, d_S=0.40, d_deals=1) > b.me_buyer["saved"]
    assert sm.saved_after(b, d_S=0.20, d_deals=1) < b.me_buyer["saved"]
    assert abs(sm.saved_after(b, d_S=0.30, d_deals=1) - 0.30) < 1e-12
    assert sm.delta(b, {"kind": "buy", "r": 0.60})[1] > sm.delta(b, {"kind": "buy", "r": 0.31})[1] > 0


def test_a_sale_at_cost_leaves_book_and_only_adds_revenue():
    b = board()
    at_cost = sm.delta(b, {"kind": "sale", "price": 1_000_000, "cost": 1_000_000})
    assert sm.shop_all(b, book=0, revenue=1_000_000) - sm.shop_all(b) == at_cost[0] > 0 and at_cost[1] == 0
    only_book = sm.shop_all(b, book=0) - sm.shop_all(b)
    assert only_book == 0
    assert sm.delta(b, {"kind": "sale", "price": 900_000, "cost": 1_000_000})[0] < at_cost[0]


def test_holding_goods_and_expired_food_cost_book():
    b = board()
    hold = sm.delta(b, {"kind": "hold", "cost": 10_000_000, "days": 7})[0]
    assert hold < 0
    assert sm.delta(b, {"kind": "expire", "cost": 10_000_000})[0] < hold
    sell_now = sm.delta(b, {"kind": "sale", "price": 9_300_000, "cost": 10_000_000})[0]      # cost x (1 - 7%)
    assert sell_now > hold                                   # same book, plus revenue


def test_broken_goods_count_minus_one_without_a_warranty_and_zero_with():
    assert sm.expected_r(0.40) == 0.40
    assert abs(sm.expected_r(0.40, 0.34, warranty=False) - (0.66 * 0.40 - 0.34)) < 1e-12
    assert abs(sm.expected_r(0.40, 0.34, warranty=True) - 0.66 * 0.40) < 1e-12


def test_saved_threshold_sits_at_or_just_under_saved():
    b = board()
    t = sm.saved_threshold(b)
    assert 0.0 < t <= b.me_buyer["saved"] + 1e-9
    assert sm.delta(b, {"kind": "buy", "r": t + 0.01})[1] > 0 > sm.delta(b, {"kind": "buy", "r": t - 0.01})[1]


def test_a_paid_dislike_costs_unit_times_n_minus_one_squared():
    b = board()
    assert sm.delta(b, {"kind": "paid_dislike", "n": 1, "unit": 8_600_000}) == (0.0, 0.0)
    four = sm.delta(b, {"kind": "paid_dislike", "n": 4, "unit": 8_600_000})[0]             # 9 units = 77.4M
    assert four == sm.shop_all(b, book=-9 * 8_600_000) - sm.shop_all(b) < 0


def test_board_from_api_leaves_us_out_of_the_field_and_round_trips(tmp_path):
    api = {"shops": [{"name": "myeonggi", "deals": 5, "book": 3, "revenue": 30, "stars_rank": 4.4},
                     {"name": "x", "deals": 9, "book": 5, "revenue": 50, "stars_rank": 4.0},
                     {"name": "idle", "deals": 0, "book": 99, "revenue": 0, "stars_rank": 5}],
           "buyers": [{"name": "myeonggi", "deals": 900, "saved": 0.3, "forum": 0.88, "posts": 7},
                      {"name": "y", "deals": 10, "saved": 0.2, "forum": 0.9, "posts": 1}]}
    b = sm.Board.from_api(api, names={"myeonggi"})
    assert b.shops["book"] == [5] and b.me_shop["book"] == 3 and b.buyers["saved"] == [0.2]
    assert abs(b.S - 0.3 * 1000) < 1e-9
    path = tmp_path / "board.json"
    got = sm.load(fetch=lambda: api, path=path, now=1000.0)
    assert got.me_buyer["deals"] == 900 and json.loads(path.read_text())["ts"] == 1000.0
    again = sm.load(fetch=lambda: 1 / 0, path=path, now=1100.0)            # fresh cache: no fetch
    assert again.me_buyer["deals"] == 900
    assert sm.load(fetch=lambda: 1 / 0, path=path, now=1000.0 + sm.STALE_S + 1) is None
    assert sm.Board.from_api({"shops": [], "buyers": []}) is None
