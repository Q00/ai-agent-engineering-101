"""v2.12: concurrent comparison arms. policy_arms.json next to the live policy.json gives arms; a sale whose salt
(the sold unit id, which the market settle carries) hashes below an arm's share gets the arm's value at its path for
the whole negotiation (arm "b"), the rest keep the policy as it is (arm "a"). No file, an empty list or only expired
arms -> the policy object itself, so decisions are byte-for-byte unchanged."""

import asyncio
import copy
import json
import types

import pytest

from tests import test_v120_buyer_log as TB

R3 = TB.R3
import policy as P  # noqa: E402  (TB put R3 and R3/hooks on sys.path)

BASE = P.load(R3 / "policy.json")
NOW = 1_800_000_000.0
SOFA = "가구/침구>가구>소파/의자/스툴"
BED = "가구/침구>가구>침대/매트리스"


def _arm(
    i=12,
    path=("categories", "a/b/goods", "price_mult"),
    value=0.6,
    share=0.5,
    until=NOW + 3600,
):
    return {"id": i, "path": list(path), "value": value, "share": share, "until": until}


def _write(d, arms):
    (d / "policy.json").write_text("{}", encoding="utf-8")
    p = d / P.ARMS_FILE
    p.write_text(arms if isinstance(arms, str) else json.dumps(arms), encoding="utf-8")
    return d / "policy.json"


# --- load_arms


def test_load_arms_missing_and_corrupt(tmp_path):
    assert P.load_arms(tmp_path / "policy.json", NOW) == []
    for raw in ("{not json", "[]", '{"arms": 3}', '{"arms": {"id": 1}}', "", "null"):
        assert P.load_arms(_write(tmp_path, raw), NOW) == [], raw


def test_load_arms_skips_bad_arms(tmp_path):
    good = _arm(1)
    bad = [
        _arm(2, share=0),
        _arm(3, share=1),
        _arm(4, share=1.5),
        _arm(5, share=True),
        _arm(6, share="0.5"),
        _arm(7, path=("buyer", "defaults", "goods", "max_pay_frac")),
        _arm(8, path=("bounds", "price_mult")),
        _arm(9, path=("shop",)),
        _arm(10, path=("shop", 3)),
        dict(_arm(11), until="soon"),
        "arm",
        {k: v for k, v in _arm(13).items() if k != "value"},
        dict(_arm(14), id=True),
        dict(_arm(15), id="15"),
        _arm(1, value=0.7),  # repeated id: the first one wins
    ]
    for i in range(0, len(bad), 7):  # at most 8 arms per file
        got = P.load_arms(_write(tmp_path, {"arms": [good] + bad[i : i + 7]}), NOW)
        assert [a["id"] for a in got] == [1] and got[0]["value"] == 0.6, bad[i : i + 7]


def test_load_arms_more_than_eight_is_ignored_and_expiry(tmp_path):
    assert (
        len(P.load_arms(_write(tmp_path, {"arms": [_arm(i) for i in range(8)]}), NOW))
        == 8
    )
    assert (
        P.load_arms(_write(tmp_path, {"arms": [_arm(i) for i in range(9)]}), NOW) == []
    )
    pp = _write(
        tmp_path,
        {"arms": [_arm(1, until=NOW - 1), _arm(2, until=NOW + 10), _arm(3, until=NOW)]},
    )
    assert [a["id"] for a in P.load_arms(pp, NOW)] == [2]
    assert P.load_arms(pp, NOW + 10) == []


def test_load_arms_rereads_a_changed_file(tmp_path):
    pp = _write(tmp_path, {"arms": [_arm(1)]})
    assert [a["id"] for a in P.load_arms(pp, NOW)] == [1]
    _write(tmp_path, {"arms": [_arm(22), _arm(23)]})
    assert [a["id"] for a in P.load_arms(pp, NOW)] == [22, 23]
    (tmp_path / P.ARMS_FILE).unlink()
    assert P.load_arms(pp, NOW) == []


# --- arm_pick / arm_salt


def test_arm_pick_share_and_determinism():
    salts = [P.arm_salt(f"u-{i:08x}") for i in range(10_000)]
    frac = sum(P.arm_pick(12, s, 0.5) for s in salts) / len(salts)
    assert abs(frac - 0.5) <= 0.02, frac
    frac = sum(P.arm_pick(12, s, 0.2) for s in salts) / len(salts)
    assert abs(frac - 0.2) <= 0.02, frac
    assert [P.arm_pick(12, s, 0.5) for s in salts[:200]] == [
        P.arm_pick(12, s, 0.5) for s in salts[:200]
    ]
    assert [P.arm_pick(12, s) for s in salts[:200]] != [
        P.arm_pick(13, s) for s in salts[:200]
    ]  # arms independent


def test_arm_salt_is_the_unit_id():
    assert P.arm_salt("u-0a1b") == "u-0a1b" == P.arm_salt("u-0a1b")
    assert P.arm_salt(17) == "17"


# --- with_arms


def _salt(arm_id, b, share=0.5):
    """A salt that picks arm b (b=True) or arm a for arm_id."""
    return next(
        s
        for s in (P.arm_salt(f"x{i}") for i in range(1000))
        if P.arm_pick(arm_id, s, share) == b
    )


def test_with_arms_none_is_the_same_object():
    assert P.with_arms(BASE, [], "s") == (BASE, {})
    a = _arm(12)
    pol, picked = P.with_arms(BASE, [a], _salt(12, False))
    assert pol is BASE and picked == {12: "a"}


def test_with_arms_copy_on_write_and_new_category():
    pol0 = copy.deepcopy(BASE)
    snap = copy.deepcopy(pol0)
    a = _arm(12, path=("categories", "소파/의자/스툴", "price_mult"), value=0.85)
    pol, picked = P.with_arms(pol0, [a], _salt(12, True))
    assert picked == {12: "b"} and pol is not pol0
    assert pol0 == snap  # original never mutated
    assert pol["categories"]["소파/의자/스툴"] == {"price_mult": 0.85}
    assert (
        pol["categories"][BED] is pol0["categories"][BED]
    )  # untouched subtrees shared
    for k in ("defaults", "shop", "buyer", "bounds", "volume"):
        assert pol[k] is pol0[k], k
    assert pol["_hash"] == pol0["_hash"]
    assert P.resolve(pol, SOFA, "goods")["price_mult"] == 0.85
    assert P.resolve(pol, BED, "goods") == P.resolve(pol0, BED, "goods")
    assert P.resolve(pol, "x>y>z", "goods") == P.resolve(pol0, "x>y>z", "goods")


def test_with_arms_nested_dict_value():
    a = _arm(
        5, path=("defaults", "goods", "fresh_discount"), value={"B": 0.1, "C": 0.4}
    )
    b = _arm(6, path=("shop", "grade_floor_list"), value={"C": 0.5})
    salt = next(
        s
        for s in (P.arm_salt(f"x{i}") for i in range(5000))
        if P.arm_pick(5, s, 0.5) and P.arm_pick(6, s, 0.5)
    )
    pol, picked = P.with_arms(BASE, [a, b], salt)
    assert picked == {5: "b", 6: "b"}
    assert pol["defaults"]["goods"]["fresh_discount"] == {"B": 0.1, "C": 0.4}
    assert pol["defaults"]["food"] is BASE["defaults"]["food"]
    assert (
        pol["shop"]["grade_floor_list"] == {"C": 0.5}
        and "grade_floor_list" not in BASE["shop"]
    )
    assert (
        pol["defaults"]["goods"]["price_mult"]
        == BASE["defaults"]["goods"]["price_mult"]
    )
    pol["defaults"]["goods"]["fresh_discount"]["B"] = (
        0.9  # the arm's value is copied, not shared
    )
    assert a["value"]["B"] == 0.1


def test_with_arms_validate_failure_falls_back():
    for bad in (
        _arm(12, value=3.0),  # price_mult outside bounds [0.5, 1.5]
        _arm(12, path=("shop", "grade_floor_list"), value={"C": 0.1}),
        _arm(12, path=("defaults", "goods", "price_mult", "x"), value=1.0),
    ):  # through a number
        ok = _arm(13, path=("categories", "goods", "min_margin"), value=0.1)
        salt = next(
            s
            for s in (P.arm_salt(f"x{i}") for i in range(5000))
            if P.arm_pick(12, s, 0.5) and not P.arm_pick(13, s, 0.5)
        )
        pol, picked = P.with_arms(BASE, [bad, ok], salt)
        assert pol is BASE and picked == {12: "invalid", 13: "a"}, bad


# --- seller


@pytest.fixture
def seller(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl"))
    m = TB.load_seller(R3 / "hooks" / "seller.py", "seller_v2120_under_test")
    trace, clock = [], TB.Clock()
    TB.patch(m, trace, clock)
    s = m.Seller(pol=copy.deepcopy(BASE))
    s.arms_policy_path = tmp_path / "policy.json"
    return types.SimpleNamespace(m=m, s=s, trace=trace, clock=clock, dir=tmp_path)


class _Q:
    async def enqueue_event(self, e):
        pass


def _ctx(env, tid, text):
    msg = types.SimpleNamespace(text=text, tid=tid, cid="c-" + tid, metadata={})
    cur = types.SimpleNamespace(id=tid, context_id="c-" + tid) if tid in env.s.open else None
    return types.SimpleNamespace(current_task=cur, message=msg, context_id="c-" + tid, call_context=None)


def _send(env, tid, text):
    env.clock.t += 1
    asyncio.run(env.s._execute(_ctx(env, tid, text), _Q()))
    return env.trace[-1]


def _cancel(env, tid):
    ctx = _ctx(env, tid, "")
    ctx.current_task = types.SimpleNamespace(id=tid, context_id="c-" + tid)
    asyncio.run(env.s.cancel(ctx, _Q()))


# TB.INV: G00002 = units u1, u5, u9, all grade B (no star_price_cap) and cost 1250; the pick takes u1, then u5.
SPLIT = next(i for i in range(1, 1000) if P.arm_pick(i, P.arm_salt("u1")) != P.arm_pick(i, P.arm_salt("u5")))


def test_seller_two_units_of_one_sku_split_and_only_b_changes_the_ask(seller):
    _write(seller.dir, {"arms": [_arm(SPLIT, value=0.6, until=seller.clock.t + 86400)]})
    for tid in ("t1", "t2"):
        _send(seller, tid, "I want G00002")
    d1, d2 = seller.s.open["t1"], seller.s.open["t2"]
    assert (d1["unit"]["unit"], d2["unit"]["unit"]) == ("u1", "u5")
    arm = {d["unit"]["unit"]: d["arms"][SPLIT] for d in (d1, d2)}
    assert sorted(arm.values()) == ["a", "b"]
    da, db = (d1, d2) if arm["u1"] == "a" else (d2, d1)
    assert da["pol"] is seller.s.pol and db["pol"] is not seller.s.pol
    assert da["ask"] == P.ask_price(da["unit"], seller.s.pol) == P.ask_price(db["unit"], seller.s.pol)
    assert db["ask"] == P.ask_price(db["unit"], db["pol"]) < da["ask"]
    opens = [json.loads(x[2]) for x in seller.trace if x[:2] == ("metric", "open")]
    assert [(o["unit"], o.get("arms")) for o in opens] == [(u, {str(SPLIT): arm[u]}) for u in ("u1", "u5")]


def test_seller_same_unit_same_arm_in_a_later_task(seller):
    _write(seller.dir, {"arms": [_arm(SPLIT, value=0.6, until=seller.clock.t + 86400)]})
    _send(seller, "t1", "I want G00002")
    first = seller.s.open["t1"]
    _cancel(seller, "t1")                                    # the buyer walks; u1 is offered again
    seller.clock.t += 7200
    _send(seller, "t2", "I want G00002")
    again = seller.s.open["t2"]
    assert again["unit"]["unit"] == first["unit"]["unit"] == "u1"
    assert again["arms"] == first["arms"] and again["ask"] == first["ask"]


def test_seller_same_task_keeps_its_arm_every_round(seller):
    _write(seller.dir, {"arms": [_arm(SPLIT, value=0.6, share=0.99, until=seller.clock.t + 86400)]})
    tid = "t1"
    _send(seller, tid, "I want G00002")
    deal = seller.s.open[tid]
    pol, arms = deal["pol"], deal["arms"]
    assert arms == {SPLIT: "b"} if P.arm_pick(SPLIT, P.arm_salt(deal["unit"]["unit"]), 0.99) else arms == {SPLIT: "a"}
    (seller.dir / P.ARMS_FILE).unlink()                      # arm withdrawn mid-negotiation
    seller.clock.t += 3600
    for offer in (500, 600, 700):
        before = dict(deal)
        _send(seller, tid, str(offer))
        if tid not in seller.s.open:
            break
        assert deal["pol"] is pol and deal["arms"] == arms
        mk = P.market_floor(deal["unit"], pol, deal.get("stats"), before["ask"])
        move, price = P.respond(deal["unit"], pol, before["ask"], offer, deal["rounds"], deal["inv"],
                                relax=deal["relax"], prev_offer=(before.get("offers") or [None])[-1],
                                history=list(before.get("offers") or []), market=mk)
        assert deal["ask"] == price, (offer, move)


def _old_path(m):
    m.Seller.deal_policy = lambda self, unit, now: (self.pol, {})   # the pre-v2.12 path: the live policy itself


@pytest.mark.parametrize("seed", [3, 17])
@pytest.mark.parametrize("case", ["no_file", "empty", "expired", "error"])
def test_decisions_unchanged_without_live_arms(tmp_path, monkeypatch, seed, case):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "b.jsonl"))
    monkeypatch.setattr(P, "POLICY_FILE", tmp_path / "policy.json")   # Seller(pol=...) looks for arms beside it
    if case == "empty":
        _write(tmp_path, {"arms": []})
    elif case == "expired":
        _write(tmp_path, {"arms": [_arm(12, value=0.6, share=0.99, until=1_000_000_000)]})
    elif case == "error":
        _write(tmp_path, {"arms": [_arm(12, value=0.6, share=0.99, until=NOW * 2)]})
        monkeypatch.setattr(P, "with_arms", lambda *a: 1 / 0)
    pol = P.load(R3 / "policy.json")
    new = TB.run(TB.load_seller(R3 / "hooks" / "seller.py", f"seller_v2120_{case}_{seed}"), seed, pol)
    ref_m = TB.load_seller(R3 / "hooks" / "seller.py", f"seller_v2120_ref_{case}_{seed}")
    _old_path(ref_m)
    ref = TB.run(ref_m, seed, pol)
    assert new == ref and any(x[0] == "metric" and x[1] == "open" for x in new)


def test_decisions_change_with_a_live_arm(tmp_path, monkeypatch):
    """The comparison above can see a difference: a live share-0.99 arm changes the opening asks."""
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "b.jsonl"))
    monkeypatch.setattr(P, "POLICY_FILE", tmp_path / "policy.json")
    _write(tmp_path, {"arms": [_arm(12, value=0.6, share=0.99, until=NOW * 2)]})
    pol = P.load(R3 / "policy.json")
    new = TB.run(TB.load_seller(R3 / "hooks" / "seller.py", "seller_v2120_live"), 3, pol)
    ref_m = TB.load_seller(R3 / "hooks" / "seller.py", "seller_v2120_live_ref")
    _old_path(ref_m)
    assert new != TB.run(ref_m, 3, pol)
