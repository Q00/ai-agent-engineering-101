"""icrl pure parts: switchback power and false-adopt rate, long-block verdict, guards, gamma pin, board weights.
No network, fixed seeds, no wall clock in assertions."""

import json
import math
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest
from icrl import board as B
from icrl import experiment as X
from icrl import space as SP
from icrl import state as ST

R3 = Path(__file__).resolve().parents[1]
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-sungsoolee-orca-workspaces-ai-agent-engineering-101-Agents-market/"
    "d5f40484-eeeb-4ce5-a60a-536b9c26ca16/scratchpad"
)
POL = json.loads((R3 / "policy.json").read_text(encoding="utf-8"))


def simulate(effect, seed, noise=0.05):
    """One switchback experiment on a synthetic metric with a time-of-day level shared by a pair's blocks."""
    rng = random.Random(seed)
    blocks = []
    for i in range(24):
        level = 100 * (1 + 0.3 * math.sin(2 * math.pi * (i // 2) / 12))
        arm = X.block_arm(seed, i)
        blocks.append(
            {
                "i": i,
                "arm": arm,
                "metric": level * (1 + (effect if arm == "B" else 0))
                + rng.gauss(0, noise * 100),
            }
        )
        if i % 2:
            v, st = X.switchback_verdict(X.pair_diffs(blocks))
            if v:
                return v, st["pairs"]
    return None, 12


def test_t_quantiles_match_tables():
    assert X.t_ppf(0.975, 5) == pytest.approx(2.5706, abs=1e-4)
    assert X.t_ppf(0.975, 11) == pytest.approx(2.2010, abs=1e-4)


def test_switchback_adopts_a_ten_percent_effect_within_12_pairs():
    v, pairs = simulate(0.10, seed=7)
    assert v == "adopt" and pairs <= 12
    rate = sum(simulate(0.10, seed=s)[0] == "adopt" for s in range(100)) / 100
    print(f"power at +10 % (noise 5 %): {rate:.2f}")
    assert rate >= 0.9


def test_switchback_false_adopt_rate_at_zero_effect():
    adopts = sum(simulate(0.0, seed=1000 + s)[0] == "adopt" for s in range(200))
    print(f"false-adopt rate at zero effect: {adopts}/200 = {adopts / 200:.3f}")
    assert adopts / 200 <= 0.05


def test_switchback_keeps_a_at_12_pairs_and_order_is_seeded():
    assert X.switchback_verdict([0.0] * 12)[0] == "keep_a"
    assert X.switchback_verdict([1.0, -1.0] * 4)[0] is None
    assert [X.order(42, p) for p in range(6)] == [X.order(42, p) for p in range(6)]
    assert {X.order(42, p) for p in range(40)} == {"AB", "BA"}


def test_long_block_needs_both_differences():
    assert X.long_verdict(10, 5, 8)[0] == "adopt"
    assert X.long_verdict(10, 5, 12)[0] == "reject"
    assert X.long_verdict(10, 5, None)[0] == "adopt"
    assert X.long_verdict(10, 5)[1]["evidence"] == "weak"


def test_thompson_is_seeded_and_updates():
    post = {}
    X.update(post, "a|up", 2.0, 6)
    assert post["a|up"][0] > 1.5
    t1 = X.thompson(["a|up", "b|up", "c|up", "d|up"], post, {}, seed=3)
    assert t1 == X.thompson(["a|up", "b|up", "c|up", "d|up"], post, {}, seed=3)
    assert len(t1) == 3


SPACE = SP.load()
CATS = ["가구/침구>가구>침대/매트리스", "가구/침구>가구>소파/의자/스툴"]


def chk(edits, ban=False):
    return SP.check(edits, POL, SPACE, CATS, {c: "goods" for c in CATS}, ban)


def test_guard_range_step_and_allowlist():
    assert chk(
        [
            {
                "path": "defaults.food.price_mult",
                "value": round(POL["defaults"]["food"]["price_mult"] + 0.03, 4),
            }
        ]
    )[1]
    assert (
        "max step" in chk([{"path": "defaults.food.price_mult", "value": 0.99}])[0][0]
    )
    assert "outside" in chk([{"path": "defaults.food.price_mult", "value": 1.3}])[0][0]
    assert (
        "allowlist"
        in chk([{"path": "defaults.goods.warranty_uplift", "value": 1}])[0][0]
    )
    assert "integer" in chk([{"path": "buyer.max_shops", "value": 6.5}])[0][0]
    assert chk([{"path": "shop.max_unit_cost", "value": 600000}])[1]   # v1.23b: max_spend_per_run left the space
    assert chk([{"path": "shop.max_unit_cost", "value": 650000}])[0]
    assert chk([{"path": "shop.ask_quantile", "value": "p75"}])[1]
    assert chk([{"path": "shop.ask_quantile", "value": "p25"}])[0]


def test_guard_fixed_keys_are_untouchable():
    for p in (
        "shop.balance_floor",
        "shop.max_inventory_cost_frac",
        "shop.max_total_units",
        "shop.restock_stop_before_end_h",
        "shop.liquidation_line",
        "defaults.food.liquidate_hours",
        "bounds.price_mult",
    ):
        assert "fixed" in chk([{"path": p, "value": 1}])[0][0], p


def test_guard_categories_bundles_and_lists():
    assert chk([{"path": f"categories.{CATS[1]}.stock_target", "value": 6}])[1]
    assert (
        "market stats"
        in chk([{"path": "categories.식품>없음>없음.stock_target", "value": 6}])[0][0]
    )
    assert (
        "mix"
        in chk(
            [
                {"path": "defaults.food.price_mult", "value": 0.95},
                {"path": "buyer.max_shops", "value": 7},
            ]
        )[0][0]
    )
    assert chk(
        [
            {
                "path": "shop.restock_priority",
                "value": POL["shop"]["restock_priority"] + [CATS[1]],
            }
        ]
    )[1]
    assert chk([{"path": "shop.restock_priority", "value": [CATS[1], "식품>x>y"]}])[0]
    assert SP.list_op(["a", "b", "c"], ["b", "a", "c"])[0] == "move"


def test_guard_supply_increase_ban_near_the_stop():
    cur = POL["shop"]["max_unit_cost"]
    assert (
        "ban"
        in chk([{"path": "shop.max_unit_cost", "value": cur + 100000}], ban=True)[
            0
        ][0]
    )
    assert chk([{"path": "shop.max_unit_cost", "value": cur - 50000}], ban=True)[1]
    assert chk([{"path": "defaults.food.price_mult", "value": 0.95}], ban=True)[1]


def test_dumps_like_keeps_bytes():
    raw = (R3 / "policy.json").read_bytes()
    assert SP.dumps_like(raw, json.loads(raw)) == raw


def test_gamma_equals_rlpo_gamma_at():
    src = (R3 / "rlpo_v121" / "rlpo" / "config.py").read_text(encoding="utf-8")
    assert re.search(r"^PERIOD_S = 3600\b", src, re.M) and re.search(
        r"^T_END = 1794927540\b", src, re.M
    )
    assert re.search(r"^GAMMA_FLOOR = 0\.0\b", src, re.M) and re.search(
        r"^GAMMA_DT_S = PERIOD_S\b", src, re.M
    )
    ts = [
        1793707306,
        1794000000,
        1794841140,
        ST.ROUND_END - 3600,
        ST.ROUND_END - 1800,
        ST.ROUND_END + 10,
    ]
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); from rlpo.reward import gamma_at; "
        "print(json.dumps([gamma_at(t) for t in json.loads(sys.argv[2])]))"
    )
    r = subprocess.run(
        [sys.executable, "-B", "-c", code, str(R3 / "rlpo_v121"), json.dumps(ts)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(R3),
    )
    assert r.returncode == 0, r.stderr[-400:]
    assert json.loads(r.stdout) == [ST.gamma_at(t) for t in ts]


@pytest.mark.skipif(
    not (SCRATCH / "board_1920.json").exists(), reason="board snapshot missing"
)
def test_board_weights_on_snapshot():
    board = json.loads((SCRATCH / "board_1920.json").read_text(encoding="utf-8"))
    w = B.weights(board, POL["owner"]["github"])
    print("weights", w)
    assert all(math.isfinite(v) and v > 0 for v in w["w"].values())
    assert set(w["w"]) == set(B.OFFICIAL)  # all 7 official terms
    pop = sorted(
        float(r["profit"]) for r in board["shops"] if not r["house"] and r["deals"] > 0
    )
    assert (
        B.pct(pop[-1] + 1, pop + [pop[-1] + 1]) == 1.0
    )  # more profit never ranks lower
    s = B.summary(board, POL["owner"]["github"])
    assert 0 <= s["profit"]["pct"] <= 1


def test_board_refresh_falls_back_after_three_auth_failures(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return 429, b""

    t = 1793800000
    for i in range(3):
        brd, st = B.refresh(tmp_path, t + i * B.PERIOD_S, fetch)
    assert brd is None and st["alarm"] and len(calls) == 3
    B.refresh(tmp_path, t + 3 * B.PERIOD_S - 1, fetch)
    assert len(calls) == 3  # no refetch inside the period
    brd, st = B.refresh(tmp_path, t + 4 * B.PERIOD_S, lambda u: (200, b'{"shops": []}'))
    assert brd == {"shops": []} and not st["alarm"]
