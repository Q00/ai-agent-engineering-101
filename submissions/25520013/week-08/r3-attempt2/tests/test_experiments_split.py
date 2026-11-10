"""Split mode (v2.12 arms): a sale-side key is compared concurrently through <root>/policy_arms.json while policy.json
keeps the old value. Arms are checked with attempt 1's real policy.py (r3/policy.py, read only): load_arms,
arm_pick/arm_salt, with_arms and load. Fixed clock values."""

import json
import os

import pytest
from kernel import experiments as ex
from kernel import policy_edit as pe
from tests.test_experiments import (  # noqa: F401  (a1 and root are fixtures)
    BED,
    CATS,
    POLICY,
    SOFA,
    H,
    S,
    _pol,
    _review,
    _rows,
    _sale,
    _set_pol,
    _traj,
    _write_events,
    a1,
    root,
)


def _bytes(root):
    return open(os.path.join(root, "policy.json"), "rb").read()


def _start(root, a1, section=BED, key="price_mult", value=0.8, now=S, **kw):
    item = {
        "section": section,
        "key": key,
        "value": value,
        "reason": "근거",
        "expect": "가설",
        "window_h": 3,
    }
    item.update(kw.pop("item", {}))
    return ex.start(
        root,
        item,
        now,
        _traj(root),
        categories=CATS,
        validate=a1.load,
        split_ok=True,
        **kw,
    )


def _balanced(eid, t0, hours, sku, ga, gb, per_arm=3, tag="u", amount=1_000_000):
    """per_arm sales of each arm every hour from t0 (units chosen by the arm hash), gross ga in arm a and gb in b, a
    little hourly variation so the se is not 0."""
    rows, k = [], 0
    for h in range(hours):
        got, i = {"a": 0, "b": 0}, 0
        while got["a"] < per_arm or got["b"] < per_arm:
            k += 1
            unit = f"{tag}{t0}-{k}"
            arm = "b" if ex.arm_b(eid, unit) else "a"
            if got[arm] >= per_arm:
                continue
            g = (gb if arm == "b" else ga) * (1 + 0.05 * (h % 3 - 1))
            rows += _sale(t0 + h * H + i * 400 + 60, sku, amount + g, amount, unit=unit)
            got[arm] += 1
            i += 1
    return rows


# --- arm hash and the arms file


def test_arm_b_agrees_with_attempt1_arm_pick(a1):
    ids = [f"unit-{i}" for i in range(1000)] + list(range(1000))
    for aid in (1, 7, 123456):
        for share in (0.5, 0.3):
            assert all(
                ex.arm_b(aid, u, share) == a1.arm_pick(aid, a1.arm_salt(u), share)
                for u in ids
            )
    assert 400 < sum(ex.arm_b(1, u) for u in ids) < 1600


def test_split_start_leaves_policy_json_and_writes_valid_arms(root, a1):
    before = _bytes(root)
    r = _start(root, a1)
    assert r == {
        "ok": True,
        "experiment": 1,
        "mode": "split",
        "section": BED,
        "key": "price_mult",
        "old": 0.9,
        "new": 0.8,
        "eval_ts": S + 5 * H,
    }
    assert _bytes(root) == before
    pp = os.path.join(root, "policy.json")
    arms = a1.load_arms(pp, S + 60)
    assert arms == [
        {
            "id": 1,
            "path": ["categories", BED, "price_mult"],
            "value": 0.8,
            "share": 0.5,
            "until": S + 5 * H + 1800,
        }
    ]
    assert a1.load_arms(pp, S + 5 * H + 1801) == []  # inert after until
    pol = a1.load(pp)
    b_unit = next(
        u for u in (f"x{i}" for i in range(100)) if a1.arm_pick(1, a1.arm_salt(u))
    )
    pol2, picked = a1.with_arms(pol, arms, a1.arm_salt(b_unit))
    assert picked == {1: "b"} and a1.resolve(pol2, BED, "goods")["price_mult"] == 0.8
    assert a1.resolve(pol, BED, "goods")["price_mult"] == 0.9
    e = ex.load(root)[0]
    assert (e["mode"], e["old"], e["new"], e["arm"]["until"]) == (
        "split",
        0.9,
        0.8,
        S + 5 * H + 1800,
    )
    assert "raw" not in e
    row = [x for x in _rows(root, "policy_edit") if x.get("by") == "split_start"]
    assert len(row) == 1 and row[0]["experiment"] == 1 and not row[0].get("backup")
    assert not os.path.exists(os.path.join(root, pe.BAK_DIR))
    # the split start started the key's cooldown
    chk = pe.check(
        root,
        {"section": BED, "key": "price_mult", "value": 0.85, "reason": "r"},
        _traj(root),
        S + 60,
        CATS,
        a1.load,
    )
    assert chk["ok"] is False and "min ago" in chk["error"]


def test_split_start_per_grade_value_is_the_whole_dict(root, a1):
    r = _start(root, a1, "defaults.goods", "fresh_discount.C", 0.25)
    assert r["ok"] and r["mode"] == "split"
    arms = a1.load_arms(os.path.join(root, "policy.json"), S)
    assert arms[0]["path"] == ["defaults", "goods", "fresh_discount"]
    assert arms[0]["value"] == {"B": 0.08, "C": 0.25}
    r = _start(root, a1, "shop", "grade_floor_list.A", 0.5)
    assert (
        r is not None and r["ok"] is False
    )  # shop:all overlaps the running defaults.goods experiment


def test_check_runs_every_edit_check_without_writing(root, a1):
    before = _bytes(root)
    traj = _traj(root)
    ok = pe.check(
        root,
        {"section": BED, "key": "price_mult", "value": 0.8, "reason": "r"},
        traj,
        S,
        CATS,
        a1.load,
    )
    assert ok == {
        "ok": True,
        "section": BED,
        "key": "price_mult",
        "old": 0.9,
        "new": 0.8,
    }
    bad = [
        {"section": BED, "key": "price_mult", "value": 0.6, "reason": "r"},  # step
        {"section": BED, "key": "price_mult", "value": 1.3, "reason": "r"},  # range
        {
            "section": "식품>과자",
            "key": "warranty_hours.A",
            "value": 3,
            "reason": "r",
        },  # goods only
        {"section": BED, "key": "price_mult", "value": 0.8},  # no reason
    ]
    for it in bad:
        assert pe.check(root, it, traj, S, CATS, a1.load)["ok"] is False, it
    seen = []

    def refuse(
        p,
    ):  # attempt 1 validate runs on a changed temp copy, never on policy.json
        seen.append((p, json.load(open(p))["categories"][BED]["price_mult"]))
        raise ValueError("refused")

    res = pe.check(
        root,
        {"section": BED, "key": "price_mult", "value": 0.8, "reason": "r"},
        traj,
        S,
        CATS,
        refuse,
    )
    assert "check failed" in res["error"] and seen[0][1] == 0.8
    assert seen[0][0] != os.path.join(root, "policy.json") and not os.path.exists(
        seen[0][0]
    )
    first = pe.check(
        root,
        {"section": "shop", "key": "grade_floor_list.A", "value": 0.5, "reason": "r"},
        traj,
        S,
        CATS,
        a1.load,
    )
    assert first["ok"] and first["old"] is None and "note" in first
    assert _bytes(root) == before and traj.since(0) == []
    assert not [f for f in os.listdir(root) if f.startswith(".policy.tmp-")]


def test_splittable_keys():
    for k in (
        "price_mult",
        "min_margin",
        "concession",
        "max_rounds",
        "warranty_uplift",
        "fresh_discount.B",
        "warranty_hours.A",
        "grade_floor_list.C",
    ):
        assert ex.splittable(k), k
    for k in (
        "stock_target",
        "qty_per_buy",
        "c_first_leaves.침대/매트리스",
        "max_pay_frac",
        "open_frac",
        "accept_c.max_pay_frac",
        "fresh_discount",
        "fresh_discount.Z",
        "nope",
        3,
    ):
        assert not ex.splittable(k), k


def test_auto_mode_picks_split_only_when_allowed(root, a1):
    r = ex.start(
        root,
        {"section": BED, "key": "price_mult", "value": 0.8, "reason": "r"},
        S,
        _traj(root),
        categories=CATS,
        validate=a1.load,
    )
    assert r["mode"] == "sequential"  # split_ok False
    r = _start(root, a1, SOFA, "stock_target", 10)
    assert r["ok"] and r["mode"] == "sequential"  # not splittable
    r = _start(root, a1, "buyer.defaults.food", "max_pay_frac", 0.9)
    assert r["ok"] and r["mode"] == "sequential"
    r = _start(root, a1, "buyer.defaults.goods", "max_pay_frac", 0.9, mode="split")
    assert r["ok"] is False  # forced split of a buyer key is refused


def test_ninth_arm_is_refused(root, a1):
    other = [
        {
            "id": 100 + i,
            "path": ["shop", "x"],
            "value": 1,
            "share": 0.5,
            "until": S + 9 * H,
        }
        for i in range(8)
    ]
    with open(ex.arms_path(root), "w") as f:
        json.dump({"arms": other}, f)
    before = _bytes(root)
    r = _start(root, a1)
    assert r["ok"] is False and "8개까지" in r["error"]
    assert (
        ex.load_arms(root) == other and _bytes(root) == before and ex.load(root) == []
    )


def test_split_and_sequential_block_each_other(root, a1):
    assert _start(root, a1)["ok"]
    assert _start(root, a1, mode="sequential", item={"value": 0.85})["ok"] is False
    assert _start(root, a1, "defaults.goods", "price_mult", 0.85)["ok"] is False
    assert _start(root, a1, SOFA, "price_mult", 0.85)["ok"] is True  # another leaf
    assert (
        _start(root, a1, "shop", "grade_floor_list.C", 0.6, mode="sequential")["ok"]
        is False
    )
    assert len(ex.load_arms(root)) == 2


def test_sequential_blocks_split(root, a1):
    assert _start(root, a1, "shop", "grade_floor_list.C", 0.6, mode="sequential")["ok"]
    r = _start(root, a1)
    assert r["ok"] is False and "#1" in r["error"]
    assert not os.path.exists(ex.arms_path(root))


# --- evaluation and ending


@pytest.mark.parametrize(
    "section,key,value,check",
    [
        (
            BED,
            "price_mult",
            0.8,
            lambda p: p["categories"][BED] == {"stock_target": 36, "price_mult": 0.8},
        ),
        (
            BED,
            "fresh_discount.B",
            0.1,  # inherited from defaults.goods: the whole dict lands in the category
            lambda p: p["categories"][BED]["fresh_discount"] == {"B": 0.1, "C": 0.3},
        ),
        (
            "shop",
            "grade_floor_list.A",
            0.5,  # a grade that did not exist
            lambda p: p["shop"]["grade_floor_list"] == {"C": 0.55, "A": 0.5},
        ),
    ],
)
def test_better_b_is_adopted(root, a1, section, key, value, check):
    r = _start(root, a1, section, key, value)
    assert r["ok"] and r["mode"] == "split", r
    _write_events(root, _balanced(1, S, 3, "BED1", 100_000, 200_000))
    out = ex.tick(root, r["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["adopted"], ex.load(root)
    e = ex.load(root)[0]
    v = e["verdict"]
    assert (v["decision"], v["reason"], v["final"], v["n_a"], v["n_b"]) == (
        "better",
        "clear",
        "decision",
        9,
        9,
    )
    assert v["b"] > v["a"] and v["effect"] > 2 * v["se"]
    pol = _pol(root)
    assert check(pol) and pe.effective(pol, section, key) == value
    a1.load(os.path.join(root, "policy.json"))
    assert ex.load_arms(root) == []
    k = [x for x in _rows(root, "policy_edit") if x.get("by") == "kernel"]
    assert (
        len(k) == 1
        and "#1" in k[0]["reason"]
        and k[0]["backup"].startswith("a2/policy_bak/")
    )


def test_worse_b_is_dropped_and_policy_untouched(root, a1):
    before = _bytes(root)
    r = _start(root, a1)
    _write_events(root, _balanced(1, S, 3, "BED1", 200_000, 100_000))
    out = ex.tick(root, r["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["dropped"]
    assert ex.load(root)[0]["verdict"]["decision"] == "worse"
    assert _bytes(root) == before and ex.load_arms(root) == []
    assert not [x for x in _rows(root, "policy_edit") if x.get("by") == "kernel"]


def test_value_changed_meanwhile_voids(root, a1):
    r = _start(root, a1)
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.7))
    _write_events(root, _balanced(1, S, 3, "BED1", 100_000, 200_000))
    out = ex.tick(root, r["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["voided"] and ex.load_arms(root) == []
    assert _pol(root)["categories"][BED]["price_mult"] == 0.7


def test_late_tick_gives_the_on_time_verdict(root, a1):
    r = _start(root, a1)
    rows = _balanced(1, S, 3, "BED1", 100_000, 120_000)
    _write_events(root, rows)
    e = ex.load(root)[0]
    on_time = ex.evaluate_split(root, e, r["eval_ts"] + 1)
    late = []
    for i in range(
        20
    ):  # sales after the window, reviews of window sales landing after eval_ts
        s = _sale(
            S + 3 * H + 600 + i * 60, "BED1", 9_000_000, 1_000_000, unit=f"late{i}"
        )
        late += s
    for x in rows[::2][:12]:
        if x["type"] == "settle":
            late += _review(r["eval_ts"] + 3 * H, [x], 1)
    _write_events(root, late)
    assert ex.evaluate_split(root, e, r["eval_ts"] + 30 * H) == on_time
    ex.tick(root, r["eval_ts"] + 30 * H, _traj(root), CATS, validate=a1.load)
    v = ex.load(root)[0]["verdict"]
    assert {k: v[k] for k in on_time} == on_time


def test_unit_missing_is_counted_and_thin_above_20_percent(root, a1):
    r = _start(root, a1)
    rows = _balanced(1, S, 3, "BED1", 100_000, 200_000)
    for i in range(6):  # 6 of 24 (25 %) without a unit id
        s = _sale(S + 1800 + i * 60, "BED1", 1_100_000, 1_000_000)
        s[0]["data"].pop("unit")
        s[0]["data"]["unit_cost"] = 1_000_000
        rows += s[:1]
    _write_events(root, rows)
    v = ex.evaluate_split(root, ex.load(root)[0], r["eval_ts"] + 1)
    assert (v["decision"], v["reason"], v["no_unit"], v["n_a"] + v["n_b"]) == (
        "thin",
        "no_unit",
        6,
        18,
    )


def test_unclear_extends_with_the_arm_then_status_quo_wins(root, a1):
    r = _start(root, a1)
    _write_events(root, _balanced(1, S, 6, "BED1", 100_000, 100_000))
    ex.tick(root, r["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    e = ex.load(root)[0]
    assert (e["status"], e["extended"], e["window_h"], e["eval_ts"]) == (
        "running",
        True,
        6,
        S + 8 * H,
    )
    assert e["first_verdict"]["decision"] == "unclear"
    assert ex.load_arms(root)[0]["until"] == S + 8 * H + 1800 == e["arm"]["until"]
    ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    e = ex.load(root)[0]
    assert e["status"] == "dropped" and e["verdict"]["final"] == "point_estimate"
    assert e["verdict"]["adopt"] is False and ex.load_arms(root) == []
    assert "점추정" in "\n".join(ex.lines(root, e["ended_ts"]))


def test_weak_final_adopts_b_above_one_se(root, a1):
    r = _start(root, a1, item={"window_h": 8})
    rows = []
    for h, gb in enumerate(
        [100_000, 150_000, 80_000, 160_000, 90_000, 170_000, 100_000, 120_000]
    ):
        rows += _balanced(1, S + h * H, 1, "BED1", 100_000, gb, tag=f"h{h}-")
    _write_events(root, rows)
    ex.tick(root, r["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    e = ex.load(root)[0]
    v = e["verdict"]
    assert not e["extended"]  # window 8 already: decided at once
    assert v["se"] < v["effect"] < 2 * v["se"] and v["final"] == "point_estimate"
    assert (
        e["status"] == "adopted" and _pol(root)["categories"][BED]["price_mult"] == 0.8
    )


def test_expired_arm_is_decided_at_once(root, a1):
    r = _start(root, a1)
    _write_events(root, _balanced(1, S, 3, "BED1", 100_000, 100_000))
    ex.tick(
        root, r["eval_ts"] + 3600, _traj(root), CATS, validate=a1.load
    )  # daemon down past until
    e = ex.load(root)[0]
    assert e["status"] == "dropped" and not e["extended"]


def test_adopt_failure_retries_then_voids(root, a1):
    r = _start(root, a1)
    _write_events(root, _balanced(1, S, 3, "BED1", 100_000, 200_000))

    def bad(p):
        raise ValueError("disk full")

    t = r["eval_ts"] + 1
    for i in range(5):
        assert ex.tick(root, t + i * 601, _traj(root), CATS, validate=bad) == []
    e = ex.load(root)[0]
    assert (
        e["status"] == "running" and e["fails"] == 5 and "disk full" in e["last_error"]
    )
    assert (
        ex.load_arms(root) == []
    )  # the arm goes before the write, so a retry sees the old value
    out = ex.tick(root, t + 5 * 601, _traj(root), CATS, validate=bad)
    e = ex.load(root)[0]
    assert [x["status"] for x in out] == ["voided"] and e["verdict"][
        "reason"
    ] == "eval_error"
    assert _pol(root)["categories"][BED].get("price_mult") is None


def test_adopt_retry_succeeds(root, a1):
    r = _start(root, a1)
    _write_events(root, _balanced(1, S, 3, "BED1", 100_000, 200_000))

    def bad(p):
        raise ValueError("disk full")

    t = r["eval_ts"] + 1
    assert ex.tick(root, t, _traj(root), CATS, validate=bad) == []
    out = ex.tick(root, t + 601, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["adopted"]
    assert _pol(root)["categories"][BED]["price_mult"] == 0.8


def test_split_register_failure_leaves_nothing(root, a1, monkeypatch):
    def boom(*a, **k):
        raise OSError("ledger disk full")

    before = _bytes(root)
    monkeypatch.setattr(ex, "register", boom)
    r = _start(root, a1)
    assert r["ok"] is False and "ledger disk full" in r["error"]
    assert _bytes(root) == before and ex.load_arms(root) == [] and ex.load(root) == []


def test_old_format_entry_still_evaluates(root, a1):
    """An entry as the 11-10 register wrote it: no mode, raw, before_from or fails."""
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    _write_events(
        root,
        _balanced(99, S - 3 * H, 3, "BED1", 300_000, 300_000)
        + _balanced(99, S, 3, "BED1", 100_000, 100_000, tag="v"),
    )
    old = {
        "id": 1,
        "section": BED,
        "key": "price_mult",
        "old": 0.9,
        "new": 0.8,
        "started_ts": S,
        "window_h": 3,
        "maturity_h": 2,
        "scope": "침대/매트리스",
        "side": "shop",
        "metric": "net_profit_per_h",
        "rev_weight": 0.0,
        "expect": "",
        "status": "running",
        "extended": False,
        "eval_ts": S + 5 * H,
    }
    ex.save(root, [old])
    out = ex.tick(root, S + 5 * H + 1, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["reverted"]
    assert _pol(root)["categories"][BED]["price_mult"] == 0.9


# --- briefing


def test_lines_both_modes_fit(root):
    long_leaf = "가구/침구>가구>아주아주아주긴잎이름입니다정말로"
    running = [  # (mode, section, key): 8 running, the briefing shows 6
        ("sequential", BED, "price_mult"),
        ("split", SOFA, "fresh_discount.B"),
        ("split", "defaults.food", "warranty_uplift"),
        ("sequential", "buyer.defaults.food", "max_pay_frac"),
        ("split", long_leaf, "grade_floor_list.C"),
        ("sequential", "buyer.defaults.goods", "accept_c.max_pay_frac"),
        ("split", "가구/침구>가구>옷장", "price_mult"),
        ("split", "가구/침구>가구>책상", "price_mult"),
    ]

    def e(i, mode, sec, key, status="running", **kw):
        side, scope = ex.scope_of(sec)
        x = {"id": i, "mode": mode, "section": sec, "key": key, "old": 0.123456789, "new": 0.1, "started_ts": S,
             "window_h": 3, "maturity_h": 2, "scope": scope, "side": side, "metric": "net_profit_per_h",
             "expect": "가설 " * 60, "status": status, "extended": i == 3, "eval_ts": S + 5 * H}
        if i == 2:
            x["last_error"] = "RuntimeError: revert failed: check failed: " + "x" * 200
        x.update(kw)
        return x

    split_v = {"decision": "better", "reason": "clear", "final": "decision", "a": 800_000.0, "b": 920_000.0,
               "effect": 120_000.0, "se": 50_000.0, "n_a": 31, "n_b": 28}
    weak_v = dict(split_v, decision="unclear", reason="noise", final="point_estimate", effect=-30_000.0)
    seq_v = {"decision": "worse", "reason": "clear", "final": "decision", "before_adj": 900_000.0,
             "after": 300_000.0, "effect": -600_000.0, "se": 27_386.0, "n_before": 9, "n_after": 9,
             "rev_weight": 0.1, "net_before": 900_000.0, "net_after": 250_000.0}
    rows = [e(i, *r) for i, r in enumerate(running, 1)]
    rows += [
        e(10, "split", BED, "price_mult", "adopted", verdict=split_v, ended_ts=S + 1),  # 6th newest: not shown
        e(11, "split", SOFA, "fresh_discount.B", "dropped", verdict=weak_v, ended_ts=S + 2),
        e(12, "sequential", BED, "price_mult", "reverted", verdict=seq_v, ended_ts=S + 3),
        e(13, "sequential", BED, "min_margin", "kept", ended_ts=S + 4,
          verdict=dict(seq_v, decision="unclear", reason="noise", final="point_estimate", effect=5_000.0)),
        e(14, "split", BED, "concession", "voided", verdict={"decision": "voided", "reason": "eval_error"},
          ended_ts=S + 5),
        e(15, "sequential", "buyer.defaults.food", "open_frac", "voided", ended_ts=S + 6,
          verdict={"decision": "voided", "reason": "value_changed"}),
    ]
    ex.save(root, rows)
    out = ex.lines(root, S + 7)
    text = "\n".join(out)
    print(text)
    assert len(text) <= 1400
    assert out[0] == "[실험] 진행 8건, 최근 판정 5건 중 유지 1, 되돌림 1, 채택 0, 버림 1"
    assert sum(1 for x in out if x.startswith("  진행 #")) == 6 and "  … 진행 2건 더" in out
    assert sum(1 for x in out if x.startswith("  #")) == 5
    assert any(x.startswith("  진행 #1 침대/매트리스 price_mult 0.1235→0.1, 판정 ") for x in out)
    assert any(x.startswith("  진행 #2 동시 비교 소파/의자/스툴 fresh_discount.B A 0.1235 / B 0.1, 판정 ")
               and ", 오류 RuntimeError" in x for x in out)
    assert any(x.startswith("  진행 #3 동시 비교 food warranty_uplift A 0.1235 / B 0.1, 판정 ")
               and "(연장)" in x for x in out)
    assert any(x.startswith("  진행 #5 동시 비교 아주긴잎이름입니다정말로 grade_floor_list.C ") for x in out)
    assert any(x.startswith("  #11 fresh_discount.B 0.1235→0.1 버림: 효과 -30,000/h (A 0.80M/h, B 0.92M/h, se ")
               and x.endswith("점추정(약한 판정)") for x in out)
    assert any(x.startswith("  #12 price_mult 0.1235→0.1 되돌림: 효과 -0.60M/h (전 보정 0.90M/h")
               and "순이익 0.90M/h→0.25M/h" in x and "잡음 넘음" in x for x in out)
    assert any(x.startswith("  #13 min_margin") and "잡음 넘음" not in x and "점추정" in x for x in out)
    assert any(x.startswith("  #14 concession 0.1235→0.1 무효: 평가 실패") for x in out)
    assert any(x.startswith("  #15 open_frac 0.1235→0.1 무효: 값이 따로 바뀜") for x in out)


def test_split_ended_line_and_summary(root):
    v = {
        "decision": "better",
        "reason": "clear",
        "final": "decision",
        "a": 800_000.0,
        "b": 920_000.0,
        "effect": 120_000.0,
        "se": 50_000.0,
        "n_a": 31,
        "n_b": 28,
    }
    base = {
        "section": BED,
        "key": "price_mult",
        "old": 0.9,
        "new": 0.85,
        "side": "shop",
        "scope": "침대/매트리스",
    }
    ex.save(
        root,
        [
            dict(base, id=7, mode="split", status="adopted", verdict=v, ended_ts=S),
            dict(base, id=8, mode="split", status="dropped", verdict=v, ended_ts=S),
            dict(base, id=9, status="kept", verdict={}, ended_ts=S),
            dict(base, id=10, status="reverted", verdict={}, ended_ts=S),
            dict(base, id=11, status="voided", verdict={}, ended_ts=S),
        ],
    )
    out = ex.lines(root, S)
    assert (
        "  #7 price_mult 0.9→0.85 채택: 효과 +0.12M/h (A 0.80M/h, B 0.92M/h, se 50,000, 판매 A 31 / B 28), 잡음 넘음"
        in out
    )
    s = ex.summary(root, S)
    assert s["counts"] == {
        "running": 0,
        "kept": 1,
        "reverted": 1,
        "voided": 1,
        "adopted": 1,
        "dropped": 1,
    }
    assert s["hit_rate"] == 0.5
