"""icrl reward over all 7 official terms (DESIGN.md sec 5), each lane on the terms it can move: shop = profit, revenue
and ledger stars, buyer = surplus and deals, forum = ledger posts plus the board forum score in long blocks only;
missing-snapshot flags, finite fallback weights, the forum allowlist family (gate, --check copy, hooks/forum.py at
the range edges; no lane offers it) and the stars guard in the shop-side lanes' proposer state. Synthetic boards, no
network."""

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

from tests.test_icrl_loop import ARM, NOW0, WHY, Fake, make_work, pick, start

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


BASE = {"n": 1000, "avg": 4.5}


def ledger_store():
    """1 h block [T0, T0 + 3600): one own sale (rev 25000, gp 10000), one buyer deal (surplus 10000), two own-shop
    reviews (5 and 2 stars) and 12 own posts."""
    s = ST.empty()
    s["units"]["u1"] = ["F1", "c", "food", 15000, T0 - 100, None]
    s["sold"]["u1"] = [T0 + 600, 25000, 15000]
    s["deals"] = [[T0 + 600, 20000, 30000]]
    s["reviews"] = [[T0 + 700, 5], [T0 + 900, 2]]
    s["posts"] = [T0 + 300 * i for i in range(12)]
    return s


# (5 + 2 - 2 x 4.5) / (1000 + 2) / 1 h
STARS_H = -2 / 1002


def exp_of(side, w, protocol="switchback", lane=None):
    return {"side": side, "protocol": protocol, "w": w, "stars_base": BASE, "lane": lane}


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


def test_each_lane_uses_only_its_own_terms_and_no_board_term_in_switchback(tmp_path):
    snap(tmp_path, T0, board(stars=4.69, forum=0.60, posts=250))
    snap(tmp_path, T0 + 3600, board(stars=4.60, forum=0.65, posts=262))
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    m = {
        ln: loop.measure(store, None, exp_of(sd, w, lane=ln), T0, T0 + 3600, tmp_path, LOGIN)
        for ln, sd in (("shop", "shop"), ("buyer", "buyer"), ("forum", "buyer"))
    }
    assert m["shop"]["metric"] == pytest.approx(
        w["profit"] * 10000 + w["revenue"] * 25000 + w["stars_rank"] * STARS_H
    )
    assert m["buyer"]["metric"] == pytest.approx(w["surplus"] * 10000 + w["deals"] * 1)
    assert m["forum"]["metric"] == pytest.approx(w["posts"] * 12)  # switchback: posts only
    for x in m.values():  # snapshots exist, yet no board term enters a switchback block
        assert x["board"] == {} and x["board_missing"] == []
    assert m["shop"]["stars_drop"] == pytest.approx(0.09)  # the guard still reads the board
    assert m["shop"]["ledger"] == {"stars_rank_h": pytest.approx(STARS_H, abs=1e-6), "reviews": 2, "posts_h": 12.0}
    # moving every board snapshot does not move any switchback metric
    snap(tmp_path, T0 + 3600, board(stars=3.0, forum=0.99, posts=999))
    for ln, sd in (("shop", "shop"), ("buyer", "buyer"), ("forum", "buyer")):
        again = loop.measure(store, None, exp_of(sd, w, lane=ln), T0, T0 + 3600, tmp_path, LOGIN)
        assert again["metric"] == pytest.approx(m[ln]["metric"])


def test_switchback_pair_diffs_contain_no_board_term(tmp_path):
    """Two blocks with identical ledgers but very different board snapshots: the pair difference is 0."""
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    for t, st in ((T0 + 3600, 4.69), (T0 + 7200, 3.0)):
        snap(tmp_path, t, board(stars=st, forum=st / 10, posts=int(st * 100)))
    snap(tmp_path, T0, board(stars=4.69))
    store2 = ledger_store()
    for k in ("reviews", "deals"):
        store2[k] = [[r[0] + 3600] + r[1:] for r in store[k]]
    store2["posts"] = [x + 3600 for x in store["posts"]]
    store2["sold"]["u1"] = [T0 + 4200, 25000, 15000]
    blocks = []
    for i, (t0, stv) in enumerate(((T0, store), (T0 + 3600, store2))):
        for ln, sd in (("shop", "shop"), ("buyer", "buyer"), ("forum", "buyer")):
            mm = loop.measure(stv, None, exp_of(sd, w, lane=ln), t0, t0 + 3600, tmp_path, LOGIN)
            blocks.append((ln, {"i": i, "arm": "AB"[i], "metric": mm["metric"]}))
    for ln in ("shop", "buyer", "forum"):
        d = loop.X.pair_diffs([b for x, b in blocks if x == ln])
        assert d == [pytest.approx(0.0, abs=1e-12)], (ln, d)


def test_ledger_stars_sign_and_scale():
    store = ST.empty()
    store["reviews"] = [[T0 + 60 * i, 5] for i in range(10)]
    m = ST.block_metrics(store, T0, T0 + 3600)
    assert (m["rv_k"], m["rv_sum"]) == (10, 50)
    up = loop.stars_h(m, {"n": 1976, "avg": 4.69})
    assert up == pytest.approx(10 * (5 - 4.69) / 1986) and up > 0
    store["reviews"] = [[T0 + 60 * i, 1] for i in range(10)]
    dn = loop.stars_h(ST.block_metrics(store, T0, T0 + 3600), {"n": 1976, "avg": 4.69})
    assert dn == pytest.approx(10 * (1 - 4.69) / 1986) and dn < 0
    # per hour: the same reviews over a 2 h block give half the rate
    m2 = ST.block_metrics(store, T0, T0 + 7200)
    assert loop.stars_h(m2, {"n": 1976, "avg": 4.69}) == pytest.approx(dn / 2)
    assert loop.stars_h(m, None) == 0.0 and loop.stars_h(m, {"n": 0, "avg": None}) == 0.0


def test_ledger_reviews_and_posts_are_ours_only(tmp_path):
    ev = [
        {"ts": T0 + 1, "type": "review", "src": "market", "role": "shop", "mine": True,
         "data": {"shop": "https://ours.example", "stars": 4}},
        {"ts": T0 + 2, "type": "review", "src": "market", "role": "buyer", "mine": True,
         "data": {"shop": "https://other.example", "stars": 1}},  # our buyer's review of another shop
        {"ts": T0 + 3, "type": "review", "src": "hook", "role": "buyer", "mine": True,
         "data": {"shop": "https://other.example", "stars": 5}},
        {"ts": T0 + 4, "type": "forum", "src": "forum", "role": "market", "mine": False,
         "data": {"author": "lee-sungsoo", "role": "buyer", "text": "x"}},
        {"ts": T0 + 5, "type": "forum", "src": "forum", "role": "market", "mine": False,
         "data": {"author": "lee-sungsoo", "role": "shop", "text": "x"}},
        {"ts": T0 + 6, "type": "forum", "src": "forum", "role": "market", "mine": False,
         "data": {"author": "someone", "role": "buyer", "text": "x"}},
    ]
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    P = {"events": tmp_path / "events.jsonl", "kit": tmp_path / "none.log"}
    s = ST.update(ST.empty(), P, T0 + 60, {}, {"lee-sungsoo"})
    assert s["reviews"] == [[T0 + 1, 4]] and s["rv_all"] == [1, 4] and s["posts"] == [T0 + 4]
    # an old store without review rows is rebuilt from offset 0
    old = {k: v for k, v in s.items() if k not in ("reviews", "rv_all", "posts")}
    ST.save_store(tmp_path / "store.json", old)
    assert ST.load_store(tmp_path / "store.json")["off"] == 0


@pytest.mark.skipif(
    not (SCRATCH / "data_20261104" / ".r3" / "events.jsonl").exists(), reason="real data copy missing"
)
def test_real_ledger_identifies_our_reviews_and_posts():
    d = SCRATCH / "data_20261104" / ".r3"
    P = {"events": d / "events.jsonl", "kit": d / "none.log"}
    s = ST.update(ST.empty(), P, 1793788878.46 + 60, {}, {"lee-sungsoo"})
    own = json.loads((d / "own_shops.json").read_text())["urls"]
    shops = set()
    for ln in (d / "events.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(ln)
        if e.get("type") == "review" and e.get("role") == "shop" and e.get("mine") is True:
            shops.add(e["data"]["shop"])
    print(f"own-shop reviews in the ledger {s['rv_all']}, own posts {len(s['posts'])}")
    assert shops == set(own) and s["rv_all"][0] == 286 and len(s["posts"]) == 263


def test_long_shop_metric_adds_ledger_stars_to_the_weighted_value_change(tmp_path):
    snap(tmp_path, T0, board(stars=4.69))
    snap(tmp_path, T0 + 3600, board(stars=4.70))
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    stats = {"category": {}, "sku": {}}
    m = loop.measure(
        store, stats, exp_of("shop", w, "long"), T0, T0 + 3600, tmp_path, LOGIN
    )
    v = ST.block_metrics(store, T0, T0 + 3600, stats)["value_h"]
    assert m["metric"] == pytest.approx(w["profit"] * v + w["stars_rank"] * STARS_H)


def test_long_forum_block_adds_the_board_forum_term_and_flags_missing(tmp_path):
    w = B.weights(board(), LOGIN)["w"]
    store = ledger_store()
    fx = exp_of("buyer", w, "long", lane="forum")
    m = loop.measure(store, None, fx, T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == ["forum"] and m["metric"] == pytest.approx(w["posts"] * 12)
    snap(tmp_path, T0, board(forum=0.60))
    snap(tmp_path, T0 + 3600, board(forum=0.65))
    m = loop.measure(store, None, fx, T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == [] and m["board"] == {"forum_h": pytest.approx(0.05)}
    assert m["metric"] == pytest.approx(w["posts"] * 12 + w["forum"] * 0.05)
    no_forum = board()
    no_forum["buyers"][0]["forum"] = None
    snap(tmp_path, T0 + 3600, no_forum)
    m = loop.measure(store, None, fx, T0, T0 + 3600, tmp_path, LOGIN)
    assert m["board_missing"] == ["forum"] and m["board"]["forum_h"] == 0.0
    m = loop.measure(store, None, fx, T0, T0 + 3600, None, LOGIN)
    assert m["board_missing"] == ["forum"]


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
    prop = Fake([pick, '{"action": "hold", "why": "x"}'])
    ctx = loop.Ctx(
        work=w,
        sdir=sdir,
        policy=pol,
        llms={"prop": prop},
        fetch=lambda u: (500, b""),
        audit=tmp_path / "audit.jsonl",
    )
    return {
        "ctx": ctx,
        "pol": pol,
        "orig": pol.read_bytes(),
        "prop": prop,
        "sdir": sdir,
    }


def _L(env):
    return json.loads((env["sdir"] / "loop.json").read_text())


def _shop(env):
    return _L(env)["lanes"]["price"]["active"]


def test_shop_block_row_has_ledger_terms_and_no_board_term(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    blk = _shop(env)["blocks"][0]
    loop.step(blk["t1_nom"] + 1, ctx=env["ctx"])
    closed = _shop(env)["blocks"][0]
    assert closed["board_missing"] == [] and closed["board"] == {}
    assert set(closed["ledger"]) == {"stars_rank_h", "reviews", "posts_h"}
    assert closed["stars_guard"] is False and t0 < closed["t1"]


def test_stars_guard_flags_the_block_and_counts_trips(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    blk = _shop(env)["blocks"][0]
    snap(env["sdir"], blk["t0"], board(stars=4.69))
    snap(env["sdir"], blk["t1_nom"], board(stars=4.63))
    loop.step(blk["t1_nom"] + 1, ctx=env["ctx"])
    exp = _shop(env)
    closed = exp["blocks"][0]
    assert closed["stars_guard"] is True and closed["stars_drop"] == pytest.approx(0.06)
    assert closed["board_missing"] == [] and exp["stars_guard_trips"] == 1
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


def test_stars_guard_is_in_the_shop_side_proposer_state_only(env):
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
    state = {
        ln: json.loads([x for x in p.splitlines() if x.startswith("STATE ")][0][6:])
        for p in env["prop"].prompts
        for ln in [p.split("\nLANE ", 1)[1].split("\n", 1)[0]]
    }
    assert state["price"]["stars_guard"] == {
        "threshold": 0.05,
        "trips_24h": 1,
        "worst_drop_24h": 0.07,
        "trips_same_path": 1,
    }
    assert state["stock"]["stars_guard"] == dict(state["price"]["stars_guard"], trips_same_path=0)
    assert "stars_guard" not in state["buyer"]
    assert all(WHY not in p for p in env["prop"].prompts)


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


def test_check_refuses_a_forum_edit_in_every_lane(env, tmp_path, monkeypatch, capsys):
    import policy

    env["prop"].replies = [
        '{"action": "change", "edits": [{"path": "forum.window_h", '
        '"value": 4}], "why": "votes"}'
    ]
    ctx_kw = {
        "work": env["ctx"].work,
        "policy": env["pol"],
        "llms": {"prop": env["prop"]},
        "fetch": lambda u: (200, json.dumps(board()).encode()),
    }
    monkeypatch.setattr(loop, "Ctx", functools.partial(loop.Ctx, **ctx_kw))
    out = tmp_path / "copy.json"
    code = loop.main(["--check", "--policy-out", str(out)])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, summary
    assert "synthetic" in summary and "lane" not in summary  # no lane took the forum edit
    assert all(e[0].split(".")[0] != "forum" for e in summary["edits"])
    copy = json.loads(out.read_bytes())
    assert copy["forum"]["window_h"] == 3 and copy["forum"]["interval_s"] == 300
    policy.validate(copy)
    assert not any(
        loop.arm_lane(SP.load(), {"path": p}) for p in FORUM
    )  # no lane offers a forum arm
    assert env["pol"].read_bytes() == env["orig"]
    assert len(env["prop"].prompts) == 3  # one proposer call per lane, nothing else
