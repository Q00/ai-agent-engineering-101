"""icrl reward over all 7 official terms (DESIGN.md sec 5): board-measured stars / forum / posts from cached snapshots,
missing-snapshot flags, finite fallback weights, the forum allowlist family (gate, --check copy, hooks/forum.py at
the range edges) and the stars guard in the approver's numeric input. Synthetic boards, no network."""

import functools
import json
import math
import sys
from pathlib import Path

import pytest
from icrl import board as B
from icrl import loop
from icrl import space as SP
from icrl import state as ST

from tests.test_icrl_loop import APPROVE, ARM, NOW0, WHY, Fake, make_work, pick, start

R3 = Path(__file__).resolve().parents[1]
POL = json.loads((R3 / "policy.json").read_text(encoding="utf-8"))
LOGIN = POL["owner"]["github"]
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-sungsoolee-orca-workspaces-ai-agent-engineering-101-Agents-market/"
    "d5f40484-eeeb-4ce5-a60a-536b9c26ca16/scratchpad"
)


def board(stars=4.69, forum=0.638, posts=259):
    me = {"name": "lee-sungsoo", "owner": {"login": LOGIN}, "house": False}
    shops = [
        dict(me, deals=1981, profit=15e6, revenue=1e8, stars=stars, stars_rank=stars)
    ]
    shops += [
        {
            "name": f"s{i}",
            "owner": {"login": f"o{i}"},
            "house": False,
            "deals": 100 + i,
            "profit": 1e7 * (i + 1),
            "revenue": 5e7 * (i + 1),
            "stars": 4.5 + 0.1 * i,
            "stars_rank": 4.5 + 0.1 * i,
        }
        for i in range(4)
    ]
    shops.append(
        {"name": "house", "house": True, "deals": 50, "profit": 1, "stars": 3.0}
    )
    buyers = [dict(me, deals=1681, surplus=2.3e7, forum=forum, posts=posts)]
    buyers += [
        {
            "name": f"b{i}",
            "owner": {"login": f"o{i}"},
            "house": False,
            "deals": 200 + i,
            "surplus": 1e7 * (i + 1),
            "forum": 0.5 + 0.05 * i,
            "posts": 50 * (i + 1),
        }
        for i in range(4)
    ]
    return {"shops": shops, "buyers": buyers}


def snap(sdir, ts, brd):
    d = Path(sdir) / "board"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{int(ts)}.json").write_text(json.dumps(brd), encoding="utf-8")


T0 = 1793800000


def ledger_store():
    """1 h block [T0, T0 + 3600): one own sale (rev 25000, gp 10000) and one buyer deal (surplus 10000)."""
    s = ST.empty()
    s["units"]["u1"] = ["F1", "c", "food", 15000, T0 - 100, None]
    s["sold"]["u1"] = [T0 + 600, 25000, 15000]
    s["deals"] = [[T0 + 600, 20000, 30000]]
    return s


def exp_of(side, w, protocol="switchback"):
    return {"side": side, "protocol": protocol, "w": w}


def test_weights_cover_all_seven_terms_and_are_finite():
    w = B.weights(board(), LOGIN)["w"]
    assert set(w) == set(B.OFFICIAL) and len(w) == 7
    assert all(math.isfinite(v) and v > 0 for v in w.values()), w
    fw = B.fixed_weights({})["w"]
    assert set(fw) == set(B.OFFICIAL)
    assert all(math.isfinite(v) and v > 0 for v in fw.values()), fw
    # normalized as before: official weight / max(|scale|, resolution floor)
    assert fw["profit"] == pytest.approx(0.5) and fw["stars_rank"] == pytest.approx(
        0.2 / 0.01
    )
    fw = B.fixed_weights({"stars_rank": 0.0, "forum": 0.02, "posts": 4.0})["w"]
    assert fw["forum"] == pytest.approx(0.3 / 0.02) and fw["posts"] == pytest.approx(
        0.1 / 4
    )


@pytest.mark.skipif(
    not (SCRATCH / "board_1920.json").exists(), reason="board snapshot missing"
)
def test_real_snapshot_values_are_read_for_our_rows(tmp_path):
    raw = (SCRATCH / "board_1920.json").read_text(encoding="utf-8")
    (tmp_path / "board").mkdir()
    (tmp_path / "board" / f"{T0}.json").write_text(raw, encoding="utf-8")
    v = B._snap_values(tmp_path / "board" / f"{T0}.json", LOGIN)
    assert v == {"stars_rank": 4.69, "stars": 4.69, "forum": 0.638, "posts": 259.0}


def test_reward_includes_all_seven_terms_when_snapshots_exist(tmp_path):
    snap(tmp_path, T0, board(stars=4.69, forum=0.60, posts=250))
    snap(tmp_path, T0 + 3600, board(stars=4.67, forum=0.65, posts=262))
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    shop = loop.measure(store, None, exp_of("shop", w), T0, T0 + 3600, tmp_path, LOGIN)
    buyer = loop.measure(
        store, None, exp_of("buyer", w), T0, T0 + 3600, tmp_path, LOGIN
    )
    assert shop["board_missing"] == [] and shop["board_snaps"] == [T0, T0 + 3600]
    assert shop["board"]["stars_rank_h"] == pytest.approx(-0.02)
    assert shop["stars_drop"] == pytest.approx(0.02)
    want_shop = w["profit"] * 10000 + w["revenue"] * 25000 + w["stars_rank"] * -0.02
    want_buyer = (
        w["surplus"] * 10000 + w["deals"] * 1 + w["forum"] * 0.05 + w["posts"] * 12
    )
    assert shop["metric"] == pytest.approx(want_shop)
    assert buyer["metric"] == pytest.approx(want_buyer)
    # each board-measured term actually moves the metric
    for side, term in (("shop", "stars_rank"), ("buyer", "forum"), ("buyer", "posts")):
        m0 = loop.measure(
            store,
            None,
            exp_of(side, dict(w, **{term: 0.0})),
            T0,
            T0 + 3600,
            tmp_path,
            LOGIN,
        )["metric"]
        assert m0 != pytest.approx((shop if side == "shop" else buyer)["metric"])


def test_long_shop_metric_adds_stars_to_the_weighted_value_change(tmp_path):
    snap(tmp_path, T0, board(stars=4.69))
    snap(tmp_path, T0 + 3600, board(stars=4.70))
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    stats = {"category": {}, "sku": {}}
    m = loop.measure(
        store, stats, exp_of("shop", w, "long"), T0, T0 + 3600, tmp_path, LOGIN
    )
    v = ST.block_metrics(store, T0, T0 + 3600, stats)["value_h"]
    assert m["metric"] == pytest.approx(w["profit"] * v + w["stars_rank"] * 0.01)


def test_missing_snapshot_contributes_zero_and_is_flagged(tmp_path):
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    ledger_only = w["profit"] * 10000 + w["revenue"] * 25000
    snap(tmp_path, T0, board(stars=4.5))  # start only: no end snapshot
    m = loop.measure(store, None, exp_of("shop", w), T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == sorted(B.BOARD_TERMS) and m["board_snaps"] is None
    assert m["metric"] == pytest.approx(ledger_only) and m["stars_drop"] is None
    snap(tmp_path, T0 + 3600 + B.SNAP_TOL_S + 60, board(stars=4.0))  # too far
    m = loop.measure(store, None, exp_of("shop", w), T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == sorted(B.BOARD_TERMS)
    assert m["metric"] == pytest.approx(ledger_only)
    no_forum = board()
    no_forum["buyers"][0]["forum"] = None
    snap(tmp_path, T0 + 3500, no_forum)
    m = loop.measure(store, None, exp_of("buyer", w), T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == ["forum"] and m["board"]["forum_h"] == 0.0
    m = loop.measure(store, None, exp_of("shop", w), T0, T0 + 3600, None, LOGIN)
    assert m["board_missing"] == sorted(B.BOARD_TERMS)


def test_term_scale_feeds_finite_fallback_weights(tmp_path):
    for i, (s, f, p) in enumerate(
        ((4.69, 0.60, 250), (4.68, 0.62, 256), (4.68, 0.62, 256))
    ):
        snap(tmp_path, T0 + 1800 * i, board(stars=s, forum=f, posts=p))
    sc = B.term_scale(tmp_path, LOGIN, T0 + 3600)
    assert sc["posts"] == pytest.approx((12 + 0) / 2) and sc["forum"] == pytest.approx(
        0.02
    )
    w = B.fixed_weights(dict(sc, profit=0, revenue=None))["w"]
    assert all(math.isfinite(v) and v > 0 for v in w.values())
    assert B.term_scale(tmp_path / "none", LOGIN, T0) == {}


# ---------------------------------------------------------------- loop level
@pytest.fixture
def env(tmp_path):
    w = make_work(tmp_path)
    pol = tmp_path / "policy.json"
    pol.write_bytes((R3 / "policy.json").read_bytes())
    sdir = tmp_path / "st"
    sdir.mkdir()
    (sdir / "arms.json").write_text(json.dumps({ARM: [50.0, 1e6]}))
    prop, appr = Fake([pick, '{"action": "hold", "why": "x"}']), Fake([APPROVE])
    ctx = loop.Ctx(
        work=w,
        sdir=sdir,
        policy=pol,
        llms={"prop": prop, "appr": appr},
        fetch=lambda u: (500, b""),
        audit=tmp_path / "audit.jsonl",
    )
    return {
        "ctx": ctx,
        "pol": pol,
        "orig": pol.read_bytes(),
        "prop": prop,
        "appr": appr,
        "sdir": sdir,
    }


def _L(env):
    return json.loads((env["sdir"] / "loop.json").read_text())


def test_block_row_flags_missing_snapshots(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    blk = _L(env)["active"]["blocks"][0]
    loop.step(blk["t1_nom"] + 1, ctx=env["ctx"])
    closed = _L(env)["active"]["blocks"][0]
    assert closed["board_missing"] == sorted(B.BOARD_TERMS)
    assert closed["board"] == {f"{t}_h": 0.0 for t in B.BOARD_TERMS}
    assert closed["stars_guard"] is False and t0 < closed["t1"]


def test_stars_guard_flags_the_block_and_reaches_the_approver(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    blk = _L(env)["active"]["blocks"][0]
    snap(env["sdir"], blk["t0"], board(stars=4.69))
    snap(env["sdir"], blk["t1_nom"], board(stars=4.63))
    loop.step(blk["t1_nom"] + 1, ctx=env["ctx"])
    L = _L(env)
    closed = L["active"]["blocks"][0]
    assert closed["stars_guard"] is True and closed["stars_drop"] == pytest.approx(0.06)
    assert closed["board_missing"] == [] and L["active"]["stars_guard_trips"] == 1
    g = [json.loads(x) for x in (env["sdir"] / "guards.jsonl").read_text().splitlines()]
    assert g[0]["paths"] == ["defaults.food.price_mult"] and g[0]["arm"] == "B"
    nums = loop.guard_nums(env["ctx"], ["defaults.food.price_mult"], blk["t1_nom"] + 2)
    assert nums == {
        "threshold": 0.05,
        "trips_24h": 1,
        "worst_drop_24h": 0.06,
        "trips_same_path": 1,
    }
    assert t0 > 0


def test_stars_guard_is_in_the_approver_prompt_without_the_why(env):
    (env["sdir"] / "guards.jsonl").write_text(
        json.dumps(
            {
                "ts": NOW0 - 600,
                "exp": "e0000",
                "arm": "B",
                "block": 1,
                "paths": ["defaults.food.price_mult"],
                "stars_drop": 0.07,
            }
        )
        + "\n"
    )
    loop.step(NOW0, ctx=env["ctx"])
    p = env["appr"].prompts[0]
    nums = json.loads(p.split("INPUT ", 1)[1])
    assert nums["stars_guard"] == {
        "threshold": 0.05,
        "trips_24h": 1,
        "worst_drop_24h": 0.07,
        "trips_same_path": 1,
    }
    assert "WHY-SECRET" not in p and WHY not in p and "why" not in nums


# ---------------------------------------------------------------- forum family
SPACE = SP.load()
FORUM = {e["path"]: e for e in SPACE if e["family"] == "forum"}


def fchk(path, value, pol=POL):
    return SP.check([{"path": path, "value": value}], pol, SPACE, [], {})


def test_forum_family_is_long_block_buyer_side_and_excludes_fixed_keys():
    assert set(FORUM) == {
        "forum.window_h",
        "forum.max_refs",
        "forum.rotate",
        "forum.min_settles",
    }
    lo, hi = json.loads((SP.HERE / "action_space.json").read_text())[
        "long_block_steps_range"
    ]
    for e in FORUM.values():
        assert e["protocol"] == "long" and lo <= e["block_steps"] <= hi
        assert e["side"] == "buyer" and e["type"] == "int"
    for p in ("forum.enabled", "forum.interval_s"):
        assert "allowlist" in fchk(p, 600 if p.endswith("_s") else False)[0][0]


def test_forum_keys_are_gated_by_range_and_step():
    cur = {p: SP.current(POL, p, e) for p, e in FORUM.items()}
    assert cur == {
        "forum.window_h": 3,
        "forum.max_refs": 8,
        "forum.rotate": 4,
        "forum.min_settles": 3,
    }
    for p, e in FORUM.items():
        up = SP.step_value(e, cur[p], "up")
        if up is not None:
            assert fchk(p, up)[1], p
            assert (
                "max step" in fchk(p, min(e["max"], cur[p] + e["max_step"] + 1))[0][0]
                or cur[p] + e["max_step"] + 1 > e["max"]
            )
        assert "outside" in fchk(p, e["max"] + 1)[0][0]
        assert "outside" in fchk(p, e["min"] - 1)[0][0]
    assert "integer" in fchk("forum.window_h", 3.5)[0][0]
    assert (
        "mix"
        in SP.check(
            [
                {"path": "forum.window_h", "value": 4},
                {"path": "defaults.food.price_mult", "value": 0.95},
            ],
            POL,
            SPACE,
            [],
            {},
        )[0][0]
    )


@pytest.mark.parametrize("edge", ["min", "max"])
def test_hooks_forum_accepts_the_range_edges(tmp_path, monkeypatch, edge):
    sys.path.insert(0, str(R3 / "hooks"))
    import forum

    cfg = {p.split(".")[1]: e[edge] for p, e in FORUM.items()}
    st, md = tmp_path / "state", tmp_path / "market"
    st.mkdir()
    md.mkdir()
    cat = {
        f"k{i}": {
            "sku": f"k{i}",
            "category": f"식품>c{i}>x",
            "list_price": 10000,
            "name": f"n{i}",
        }
        for i in range(10)
    }
    (md / "catalog.json").write_text(
        json.dumps(cat, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("R3_STATE", str(st))
    now, rows = 1793800000.0, []
    for i in range(300):
        rows.append(
            {
                "seq": i,
                "ts": now - 3000 + i * 10,
                "type": "settle",
                "src": "market",
                "data": {"sku": f"k{i % 10}", "amount": 8000 + i, "ledger": 1000 + i},
            }
        )
    (st / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    pol = {"forum": dict(cfg, interval_s=0)}
    for k in range(cfg["rotate"] + 2):
        (md / "outbox.jsonl").unlink(missing_ok=True)
        why = forum.run_once(md, now=now + k, pol=pol)
        assert why.startswith("queued"), why
    posts = [json.loads(x) for x in (st / "forum_posts.jsonl").read_text().splitlines()]
    assert all(1 <= len(p["refs"]) <= cfg["max_refs"] for p in posts)
    assert f"최근 {cfg['window_h']}시간" in posts[0]["post"]
    assert (
        len(json.loads((st / "forum_state.json").read_text())["recent_cats"])
        == cfg["rotate"]
    )


def test_check_copy_with_a_forum_edit_passes_policy_validate(
    env, tmp_path, monkeypatch, capsys
):
    import policy

    arm = "forum.window_h|up"
    monkeypatch.setattr(
        loop.X,
        "thompson",
        lambda arms, post, pri, seed, k=3: [(arm, 9.0, 0.0)] if arm in arms else [],
    )
    env["prop"].replies = [
        '{"action": "change", "edits": [{"path": "forum.window_h", '
        '"value": 4}], "why": "votes"}'
    ]
    ctx_kw = {
        "work": env["ctx"].work,
        "policy": env["pol"],
        "llms": {"prop": env["prop"], "appr": env["appr"]},
        "fetch": lambda u: (200, json.dumps(board()).encode()),
    }
    monkeypatch.setattr(loop, "Ctx", functools.partial(loop.Ctx, **ctx_kw))
    out = tmp_path / "copy.json"
    code = loop.main(["--check", "--policy-out", str(out)])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, summary
    assert "synthetic" not in summary and summary["edits"] == [["forum.window_h", 3, 4]]
    copy = json.loads(out.read_bytes())
    assert copy["forum"]["window_h"] == 4 and copy["forum"]["interval_s"] == 300
    policy.validate(copy)
    assert env["pol"].read_bytes() == env["orig"]
    nums = json.loads(env["appr"].prompts[-1].split("INPUT ", 1)[1])
    assert "stars_guard" in nums
