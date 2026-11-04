"""v1.26c (2026-11-05): an experiment aborted by an outside policy edit puts its A values back on the paths the edit
left alone, so an untested B value never becomes the new baseline (03:25 KST: e0004, e0006 and e0008 had left theirs
live). A path the edit changed keeps the edit; other lanes' experiments keep running with their B values."""

import json

from icrl import loop
from icrl import space as SP

from tests.test_icrl_lanes import L_of, env, rows, start_all  # noqa: F401 - env is the shared fixture
from tests.test_icrl_loop import HOLD, NOW0

PRICE = "defaults.food.price_mult"  # e0001's own path (tests.test_icrl_lanes.PREF)


def _alarm(env):
    return [
        x for x in rows(env["sdir"] / "log.jsonl") if x.get("what") == "external_edit"
    ][-1]


def _edit(env, path, value):
    raw = env["pol"].read_bytes()
    pol = json.loads(raw)
    SP.put(pol, path, value)
    new = SP.dumps_like(raw, pol)
    env["pol"].write_bytes(new)
    return new


def test_an_abort_restores_the_untouched_b_paths(env, monkeypatch):
    t = start_all(env, monkeypatch)
    exp = L_of(env)["lanes"]["price"]["active"]
    a_val, b_val = exp["edits"][0]["current"], exp["edits"][0]["value"]
    before = json.loads(env["pol"].read_bytes())
    assert SP.get(before, PRICE)[0] == b_val != a_val
    edited = _edit(
        env, "defaults.goods.min_margin", 0.123
    )  # price family, not e0001's own path
    env["prop"].replies = [HOLD]
    r = loop.step(t + 650, ctx=env["ctx"])
    assert r["live"] == "external"
    live = json.loads(env["pol"].read_bytes())
    want = json.loads(edited)
    SP.put(want, PRICE, a_val)
    assert live == want  # the edit stays, e0001's B value is gone
    al = _alarm(env)
    assert al["aborted"] == ["e0001"] and al["restored"] == [PRICE]
    L = L_of(env)
    assert L["lanes"]["stock"]["active"] and L["lanes"]["buyer"]["active"]
    assert L["base_version"] == L["live_version"] and L["live_sha"] == loop.sha(
        env["pol"].read_bytes()
    )
    r = loop.step(t + 700, ctx=env["ctx"])
    assert (
        r["live"] is None
    )  # the restore is the loop's own write, not a second outside edit


def test_an_edit_of_the_own_path_keeps_the_edit(env, monkeypatch):
    t = start_all(env, monkeypatch)
    edited = _edit(env, PRICE, 1.111)
    env["prop"].replies = [HOLD]
    r = loop.step(t + 650, ctx=env["ctx"])
    assert r["live"] == "external" and env["pol"].read_bytes() == edited
    al = _alarm(env)
    assert al["aborted"] == ["e0001"] and al["restored"] == []


def test_an_abort_in_an_a_block_writes_nothing(env, monkeypatch):
    monkeypatch.setattr(loop.X, "block_arm", lambda seed, i: "A")
    loop.step(NOW0, ctx=env["ctx"])
    t = max(x["active"]["t_start"] for x in L_of(env)["lanes"].values())
    loop.step(t + 1, ctx=env["ctx"])
    assert env["pol"].read_bytes() == env["orig"]  # every lane runs its A arm
    edited = _edit(env, "defaults.goods.min_margin", 0.123)
    env["prop"].replies = [HOLD]
    r = loop.step(t + 650, ctx=env["ctx"])
    assert r["live"] == "external" and env["pol"].read_bytes() == edited
    al = _alarm(env)
    assert al["aborted"] == ["e0001"] and al["restored"] == []
