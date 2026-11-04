"""v1.26d (2026-11-05): category arms come from the policy's overrides plus the top 5 categories by our own sales
value, and a scheduled experiment whose category edits all fall outside them is retired before it starts (03:36 KST:
e0017 kimchi breadth_qty, 1 % of our sales value, was 3rd by settle count and 4th by market value)."""

import json

from icrl import loop

from tests.test_icrl_lanes import L_of, all_b, env, rows, start_all  # noqa: F401
from tests.test_icrl_loop import NOW0

MAT, SOFA, BED, KIM, FRUIT, JUICE, RUG = (
    "매트리스",
    "소파",
    "침구",
    "김치",
    "과일",
    "주스",
    "러그",
)
STATS = {  # 03:36 KST live numbers: our settles in 24 h, mean price, market settles per hour
    "category": {
        MAT: {"our_n": 522, "mean": 372870, "settles_h": 432.96},
        SOFA: {"our_n": 79, "mean": 1299194, "settles_h": 134.08},
        BED: {"our_n": 134, "mean": 85166, "settles_h": 443.04},
        RUG: {"our_n": 115, "mean": 52493, "settles_h": 40.12},
        FRUIT: {"our_n": 259, "mean": 18651, "settles_h": 195.71},
        KIM: {"our_n": 140, "mean": 23181, "settles_h": 278.79},
        JUICE: {"our_n": 278, "mean": 9706, "settles_h": 206.33},
        "?": {"our_n": 9999, "mean": 9999999, "settles_h": 9999},
    }
}


def _exp(*paths, retest=False):
    return {
        "edits": [{"path": p, "current": 2, "value": 3} for p in paths],
        "retest": retest,
    }


def _cat(c):
    return f"categories.{c}.breadth_qty"


def test_top_cats_by_count_by_market_value_and_by_ours():
    assert loop.top_cats(STATS, 5) == [BED, MAT, KIM, JUICE, FRUIT]
    assert loop.top_cats(STATS, 5, by="value") == [SOFA, MAT, BED, KIM, FRUIT]
    assert loop.top_cats(STATS, 5, by="ours") == [MAT, SOFA, BED, RUG, FRUIT]


def test_arm_cats_are_the_overrides_plus_the_top_5_by_ours():
    pol = {"categories": {JUICE: {"breadth_qty": 2}, "a.b": {}}}
    assert loop.arm_cats(pol, STATS) == [JUICE, MAT, SOFA, BED, RUG, FRUIT]
    assert KIM not in loop.arm_cats({}, STATS)


def test_retired_only_when_every_edit_is_an_ineligible_category():
    pol = {"categories": {JUICE: {}}}
    assert (
        loop.retired(pol, _exp(_cat(KIM)), STATS)
        == f"retired: {KIM} not among the arm categories"
    )
    assert loop.retired(pol, _exp(_cat(MAT)), STATS) is None
    assert (
        loop.retired(pol, _exp(_cat(JUICE)), STATS) is None
    )  # an override always has arms
    assert loop.retired(pol, _exp(_cat(KIM), _cat(MAT)), STATS) is None
    assert (
        loop.retired(pol, _exp(_cat(KIM), "defaults.food.breadth_qty"), STATS) is None
    )
    assert loop.retired(pol, _exp("defaults.food.breadth_qty"), STATS) is None
    assert loop.retired(pol, _exp(_cat(KIM), retest=True), STATS) is None
    assert loop.retired(pol, _exp(), STATS) is None


def test_a_scheduled_experiment_outside_the_arm_categories_is_retired_at_its_start(
    env, monkeypatch
):
    all_b(monkeypatch)
    loop.step(NOW0, ctx=env["ctx"])
    L = L_of(env)
    exp = L["lanes"]["stock"]["active"]
    assert exp["status"] == "scheduled"
    path = "categories.식품>건강식품>홍삼.breadth_qty"  # neither an override nor in the market stats
    exp["edits"] = [dict(exp["edits"][0], path=path)]
    (env["sdir"] / "loop.json").write_text(json.dumps(L))
    r = loop.step(exp["t_start"] - 1, ctx=env["ctx"])
    assert r["exp"]["stock"] == "scheduled"  # not before its start
    r = loop.step(exp["t_start"] + 1, ctx=env["ctx"])
    assert r["exp"]["stock"] == "retired", r
    end = [
        x
        for x in rows(env["sdir"] / "experiments.jsonl")
        if x.get("event") == "end" and x["id"] == exp["id"]
    ]
    assert (
        len(end) == 1
        and end[0]["verdict"] == "stopped"
        and end[0]["reason"].startswith("retired: 식품>건강식품>홍삼")
    )
    live = json.loads(env["pol"].read_bytes())
    assert "식품>건강식품>홍삼" not in (
        live.get("categories") or {}
    )  # B was never written
    nxt = L_of(env)["lanes"]["stock"].get("active")
    assert (
        "stock" in (r.get("cycles") or {}) and nxt and nxt["id"] != exp["id"]
    )  # the lane re-cycled in the same tick


def test_an_eligible_category_experiment_starts(env, monkeypatch):
    start_all(
        env, monkeypatch
    )  # the stock arm edits the mattress override: started, not retired
    assert L_of(env)["lanes"]["stock"]["active"]["status"] == "running"
