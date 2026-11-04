"""icrl lanes (DESIGN.md sec 6, 7, 8): price / stock / buyer experiments running together, path-level A/B writes,
external edits aborting every lane, migration of the old single-active loop.json and of the old shop / buyer / forum
lanes (forum ended as lane_off), no forum arm in any lane, no approver call, the supply / structure switchback with
its washout step, the whole-shop loss cap over both shop-side lanes, independent A/B orders, harness.json per side,
and the robustness fixes F5-F7. Fake clock, fake proposer, no network."""

import json
import types
from pathlib import Path

import pytest
from icrl import board as B
from icrl import loop
from icrl import space as SP
from icrl import state as ST

from tests.test_icrl_loop import HOLD, NOW0, Fake, make_work

R3 = Path(__file__).resolve().parents[1]
CAT_ARM = "categories.가구/침구>가구>침대/매트리스.breadth_qty|down"
CAT_PATH = CAT_ARM.split("|")[0]
PREF = {
    "price": "defaults.food.price_mult|up",
    "stock": CAT_ARM,
    "buyer": "buyer.max_shops|down",
}


def lane_of(prompt):
    return prompt.split("\nLANE ", 1)[1].split("\n", 1)[0]


def picker(pref):
    """Fake proposer: in each lane, change to the preferred arm's suggested value when it is offered, else hold."""

    def f(prompt):
        arms = json.loads(
            [x for x in prompt.splitlines() if x.startswith("ARMS ")][0][5:]
        )
        a = arms.get(pref.get(lane_of(prompt)))
        if not a:
            return HOLD
        return json.dumps(
            {
                "action": "change",
                "edits": [{"path": a["path"], "value": a["suggest"]}],
                "why": "w",
            }
        )

    return f


def rows(p):
    return (
        [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines()]
        if Path(p).exists()
        else []
    )


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def make_env(tmp_path, **kw):
    w = make_work(tmp_path, **kw)

    pol = tmp_path / "policy.json"
    pol.write_bytes((R3 / "policy.json").read_bytes())
    sdir = tmp_path / "st"
    sdir.mkdir()
    (sdir / "arms.json").write_text(
        json.dumps({a: [50.0, 1e6] for a in PREF.values()})
    )
    prop = Fake([picker(PREF)])
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


def L_of(env):
    return json.loads((env["sdir"] / "loop.json").read_text())


def all_b(monkeypatch):
    monkeypatch.setattr(loop.X, "block_arm", lambda seed, i: "B")


def start_all(env, monkeypatch):
    all_b(monkeypatch)
    r = loop.step(NOW0, ctx=env["ctx"])
    assert {ln: c.get("started") for ln, c in r["cycles"].items()} == {
        "price": "e0001",
        "stock": "e0002",
        "buyer": "e0003",
    }, r
    t = max(x["active"]["t_start"] for x in L_of(env)["lanes"].values())
    r = loop.step(t + 1, ctx=env["ctx"])
    assert r["exp"] == {"price": "started", "stock": "started", "buyer": "started"}, r
    return t + 1


# ---------------------------------------------------------------- lanes together
def test_three_lanes_run_concurrently_with_one_proposer_call_each(env, monkeypatch):
    t = start_all(env, monkeypatch)
    live = json.loads(env["pol"].read_bytes())
    orig = json.loads(env["orig"])
    assert live["defaults"]["food"]["price_mult"] == pytest.approx(
        orig["defaults"]["food"]["price_mult"] + 0.03
    )
    assert live["buyer"]["max_shops"] == orig["buyer"]["max_shops"] - 1
    assert SP.get(live, CAT_PATH)[0] == SP.get(orig, CAT_PATH)[0] - 1
    assert live["forum"] == orig["forum"]
    assert len(env["prop"].prompts) == 3
    assert [lane_of(p) for p in env["prop"].prompts] == ["price", "stock", "buyer"]
    # each lane is offered only its own arms; the others' running experiments are context
    for p in env["prop"].prompts:
        arms = json.loads([x for x in p.splitlines() if x.startswith("ARMS ")][0][5:])
        assert {loop.arm_lane(env["ctx"].space, a) for a in arms.values()} == {
            lane_of(p)
        }
    st = json.loads(
        [x for x in env["prop"].prompts[2].splitlines() if x.startswith("STATE ")][0][
            6:
        ]
    )
    assert set(st["lanes"]) == {"price", "stock"}
    starts = [
        x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "start"
    ]
    assert [x["lane"] for x in starts] == ["price", "stock", "buyer"]
    cyc = [x for x in rows(env["sdir"] / "log.jsonl") if x["ev"] == "cycle"]
    for c in cyc:
        assert {"lane", "side", "proposer", "guards", "harness_version"} <= set(c)
        assert c["side"] == loop.LANE_SIDE[c["lane"]]
        assert (
            c["proposer"] == "change"
            and c["guards"] == []
            and c["harness_version"] is None
        )
    # no approver: only the proposer role was called
    assert {x["role"] for x in rows(env["sdir"] / "llm.jsonl")} == {"prop"}
    assert "approver" not in json.dumps(cyc)
    # busy lanes are not proposed to again
    loop.step(t + loop.STEP_S, ctx=env["ctx"])
    assert len(env["prop"].prompts) == 3


def test_a_lane_proposal_outside_its_lane_is_refused(env):
    env["prop"].replies = [
        json.dumps(
            {
                "action": "change",
                "edits": [{"path": "buyer.max_shops", "value": 5}],
                "why": "x",
            }
        )
    ]
    r = loop.step(NOW0, ctx=env["ctx"])
    assert r["cycles"]["price"]["guards"] == ["edits outside the price lane"]
    assert r["cycles"]["stock"]["guards"] == ["edits outside the stock lane"]
    assert r["cycles"]["buyer"]["started"] == "e0001"


def test_no_lane_offers_or_takes_a_forum_edit(env):
    space = env["ctx"].space
    assert {e["family"] for e in space} >= {"forum"}  # still listed in action_space.json
    assert all(loop.lane_of_path(space, e["path"]) is None for e in space if e["family"] == "forum")
    assert "_note_forum" in json.loads((SP.HERE / "action_space.json").read_text(encoding="utf-8"))
    env["prop"].replies = [
        json.dumps({"action": "change", "edits": [{"path": "forum.rotate", "value": 5}], "why": "x"})
    ]
    r = loop.step(NOW0, ctx=env["ctx"])
    assert {ln: c["guards"] for ln, c in r["cycles"].items()} == {
        ln: [f"edits outside the {ln} lane"] for ln in loop.LANES
    }
    for p in env["prop"].prompts:
        arms = json.loads([x for x in p.splitlines() if x.startswith("ARMS ")][0][5:])
        assert not any(a.startswith("forum.") for a in arms)
    assert env["pol"].read_bytes() == env["orig"] and not (env["sdir"] / "experiments.jsonl").exists()


def test_path_level_restore_when_two_lanes_overlap(env, monkeypatch):
    t = start_all(env, monkeypatch)
    ctx = env["ctx"]
    orig = json.loads(env["orig"])
    L = L_of(env)
    L["pol"] = json.loads(env["pol"].read_bytes())
    order = [
        ("price", "defaults.food.price_mult"),
        ("stock", CAT_PATH),
        ("buyer", "buyer.max_shops"),
    ]
    for i, (ln, path) in enumerate(order):
        loop.end(ctx, L, L["lanes"][ln]["active"], "reject", None, t + 60)
        live = json.loads(env["pol"].read_bytes())
        assert SP.get(live, path) == SP.get(orig, path)  # this lane's path back to A
        for ln2, p2 in order[i + 1 :]:  # the other lanes' B values untouched
            assert SP.get(live, p2) != SP.get(orig, p2)
        if i < 2:
            assert env["pol"].read_bytes() == SP.dumps_like(env["orig"], live)
    assert (
        env["pol"].read_bytes() == env["orig"]
    )  # the last lane alone restores byte-identically


def test_arm_bytes_restores_a_new_category_override_by_removing_it(env):
    ctx = env["ctx"]
    L = {}
    loop.migrate(ctx, L)
    loop.check_live(ctx, L, NOW0)
    new_cat = "식품>김치/반찬/즉석요리>김치"
    shop = {
        "id": "s",
        "lane": "stock",
        "A_version": L["live_version"],
        "edits": [{"path": f"categories.{new_cat}.stock_target", "value": 5}],
    }
    buyer = {
        "id": "b",
        "lane": "buyer",
        "A_version": L["live_version"],
        "edits": [{"path": "buyer.max_shops", "value": 5}],
    }
    for x in (shop, buyer):
        env["pol"].write_bytes(loop.arm_bytes(ctx, x, "B"))
    live = json.loads(env["pol"].read_bytes())
    assert (
        live["categories"][new_cat] == {"stock_target": 5}
        and live["buyer"]["max_shops"] == 5
    )
    env["pol"].write_bytes(loop.arm_bytes(ctx, shop, "A"))
    live = json.loads(env["pol"].read_bytes())
    want = json.loads(env["orig"])
    want["buyer"]["max_shops"] = 5
    assert live == want and list(live["categories"]) == list(want["categories"])
    env["pol"].write_bytes(loop.arm_bytes(ctx, buyer, "A"))
    assert env["pol"].read_bytes() == env["orig"]


def test_external_edit_aborts_only_the_lanes_it_touches(env, monkeypatch):
    """v1.25: an outside edit of a key no lane uses keeps every experiment running (the open blocks are marked
    ext_edit, so their pairs are left out); an edit of a price-family key aborts the price lane only. The live
    edit is never overwritten."""
    t = start_all(env, monkeypatch)
    edited = env["pol"].read_bytes().replace(b'"version": 10', b'"version": 11')
    assert edited != env["pol"].read_bytes()
    env["pol"].write_bytes(edited)
    env["prop"].replies = [HOLD]
    r = loop.step(t + 600, ctx=env["ctx"])
    assert r["live"] == "external" and env["pol"].read_bytes() == edited
    assert not [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    lanes = L_of(env)["lanes"]
    assert all(s["active"]["blocks"][-1].get("ext_edit") for s in lanes.values())
    alarm = [x for x in rows(env["sdir"] / "log.jsonl") if x.get("what") == "external_edit"]
    assert alarm[-1]["aborted"] == [] and sorted(alarm[-1]["kept"]) == ["e0001", "e0002", "e0003"]
    assert alarm[-1]["paths"] == ["version"]
    pol = json.loads(edited)
    pol["defaults"]["goods"]["min_margin"] = 0.123  # price family, not e0001's own path (food price_mult)
    edited2 = SP.dumps_like(edited, pol)
    env["pol"].write_bytes(edited2)
    r = loop.step(t + 650, ctx=env["ctx"])
    assert r["live"] == "external" and env["pol"].read_bytes() == edited2
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert [(x["lane"], x["verdict"]) for x in ends] == [("price", "aborted_external_edit")]
    lanes = L_of(env)["lanes"]
    assert lanes["price"]["active"] is None and lanes["stock"]["active"] and lanes["buyer"]["active"]
    alarm = [x for x in rows(env["sdir"] / "log.jsonl") if x.get("what") == "external_edit"]
    assert alarm[-1]["aborted"] == ["e0001"] and sorted(alarm[-1]["kept"]) == ["e0002", "e0003"]


def test_stop_and_freeze_end_every_lane(env, monkeypatch):
    t = start_all(env, monkeypatch)
    (env["sdir"] / "STOP").write_text("")
    assert loop.step(t + 600, ctx=env["ctx"])["status"] == "stopped"
    assert env["pol"].read_bytes() == env["orig"]
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert sorted(x["lane"] for x in ends if x["verdict"] == "stopped") == [
        "buyer",
        "price",
        "stock",
    ]


# ---------------------------------------------------------------- migration
E0001 = {
    "id": "e0001",
    "arm": "categories.가구/침구>침구/홈데코>침구.breadth_qty|up",
    "mode": "explore",
    "protocol": "long",
    "block_steps": 9,
    "side": "shop",
    "status": "running",
    "t_start": 1793790000,
    "blocks": [{"i": 0, "arm": "B", "t0": 1793790001, "t1_nom": 1793800800}],
    "edits": [
        {
            "path": "categories.가구/침구>침구/홈데코>침구.breadth_qty",
            "current": 1,
            "value": 2,
            "dir": "up",
        }
    ],
    "A_version": "v0002",
    "B_version": "v0003",
}


def test_migrate_moves_the_old_single_active_into_its_family_lane():
    import copy

    ctx = types.SimpleNamespace(space=SP.load())
    L = {
        "active": copy.deepcopy(E0001),
        "next_cycle_step": 4242,
        "n_exp": 1,
        "live_sha": "x",
    }
    lanes = loop.migrate(ctx, L)
    assert "active" not in L and "next_cycle_step" not in L and L["n_exp"] == 1
    assert lanes["stock"]["active"] == dict(E0001, lane="stock")
    assert {ln: s["next_cycle_step"] for ln, s in lanes.items()} == dict.fromkeys(
        loop.LANES, 4242
    )
    assert lanes["buyer"]["active"] is None and lanes["price"]["active"] is None
    assert (
        loop.migrate(ctx, L) is lanes and lanes["stock"]["active"]["id"] == "e0001"
    )  # idempotent


def test_migration_of_a_running_loop_keeps_the_experiment_schedule(env, monkeypatch):
    # run the shop lane alone to a running long category experiment under the OLD supply protocol (long, 9 steps),
    # then rewrite loop.json in the old format and switch the allowlist to the new protocol
    env["ctx"].space = [
        dict(e, protocol="long", block_steps=9, washout_steps=0) if e["family"] == "supply" else e
        for e in SP.load()
    ]
    env["prop"].replies = [picker({"stock": CAT_ARM})]
    loop.step(NOW0, ctx=env["ctx"])
    t_start = L_of(env)["lanes"]["stock"]["active"]["t_start"]
    assert loop.step(t_start + 1, ctx=env["ctx"])["exp"] == {"stock": "started"}
    L = L_of(env)
    exp = L["lanes"]["stock"]["active"]
    assert (
        exp["protocol"] == "long"
        and exp["block_steps"] == 9
        and exp["status"] == "running"
    )
    b_raw = env["pol"].read_bytes()
    assert b_raw != env["orig"]
    for k in ("lane", "stars_base", "washout_steps", "outcome"):
        exp.pop(k)
    env["ctx"].space = SP.load()
    old = {k: v for k, v in L.items() if k != "lanes"}
    old.update(active=exp, next_cycle_step=L["lanes"]["stock"]["next_cycle_step"])
    (env["sdir"] / "loop.json").write_text(json.dumps(old, ensure_ascii=False))
    n_versions = len(env["ctx"].runs.versions())
    env["prop"].replies = [HOLD]
    t1 = exp["blocks"][0]["t1_nom"]
    r = loop.step(t1 - 700, ctx=env["ctx"])
    assert r["exp"] == {"stock": "running"} and set(r["cycles"]) == {"price", "buyer"}
    new = L_of(env)
    got = new["lanes"]["stock"]["active"]
    assert (
        got["lane"] == "stock"
        and got["id"] == exp["id"]
        and got["blocks"] == exp["blocks"]
    )
    assert got["t_start"] == exp["t_start"] and got["A_version"] == exp["A_version"]
    assert "active" not in new and "next_cycle_step" not in new
    assert (
        env["pol"].read_bytes() == b_raw
        and len(env["ctx"].runs.versions()) == n_versions
    )
    r = loop.step(t1 + 1, ctx=env["ctx"])
    assert r["exp"]["stock"] in ("adopt", "reject")  # the long verdict, not a switchback look
    end = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"][
        -1
    ]
    assert end["id"] == exp["id"] and end["lane"] == "stock"
    assert end["protocol"] == "long" and end["blocks"] == 1 and end["stats"]["evidence"] == "weak"
    assert rows(env["sdir"] / "lessons.jsonl")[-1]["lane"] == "stock"


# ---------------------------------------------------------------- supply switchback, washout
def test_supply_and_structure_keys_get_the_switchback_protocol_with_washout():
    fam = {e["family"]: (e["protocol"], e["block_steps"], e.get("washout_steps", 0)) for e in SP.load()}
    assert fam["supply"] == fam["structure"] == ("switchback", 2, 1)   # v1.24: 40 min blocks, first 20 min washout
    assert fam["price"] == fam["buyer"] == ("switchback", 1, 0)         # v1.24: 20 min blocks
    assert fam["forum"] == ("long", 12, 0)


def test_a_new_supply_experiment_is_a_whole_shop_switchback(env):
    env["prop"].replies = [picker({"stock": CAT_ARM})]
    loop.step(NOW0, ctx=env["ctx"])
    exp = L_of(env)["lanes"]["stock"]["active"]
    assert (exp["protocol"], exp["block_steps"], exp["washout_steps"], exp["outcome"]) == ("switchback", 2, 1, "value")
    w = {"profit": 0.5, "revenue": 0.3, "stars_rank": 0.2}
    m = {"value_h": 100.0, "gp_h": 7.0, "rev_h": 40.0, "stars_rank_h": 0.5}
    assert loop.proxy(m, dict(exp, w=w)) == pytest.approx(0.5 * 100 + 0.3 * 40 + 0.2 * 0.5)
    assert loop.won(m, exp) == 100.0
    price = dict(exp, outcome=None, w=w)  # a price experiment keeps gross profit
    assert loop.proxy(m, price) == pytest.approx(0.5 * 7 + 0.3 * 40 + 0.2 * 0.5)


@pytest.mark.parametrize("lane,washout", [("stock", 1), ("price", 0)])
def test_the_washout_step_is_excluded_from_the_block_measurement(env, monkeypatch, lane, washout):
    all_b(monkeypatch)
    env["prop"].replies = [picker({lane: PREF[lane]})]
    loop.step(NOW0, ctx=env["ctx"])
    t_start = L_of(env)["lanes"][lane]["active"]["t_start"]
    loop.step(t_start + 1, ctx=env["ctx"])
    blk = L_of(env)["lanes"][lane]["active"]["blocks"][0]
    seen, real = [], loop.measure

    def spy(store, stats, exp, t0, t1, *a, **kw):
        seen.append((t0, t1))
        return real(store, stats, exp, t0, t1, *a, **kw)

    monkeypatch.setattr(loop, "measure", spy)
    now = blk["t1_nom"] + 1
    loop.step(now, ctx=env["ctx"])
    closed = L_of(env)["lanes"][lane]["active"]["blocks"][0]
    assert closed["t0_measured"] == blk["t0"] + washout * loop.STEP_S
    assert (blk["t0"] + washout * loop.STEP_S, now) in seen
    if washout:
        assert (blk["t0"], now) not in seen[1:]  # only the loss-cap check may start at t0
    # a sale inside the washout step does not move the block's metric
    store = ST.empty()
    store["units"]["u1"] = ["F1", "c", "goods", 1000, blk["t0"] - 10, None]
    store["sold"]["u1"] = [blk["t0"] + 60, 50000, 1000]
    exp = {"side": "shop", "protocol": "switchback", "outcome": None, "w": {"profit": 1.0, "revenue": 0.0}, "lane": "price"}
    assert real(store, None, exp, closed["t0_measured"], now)["metric"] == (0.0 if washout else pytest.approx(49000 / ((now - blk["t0"]) / 3600)))


# ---------------------------------------------------------------- harness.json
def H(env, obj):
    (env["sdir"] / "harness.json").write_text(
        obj if isinstance(obj, str) else json.dumps(obj)
    )


GOOD = {
    "n_lessons": 7,
    "recall": "recent_first",
    "prompt_notes": "prefer small moves",
    "features": ["time", "nope"],
}


@pytest.mark.parametrize(
    "obj",
    [
        None,
        "{not json",
        {"lanes": {"shop": GOOD}},
        {"version": "3", "lanes": {"shop": GOOD}},
        {"version": 3},
        {"version": 3, "lanes": {"shop": dict(GOOD, n_lessons=4)}},
        {"version": 3, "lanes": {"shop": dict(GOOD, n_lessons=41)}},
        {"version": 3, "lanes": {"shop": dict(GOOD, n_lessons=True)}},
        {"version": 3, "lanes": {"shop": dict(GOOD, recall="random")}},
        {"version": 3, "lanes": {"shop": dict(GOOD, prompt_notes="x" * 601)}},
        {"version": 3, "lanes": {"shop": dict(GOOD, features="time")}},
        {"version": 3, "lanes": {"shop": "x"}},
    ],
)
def test_harness_invalid_falls_back_to_defaults(env, obj):
    if obj is not None:
        H(env, obj)
    v, h, applied = loop.harness(env["ctx"], "price")
    assert h == loop.HARNESS_DEFAULT and applied is False
    assert v == (3 if isinstance(obj, dict) and obj.get("version") == 3 else None)


def test_harness_is_per_side_and_the_other_side_keeps_defaults(env):
    H(env, {"version": 5, "lanes": {"shop": GOOD, "forum": dict(GOOD, n_lessons=50), "price": dict(GOOD, n_lessons=9)}})
    for ln in ("price", "stock"):  # both shop-side lanes read the "shop" object, never a lane-named one
        v, h, applied = loop.harness(env["ctx"], ln)
        assert (v, applied) == (5, True)
        assert h == {
            "n_lessons": 7,
            "recall": "recent_first",
            "prompt_notes": "prefer small moves",
            "features": ["time"],
        }
    assert loop.harness(env["ctx"], "buyer") == (5, loop.HARNESS_DEFAULT, False)
    H(env, {"version": 5, "lanes": {"buyer": GOOD}})
    assert loop.harness(env["ctx"], "buyer")[2] is True
    assert loop.harness(env["ctx"], "price") == loop.harness(env["ctx"], "stock") == (5, loop.HARNESS_DEFAULT, False)
    H(env, {"version": 5, "lanes": {"shop": dict(GOOD, features=["nope"])}})
    assert (
        loop.harness(env["ctx"], "stock")[1]["features"] is None
    )  # no known name: all features


def test_harness_shapes_the_prompt_and_is_logged(env):
    env["prop"].replies = [HOLD]
    H(env, {"version": 9, "lanes": {"shop": GOOD}})
    loop.step(NOW0, ctx=env["ctx"])
    p = {lane_of(x): x for x in env["prop"].prompts}
    for ln in ("price", "stock"):
        assert p[ln].endswith("NOTES (harness)\nprefer small moves\n")
        st = json.loads(
            [x for x in p[ln].splitlines() if x.startswith("STATE ")][0][6:]
        )
        assert set(st) == {"time"}
    assert "NOTES (harness)" not in p["buyer"]
    sb = json.loads(
        [x for x in p["buyer"].splitlines() if x.startswith("STATE ")][0][6:]
    )
    assert {"time", "shop", "buyer", "market", "budget", "lanes"} <= set(sb)
    cyc = {x["lane"]: x for x in rows(env["sdir"] / "log.jsonl") if x["ev"] == "cycle"}
    assert {
        ln: (c["harness_version"], c["harness_applied"]) for ln, c in cyc.items()
    } == {
        "price": (9, True),
        "stock": (9, True),
        "buyer": (9, False),
    }
    assert all(c["proposer"] == "hold" and c["guards"] == [] for c in cyc.values())


def test_harness_defaults_reproduce_the_prompt_without_harness(env, tmp_path):
    env["prop"].replies = [HOLD]
    loop.step(NOW0, ctx=env["ctx"])
    without = list(env["prop"].prompts)
    w2 = make_work(tmp_path / "second")
    sdir2 = tmp_path / "st2"
    sdir2.mkdir()
    (sdir2 / "arms.json").write_bytes((env["sdir"] / "arms.json").read_bytes())
    (sdir2 / "harness.json").write_text(
        json.dumps({"version": 1, "lanes": {sd: {} for sd in loop.LANE_SIDE.values()}})
    )
    pol2 = tmp_path / "policy2.json"
    pol2.write_bytes(env["orig"])
    prop2 = Fake([HOLD])
    ctx2 = loop.Ctx(
        work=w2,
        sdir=sdir2,
        policy=pol2,
        llms={"prop": prop2},
        fetch=lambda u: (500, b""),
        audit=tmp_path / "audit2.jsonl",
    )
    loop.step(NOW0, ctx=ctx2)
    assert prop2.prompts == without
    assert all(loop.harness(ctx2, ln)[2] for ln in loop.LANES)


def test_recall_order_and_size_follow_the_harness(tmp_path, monkeypatch):
    from memory import lessons as LS

    view = {"exp": {f"a{i}|up": ("adopt", 100 + i) for i in range(12)}, "policy": {}}
    view["exp"]["keyX|up"] = ("reject", 1)
    monkeypatch.setattr(LS, "view", lambda memo, at=None: view)
    ctx = types.SimpleNamespace(f=lambda n: tmp_path / n)
    mem = ({"edges": {"memo": {}}}, [], 0)
    monkeypatch.setattr(loop, "_memview", lambda c: mem)
    g = loop.recall(ctx, [], ["keyX"], mem=mem, n_lessons=5, order="graph_first")
    assert [e["exp"] for e in g["edges"]][0] == "keyX|up" and len(g["edges"]) == 5
    r = loop.recall(ctx, [], ["keyX"], mem=mem, n_lessons=5, order="recent_first")
    assert [e["exp"] for e in r["edges"]] == [f"a{i}|up" for i in (11, 10, 9, 8, 7)]


# ---------------------------------------------------------------- F5-F7
def test_rows_skips_bad_and_non_object_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(
        '{"a": 1}\nnot json\n[1, 2]\n"s"\n\n{"b": 2}\n{"c": 3}', encoding="utf-8"
    )
    assert loop._rows(p) == [{"a": 1}, {"b": 2}, {"c": 3}]
    assert loop._rows(tmp_path / "missing.jsonl") == []


@pytest.mark.parametrize(
    "cursor",
    [
        [1, 2],
        {"lessons": [1]},
        {"lessons": "x"},
        {"lessons": {"path": "SAME", "n": "3"}},
        {"lessons": {"path": "SAME", "n": 2.5}},
        {"lessons": {"path": "SAME", "n": True}},
        {"lessons": {"path": "SAME", "n": -1}},
    ],
)
def test_memview_tolerates_wrong_typed_cursor(tmp_path, cursor):
    md = tmp_path / "mem"
    md.mkdir()
    (md / "graph.json").write_text(json.dumps({"edges": {"memo": {}}}))
    sdir = tmp_path / "st"
    sdir.mkdir()
    (sdir / "lessons.jsonl").write_text('{"exp": "e1"}\nbad\n{"exp": "e2"}\n')
    raw = json.dumps(cursor).replace("SAME", str((sdir / "lessons.jsonl").resolve()))
    (md / "cursor.json").write_text(raw)
    ctx = types.SimpleNamespace(P={"graph": md / "graph.json"}, f=lambda n: sdir / n)
    g, rows_, n = loop._memview(ctx)
    assert (
        g == {"edges": {"memo": {}}}
        and [r["exp"] for r in rows_] == ["e1", "e2"]
        and n == 0
    )


def test_memview_reads_a_good_cursor(tmp_path):
    md = tmp_path / "mem"
    md.mkdir()
    (md / "graph.json").write_text(json.dumps({"edges": {"memo": {}}}))
    sdir = tmp_path / "st"
    sdir.mkdir()
    (sdir / "lessons.jsonl").write_text('{"exp": "e1"}\n')
    (md / "cursor.json").write_text(
        json.dumps(
            {"lessons": {"path": str((sdir / "lessons.jsonl").resolve()), "n": 1}}
        )
    )
    ctx = types.SimpleNamespace(P={"graph": md / "graph.json"}, f=lambda n: sdir / n)
    assert loop._memview(ctx)[2] == 1


def test_board_cache_ignores_non_numeric_json_stems(tmp_path):
    d = tmp_path / "board"
    d.mkdir()
    for name in ("README.json", "notes.json", "12a.json", "²³.json", "-5.json"):
        (d / name).write_text("{}")
    (d / "200.json").write_text(json.dumps({"shops": [], "buyers": [], "n": 2}))
    (d / "100.json").write_text(json.dumps({"shops": [], "buyers": [], "n": 1}))
    assert [p.name for p in B._cache(tmp_path)] == ["100.json", "200.json"]
    assert B.latest(tmp_path)["n"] == 2
    assert B.nearest(tmp_path, 190)[0] == 200
    st = B.refresh(tmp_path, 10_000, lambda u: (200, b'{"shops": [], "buyers": []}'))[1]
    assert st["last_code"] == 200 and (d / "README.json").exists()
    assert B.term_scale(tmp_path, "x", 10_000) == {}


# ---------------------------------------------------------------- live migration: old shop / buyer / forum lanes
def live_like(env, now):
    """The live loop.json of 2026-11-04 23:26 KST in the old lane format, written through the real path-level write
    path: shop e0001 (category breadth_qty 1 -> 2, long 9 steps, one hour older), buyer e0002 (goods max_pay_frac
    0.85 -> 0.88, switchback) and forum e0003 (forum.rotate 4 -> 5, long 12 steps), both started at 23:26."""
    ctx = env["ctx"]
    L = {}
    loop.migrate(ctx, L)
    loop.check_live(ctx, L, now - 3 * 3600)
    L["pol"] = json.loads(env["pol"].read_bytes())
    t2 = now - 300
    t1 = t2 - 3600
    cat = "categories.가구/침구>침구/홈데코>침구.breadth_qty"
    specs = [
        (1, "shop", "shop", cat, 1, 2, "long", 9, t1),
        (2, "buyer", "buyer", "buyer.defaults.goods.max_pay_frac", 0.85, 0.88, "switchback", 3, t2),
        (3, "forum", "buyer", "forum.rotate", 4, 5, "long", 12, t2),
    ]
    exps = {}
    for i, lane, side, path, cur, val, protocol, steps, t0 in specs:
        assert SP.current(L["pol"], path, SP.entry(ctx.space, path), {}) == cur
        x = {
            "id": f"e{i:04d}",
            "lane": lane,
            "arm": f"{path}|up",
            "mode": "explore",
            "retest": False,
            "protocol": protocol,
            "block_steps": steps,
            "washout_steps": 0,
            "outcome": None,
            "seed": loop.SEED * 1000 + i,
            "side": side,
            "prior": 0.0,
            "edits": [{"path": path, "current": cur, "value": val, "dir": "up"}],
            "supply_up": SP.supply_up(SP.entry(ctx.space, path), cur, val),
            "t_start": t0 - 1,
            "status": "running",
            "blocks": [{"i": 0, "arm": "B", "t0": t0, "t1_nom": t0 + steps * loop.STEP_S}],
            "A_version": L["live_version"],
            "B_version": None,
            "w": {"profit": 0.5, "revenue": 0.3, "stars_rank": 0.2, "surplus": 0.5, "deals": 0.1, "posts": 0.1},
            "stars_base": None,
            "why": "w",
            "a_ref": {"won": 0.0, "metric": 0.0, "hours": 1.0},
        }
        assert loop.put_arm(ctx, L, x, "B", t0)
        exps[lane] = x
    k = loop.step_idx(L.pop("pol"), now)
    L["lanes"] = {
        "shop": {"active": exps["shop"], "next_cycle_step": k},
        "buyer": {"active": exps["buyer"], "next_cycle_step": k + 4},
        "forum": {"active": exps["forum"], "next_cycle_step": k + 4},
    }
    L["n_exp"] = 3
    (env["sdir"] / "loop.json").write_text(json.dumps(L, ensure_ascii=False))
    return json.loads(json.dumps(exps)), k


def test_live_migration_splits_shop_and_ends_forum_as_lane_off(env):
    ctx, now = env["ctx"], NOW0 + 60
    exps, k = live_like(env, now)
    before = json.loads(env["pol"].read_bytes())
    assert (before["forum"]["rotate"], before["buyer"]["defaults"]["goods"]["max_pay_frac"]) == (5, 0.88)
    n_versions = len(ctx.runs.versions())
    env["prop"].replies = [HOLD]
    r = loop.step(now, ctx=ctx)
    assert r["status"] == "ok" and r["lane_off"] == ["forum"], r
    assert r["exp"] == {"stock": "running", "buyer": "running"}
    assert set(r["cycles"]) == {"price"} and r["cycles"]["price"]["proposer"] == "hold"
    L = L_of(env)
    assert set(L["lanes"]) == set(loop.LANES)
    assert L["lanes"]["stock"]["active"] == dict(exps["shop"], lane="stock")  # schedule, blocks, versions as they were
    want_b = dict(exps["buyer"], block_steps=1, reblocked_from=3)  # v1.24: block 0 still open -> the lane's 20 min block
    want_b["blocks"] = [dict(exps["buyer"]["blocks"][0], t1_nom=exps["buyer"]["t_start"] + loop.STEP_S)]
    assert L["lanes"]["buyer"]["active"] == want_b
    assert L["lanes"]["price"]["active"] is None
    assert L["lanes"]["price"]["next_cycle_step"] == k + loop.HOLD_STEPS  # cycled from shop's next_cycle_step
    assert L["lanes"]["stock"]["next_cycle_step"] == k  # inherited from the old shop lane
    # the forum experiment ended through end(): path-level restore of forum.rotate only, one rollback version
    want = json.loads(json.dumps(before))
    want["forum"]["rotate"] = 4
    assert json.loads(env["pol"].read_bytes()) == want
    vs = ctx.runs.versions()
    assert len(vs) == n_versions + 1
    assert ctx.runs.meta(vs[-1])["action"] == "rollback" and "e0003" in ctx.runs.meta(vs[-1])["note"]
    ends = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert [(x["id"], x["lane"], x["verdict"], x["reason"]) for x in ends] == [
        ("e0003", "forum", "stopped", "lane_off")
    ]
    # idempotent: the next tick migrates nothing and writes nothing
    raw = env["pol"].read_bytes()
    r = loop.step(now + 60, ctx=ctx)
    assert "lane_off" not in r and r["exp"] == {"stock": "running", "buyer": "running"}
    assert env["pol"].read_bytes() == raw and len(ctx.runs.versions()) == n_versions + 1
    assert L_of(env)["lanes"]["stock"]["active"] == dict(exps["shop"], lane="stock")
    # e0001 finishes under the long rule in the stock lane
    t1 = exps["shop"]["blocks"][0]["t1_nom"]
    r = loop.step(t1 + 1, ctx=ctx)
    assert r["exp"]["stock"] in ("adopt", "reject")
    end = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"][-1]
    assert (end["id"], end["lane"], end["protocol"], end["stats"]["evidence"]) == ("e0001", "stock", "long", "weak")


# ---------------------------------------------------------------- combined loss cap, independent A/B orders
@pytest.mark.parametrize("restock", [False, True])
def test_shop_side_loss_cap_trips_on_the_combined_whole_shop_shortfall(tmp_path, monkeypatch, restock):
    """Price and stock lanes both in B. Lost sales and a stock-side spend are each below 2 % of the balance, together
    above it: both shop-side lanes measure whole-shop value_h, so both trip on the combined shortfall."""
    e = make_env(tmp_path, own_per_h=12)
    ctx = e["ctx"]
    all_b(monkeypatch)
    e["prop"].replies = [picker({"price": PREF["price"], "stock": PREF["stock"]})]
    loop.step(NOW0, ctx=ctx)
    t = max(x["active"]["t_start"] for x in L_of(e)["lanes"].values() if x["active"])
    assert loop.step(t + 1, ctx=ctx)["exp"] == {"price": "started", "stock": "started"}
    lanes = L_of(e)["lanes"]
    t0 = lanes["price"]["active"]["blocks"][0]["t0"]
    assert lanes["stock"]["active"]["blocks"][0]["t0"] == t0
    now = t0 + loop.STEP_S - 300  # v1.24: still inside the 20 min price block
    cat = ST.catalog(ctx.P)

    def shortfall(key):
        st = ST.update(ST.load_store(e["sdir"] / "store.json"), ctx.P, now, cat)
        stats = ST.market_stats(st, cat, now)
        a = ST.block_metrics(st, t0 - 3600, t0, stats)[key]
        b = ST.block_metrics(st, t0, now, stats)
        return (a - b[key]) * b["hours"]

    lost = shortfall("value_h")
    assert lost > 0
    cap = 1.5 * lost
    with open(ctx.P["kit"], "a", encoding="utf-8") as f:
        f.write(f"10:30:00 [supply] 1 x thing for 1000, balance {round(cap / loop.LOSS_CAP_FRAC)}\n")
    if restock:  # the stock arm's effect: one goods unit bought inside the B block that will not sell
        with open(ctx.P["events"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 10**6, "ts": t0 + 60, "type": "restock", "src": "inventory", "data": {
                "unit": "zz1", "sku": "G1", "category": "가구/침구>가구>침대/매트리스", "kind": "goods",
                "unit_cost": round(lost), "supplied_at": t0 + 60, "expires_at": None}}, ensure_ascii=False) + "\n")
        both = shortfall("value_h")
        assert lost < cap and both - lost < cap < both  # each effect alone below the cap, together above
        assert shortfall("gp_h") < cap  # the gross-profit metric alone would not have seen it
    r = loop.step(now, ctx=ctx)
    if not restock:
        assert r["exp"] == {"price": "running", "stock": "running"}, r
        return
    assert r["exp"] == {"price": "loss_cap", "stock": "loss_cap"}, r
    assert e["pol"].read_bytes() == e["orig"]
    ends = [x for x in rows(e["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert sorted(x["lane"] for x in ends) == ["price", "stock"]
    assert all(x["verdict"] == "reject" and x["reason"].startswith("loss_cap") for x in ends)


def test_the_two_shop_side_lanes_have_independent_ab_orders(env):
    env["prop"].replies = [picker(PREF)]
    r = loop.step(NOW0, ctx=env["ctx"])
    k = loop.step_idx(json.loads(env["orig"]), NOW0)
    assert {ln: c["seed"] for ln, c in r["cycles"].items()} == {
        ln: loop.SEED + k + 1000 * i for i, ln in enumerate(loop.LANES)
    }
    lanes = L_of(env)["lanes"]
    sp, ss = lanes["price"]["active"]["seed"], lanes["stock"]["active"]["seed"]
    seq = {s: [loop.X.block_arm(s, i) for i in range(24)] for s in (sp, ss)}  # 12 pairs
    assert sp != ss and seq[sp] != seq[ss]
    assert all(sorted(q[i : i + 2]) == ["A", "B"] for q in seq.values() for i in range(0, 24, 2))
