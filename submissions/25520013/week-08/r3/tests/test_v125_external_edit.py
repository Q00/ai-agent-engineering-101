"""v1.25 (2026-11-05): an outside policy edit aborts only the experiments it touches; pairs an edit fell into are
left out of the switchback verdict; unreadable previous bytes still abort every lane."""

import json

from icrl import experiment as X
from icrl import loop
from icrl import space as SP
from tests.test_icrl_lanes import L_of, env, rows, start_all  # noqa: F401 - env is the shared fixture
from tests.test_icrl_loop import HOLD

CAT = "가구/침구>가구>소파/의자/스툴"


def test_pair_diffs_leaves_out_a_pair_an_edit_fell_into():
    blocks = [
        {"i": 0, "arm": "A", "metric": 1.0},
        {"i": 1, "arm": "B", "metric": 3.0},
        {"i": 2, "arm": "B", "metric": 9.0, "ext_edit": True},
        {"i": 3, "arm": "A", "metric": 1.0},
        {"i": 4, "arm": "A", "metric": 2.0},
        {"i": 5, "arm": "B", "metric": 2.5},
    ]
    assert X.pair_diffs(blocks) == [2.0, 0.5]


def test_changed_paths_finds_added_removed_and_category_leaves():
    a = {"version": 1, "shop": {"x": 1}, "categories": {CAT: {"stock_target": 8, "qty_per_buy": 4}}}
    b = {"version": 1, "shop": {"x": 1, "y": 2}, "categories": {CAT: {"stock_target": 16, "qty_per_buy": 4}}}
    got = loop.changed_paths(json.dumps(a).encode(), json.dumps(b).encode())
    assert got == [("categories", CAT, "stock_target"), ("shop", "y")]
    assert loop._pstr(got[0]) == f"categories.{CAT}.stock_target"
    c = {"version": 1, "shop": {"x": 1}}
    gone = loop.changed_paths(json.dumps(a).encode(), json.dumps(c).encode())
    assert gone == [("categories", CAT, "qty_per_buy"), ("categories", CAT, "stock_target")]


def test_edit_hits_own_path_parent_child_and_lane_family():
    space = SP.load()
    exp = {"lane": "stock", "edits": [{"path": f"categories.{CAT}.breadth_qty"}]}
    own = ("categories", CAT, "breadth_qty")
    fam = ("categories", "식품>과자", "stock_target")  # supply family -> stock lane
    other = ("defaults", "goods", "price_mult")  # price lane
    assert loop.edit_hits(space, exp, [own, other]) == [own]
    assert loop.edit_hits(space, exp, [fam]) == [fam]
    assert loop.edit_hits(space, exp, [other, ("version",)]) == []
    assert loop.edit_hits(space, exp, [("categories", CAT)]) == [("categories", CAT)]  # parent of its path
    price = {"lane": "price", "edits": [{"path": "defaults.food.price_mult"}]}
    assert loop.edit_hits(space, price, [("defaults", "goods", "min_margin")]) == [("defaults", "goods", "min_margin")]
    assert loop.edit_hits(space, price, [("shop", "max_spend_frac")]) == []  # supply key: stock lane


def test_unreadable_previous_bytes_abort_every_lane(env, monkeypatch):
    t = start_all(env, monkeypatch)

    def boom(v):
        raise FileNotFoundError(v)

    monkeypatch.setattr(env["ctx"].runs, "policy_bytes", boom)
    edited = env["pol"].read_bytes().replace(b'"version": 10', b'"version": 11')
    env["pol"].write_bytes(edited)
    env["prop"].replies = [HOLD]
    r = loop.step(t + 600, ctx=env["ctx"])
    assert r["live"] == "external"
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert sorted(x["verdict"] for x in ends) == ["aborted_external_edit"] * 3
    assert all(s["active"] is None for s in L_of(env)["lanes"].values())
    alarm = [x for x in rows(env["sdir"] / "log.jsonl") if x.get("what") == "external_edit"]
    assert alarm[-1]["paths"] is None and alarm[-1]["kept"] == []
