"""A group whose keys all sit in one scope is measured on that scope, not on every sale of its side (live 11-13: the
two keys of one fruit leaf were judged against the whole shop's noise, +-1.58M/h, and could not be decided), and the
running line says what the verdict counts and how far the present difference is from the needed one."""

from kernel import experiments as ex
from tests.test_experiments import (  # noqa: F401 - root and a1 are fixtures
    BED,
    CATS,
    SOFA,
    H,
    S,
    _hourly,
    _traj,
    _write_events,
    a1,
    root,
)


def _it(key, value, section, group):
    return {"section": section, "key": key, "value": value, "test": "before_after", "group": group}


def _apply(root, a1, items, now=S):
    return ex.apply_policy(root, items, now, _traj(root), role="shop", categories=CATS, validate=a1.load)


def test_group_in_one_leaf_is_measured_on_that_leaf(root, a1):
    out = _apply(root, a1, [_it("price_mult", 0.6, BED, "침대"), _it("stock_target", 60, BED, "침대")])
    assert all(r["ok"] for r in out["results"]), out
    e = ex.load(root)[0]
    assert e["mode"] == "group" and e["scope"] == ex.scope_of(BED)[1]
    assert f"({e['scope']} 판매로 잼)" in "\n".join(ex.lines(root, S))
    # a big swing in another leaf does not move a bed-only group's verdict: bed sales are flat, sofa sales collapse
    wh = e["window_h"]
    _write_events(root, _hourly(S - wh * H, wh, "BED1", 200_000) + _hourly(S, wh, "BED1", 200_000)
                  + _hourly(S - wh * H, wh, "SOFA1", 900_000) + _hourly(S, wh, "SOFA1", 10_000))
    ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    got = ex.load(root)[0]
    assert (got.get("verdict") or {}).get("decision") != "worse", got  # flat bed: no verdict yet or not worse


def test_the_same_swing_reads_as_worse_on_the_whole_shop(root, a1):
    # the contrast: a group across two leaves counts every sale, so the sofa collapse alone makes it worse
    _apply(root, a1, [_it("price_mult", 0.6, BED, "섞음"), _it("price_mult", 0.6, SOFA, "섞음")])
    e = ex.load(root)[0]
    wh = e["window_h"]
    _write_events(root, _hourly(S - wh * H, wh, "BED1", 200_000) + _hourly(S, wh, "BED1", 200_000)
                  + _hourly(S - wh * H, wh, "SOFA1", 900_000) + _hourly(S, wh, "SOFA1", 10_000))
    ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    assert ex.load(root)[0]["verdict"]["decision"] == "worse"


def test_group_across_scopes_stays_on_the_whole_side(root, a1):
    _apply(root, a1, [_it("price_mult", 0.6, BED, "섞음"), _it("price_mult", 0.6, SOFA, "섞음")])
    e = ex.load(root)[0]
    assert e["scope"] == "shop:all"
    assert "(가게 전체 판매로 잼)" in "\n".join(ex.lines(root, S))


def test_interim_line_gives_the_difference_as_a_share_of_the_needed_one(root, a1):
    _apply(root, a1, [_it("price_mult", 0.6, BED, "침대"), _it("stock_target", 60, BED, "침대")])
    e = ex.load(root)[0]
    wh = e["window_h"]
    _write_events(root, _hourly(S - wh * H, wh, "BED1", 200_000) + _hourly(S, wh, "BED1", 210_000))
    line = ex.interim_line(e, S + wh * H, ex.interim_data(root, [e], S + wh * H))
    assert "뚜렷한 판정에 필요한 차이는" in line and "지금 차이는 필요한 차이의 " in line, line
    assert line.rstrip().endswith("%"), line
