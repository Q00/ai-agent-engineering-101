"""The meta-harness (icrl/meta.py, DESIGN.md sec 13) wired into the loop: same harness limits on both sides, the
same lane -> side mapping, a meta-written side is applied by the loop to each of its lanes with every state key, and
loop.step runs meta.step every META_S."""

import json

from icrl import loop
from icrl import meta

from tests.test_icrl_lanes import env  # noqa: F401  (pytest fixture)
from tests.test_icrl_loop import NOW0


def test_meta_limits_match_the_loop():
    assert meta.SIDES == tuple(dict.fromkeys(loop.LANE_SIDE.values()))
    assert set(loop.LANE_SIDE) == set(loop.LANES)
    assert all(meta.LANE_SIDE[ln] == sd for ln, sd in loop.LANE_SIDE.items())
    assert meta.FEATURES == loop.FEATURES
    assert meta.N_LESSONS_RANGE == loop.N_LESSONS_RANGE
    assert meta.NOTES_MAX == loop.NOTES_MAX
    assert meta.RECALLS == loop.RECALL_ORDERS
    assert meta.META_S == 3 * 3600


def test_a_meta_default_lane_is_applied_with_every_state_key(env):  # noqa: F811
    lanes = {sd: dict(meta.DEFAULT_LANE) for sd in meta.SIDES}
    (env["sdir"] / "harness.json").write_text(json.dumps({"version": 1, "lanes": lanes}))
    for ln in loop.LANES:
        v, h, applied = loop.harness(env["ctx"], ln)
        assert (v, applied) == (1, True)
        assert h["features"] == list(loop.FEATURES)  # nothing dropped from the proposer's STATE


def test_loop_step_runs_meta_every_meta_s(env):  # noqa: F811
    r = loop.step(NOW0, ctx=env["ctx"])
    assert isinstance(r.get("meta"), dict) and r["meta"]["status"] == "waiting", r.get("meta")
    assert (env["sdir"] / "meta" / "state.json").exists()
    r = loop.step(NOW0 + loop.STEP_S, ctx=env["ctx"])
    assert r.get("meta") is None  # not due
    r = loop.step(NOW0 + meta.META_S, ctx=env["ctx"])
    assert isinstance(r.get("meta"), dict), r.get("meta")
