"""v1.24 (2026-11-05, 성수님: the agent should improve the policy overnight): switchback blocks shrink from 3 steps
(1 h) to 1 step for price and buyer and 2 steps for supply and structure (first step washout), and loop.reblock gives
an experiment its lane's current block length until its first block closes."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from icrl import experiment as X  # noqa: E402
from icrl import loop  # noqa: E402
from icrl import space as SP  # noqa: E402

CTX = SimpleNamespace(space=SP.load())


def _exp(lane, steps=3, washout=0, blocks=None, protocol="switchback"):
    return {"id": "e0100", "lane": lane, "protocol": protocol, "block_steps": steps, "washout_steps": washout,
            "t_start": 1000.0, "status": "running" if blocks else "scheduled", "blocks": blocks or []}


def test_every_switchback_family_measures_at_least_one_step():
    for e in SP.load():
        if e["protocol"] == "switchback":
            assert e["block_steps"] > e.get("washout_steps", 0), e["path"]


def test_first_look_comes_after_4_h_for_price_and_8_h_for_stock():
    first = X.LOOKS[0] * 2  # blocks per first look
    assert first * 1 * loop.STEP_S == 4 * 3600
    assert first * 2 * loop.STEP_S == 8 * 3600


def test_a_scheduled_experiment_takes_the_lane_length():
    e = _exp("price")
    assert loop.reblock(CTX, e) == 3
    assert (e["block_steps"], e["reblocked_from"]) == (1, 3)


def test_an_open_first_block_gets_the_new_nominal_end():
    e = _exp("stock", washout=1, blocks=[{"i": 0, "arm": "A", "t0": 1500.0, "t1_nom": 1000.0 + 3 * loop.STEP_S}])
    assert loop.reblock(CTX, e) == 3
    assert e["block_steps"] == 2
    assert e["blocks"][0]["t1_nom"] == 1000.0 + 2 * loop.STEP_S


def test_a_late_start_rounds_the_end_past_the_block_start():
    t0 = 1000.0 + 1.5 * loop.STEP_S
    e = _exp("buyer", blocks=[{"i": 0, "arm": "B", "t0": t0, "t1_nom": 1000.0 + 3 * loop.STEP_S}])
    loop.reblock(CTX, e)
    assert e["blocks"][0]["t1_nom"] == 1000.0 + 2 * loop.STEP_S > t0


def test_an_experiment_with_a_closed_block_keeps_its_length():
    blocks = [{"i": 0, "arm": "A", "t0": 1000.0, "t1_nom": 4600.0, "t1": 4700.0},
              {"i": 1, "arm": "B", "t0": 4700.0, "t1_nom": 8200.0}]
    e = _exp("price", blocks=blocks)
    assert loop.reblock(CTX, e) is None
    assert e["block_steps"] == 3 and "reblocked_from" not in e


def test_long_experiments_and_matching_lengths_are_left_alone():
    assert loop.reblock(CTX, _exp("forum", steps=12, protocol="long")) is None
    assert loop.reblock(CTX, _exp("price", steps=1)) is None
