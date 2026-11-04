"""v1.25c (2026-11-05): the loss cap is a single-block disaster stop (15 % of the balance); a paired harm stop on won
(t < -4 after 3 pairs) rejects a clearly losing arm early; an experiment whose B equals its A_version stops as
null_arm; a lane that ended earlier in the tick re-cycles from the restored live policy, not the tick-start snapshot."""

import json

import pytest
from icrl import experiment as X
from icrl import loop
from icrl import space as SP

from tests.test_icrl_lanes import (
    PREF,
    L_of,
    all_b,
    env,
    make_env,
    picker,
    rows,
    start_all,
)  # noqa: F401
from tests.test_icrl_loop import NOW0


def _exp(diffs):
    blocks = []
    for k, d in enumerate(diffs):
        blocks += [
            {"i": 2 * k, "arm": "A", "won": 1000.0},
            {"i": 2 * k + 1, "arm": "B", "won": 1000.0 + d},
        ]
    return {"blocks": blocks}


def test_pair_diffs_reads_the_given_key_and_skips_blocks_without_it():
    blocks = [
        {"i": 0, "arm": "A", "metric": 1.0, "won": 100.0},
        {"i": 1, "arm": "B", "metric": 3.0, "won": 40.0},
        {"i": 2, "arm": "B", "metric": 2.0},
        {"i": 3, "arm": "A", "metric": 1.0, "won": 90.0},
    ]
    assert X.pair_diffs(blocks) == [2.0, 1.0]
    assert X.pair_diffs(blocks, key="won") == [-60.0]


def test_harm_needs_three_pairs():
    assert loop.harm(_exp([-500, -510])) is None


def test_harm_trips_on_a_consistent_loss_and_feeds_the_posterior():
    h = loop.harm(_exp([-500, -510, -490]))
    assert (
        h["pairs"] == 3
        and h["mean"] == -500
        and h["t"] < -loop.LOSS_T
        and h["metric"] == "won"
    )
    d, n = X.std_effect(h, "switchback")
    assert d < 0 and n == 3


@pytest.mark.parametrize(
    "diffs", [[-500, 400, -500], [100, 120, 90], [-10, 300, -200, -150]]
)
def test_harm_leaves_noisy_or_good_arms_to_the_verdict(diffs):
    assert loop.harm(_exp(diffs)) is None


def test_harm_with_zero_spread_is_serializable():
    h = loop.harm(_exp([-100, -100, -100]))
    assert h["t"] == -1e9 and h["sd"] == 0
    json.dumps(h)
    assert X.std_effect(h, "switchback") == (0.0, 3)


def test_null_arm_stops_only_the_experiment_whose_b_equals_its_a_version(
    env, monkeypatch
):
    t = start_all(env, monkeypatch)
    price = L_of(env)["lanes"]["price"]["active"]
    a_pol = json.loads(env["orig"])
    SP.put(a_pol, price["edits"][0]["path"], price["edits"][0]["value"])
    a_raw = SP.dumps_like(env["orig"], a_pol)
    real = env["ctx"].runs.policy_bytes
    monkeypatch.setattr(
        env["ctx"].runs,
        "policy_bytes",
        lambda v: a_raw if v == price["A_version"] else real(v),
    )
    r = loop.step(t + 600, ctx=env["ctx"])
    assert r["exp"]["price"] == "null_arm"
    assert r["exp"]["stock"] != "null_arm" and r["exp"]["buyer"] != "null_arm", r
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert [(x["lane"], x["verdict"]) for x in ends] == [("price", "stopped")]
    assert ends[0]["reason"].startswith("null_arm")
    assert "price" in r["cycles"]  # the lane re-cycles in the same tick


def test_a_lane_that_ended_this_tick_recycles_from_the_restored_policy(
    tmp_path, monkeypatch
):
    e = make_env(tmp_path, own_per_h=12)
    ctx = e["ctx"]
    all_b(monkeypatch)
    e["prop"].replies = [picker({"price": PREF["price"]})]
    loop.step(NOW0, ctx=ctx)
    t = L_of(e)["lanes"]["price"]["active"]["t_start"]
    assert loop.step(t + 1, ctx=ctx)["exp"] == {"price": "started"}
    exp = L_of(e)["lanes"]["price"]["active"]
    a_val, b_val = exp["edits"][0]["current"], exp["edits"][0]["value"]
    assert a_val != b_val
    monkeypatch.setattr(loop, "LOSS_CAP_FRAC", 1e-9)  # any shortfall trips
    r = loop.step(exp["blocks"][0]["t0"] + loop.STEP_S - 300, ctx=ctx)
    assert r["exp"] == {"price": "loss_cap"}, r
    assert r["cycles"]["price"].get("started"), r
    new = L_of(e)["lanes"]["price"]["active"]
    assert new["id"] != exp["id"]
    assert (
        new["edits"][0]["current"] == a_val
    )  # v1.25c: the restored A value, not the B value the tick began with
    assert new["edits"][0]["value"] != new["edits"][0]["current"]


def test_harm_ends_the_experiment_at_an_odd_block_close(env, monkeypatch):
    t = start_all(env, monkeypatch)
    hv = {"pairs": 3, "mean": -1000.0, "sd": 100.0, "t": -17.32, "metric": "won"}
    monkeypatch.setattr(
        loop, "harm", lambda exp: hv if exp_lane(exp) == "price" else None
    )
    got = None
    for k in range(1, 12):
        r = loop.step(t + 600 * k, ctx=env["ctx"])
        if r.get("exp", {}).get("price") == "harm":
            got = k
            break
    assert got, "harm never fired"
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert [(x["lane"], x["verdict"]) for x in ends] == [("price", "reject")]
    assert ends[0]["reason"] == "harm t=-17.32 over 3 pairs"
    assert (
        env["pol"].read_bytes() != env["orig"]
    )  # stock and buyer still run their B arms
    post = json.loads((env["sdir"] / "arms.json").read_text())
    assert post[PREF["price"]][0] < 50.0  # the posterior moved down


def exp_lane(exp):
    return loop.exp_lane(exp)
