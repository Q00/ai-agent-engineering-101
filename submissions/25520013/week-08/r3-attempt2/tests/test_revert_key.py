"""Key-level revert and the own-key voiding rule (verifier probes p3/p8, 11-12): a revert puts back only the
experiment's own key inside a shared base (per-grade dict, accept_c block, c_first_leaves list), a write voids only
the experiments whose own value it writes, a scope-only touch is named on the verdict, and a split's arm goes before
policy.json changes. Fixed clock values, attempt 1's real policy.load as validate. Since 11-12 a verdict only
measures; the put-back runs from the tick helper below through the same key-level restore."""

import copy
import json
import os

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
    _traj,
    a1,
    root,
)

WORSE = {"decision": "worse", "reason": "clear", "effect": -1.0, "se": 0.1, "n_before": 30, "n_after": 30}
BETTER = {"decision": "better", "reason": "clear", "effect": 1.0, "se": 0.1, "n_before": 30, "n_after": 30}


def it(section, key, value, **kw):
    return dict({"section": section, "key": key, "value": value}, **kw)


def ap(root, a1, items, now=S, role="shop", **kw):
    return ex.apply_policy(root, items, now, _traj(root), role=role, categories=CATS, validate=a1.load, **kw)


def force(monkeypatch, by_id):
    """evaluate -> the verdict chosen per experiment id (default worse)."""
    monkeypatch.setattr(ex, "evaluate", lambda root, exp, now, data=None: dict(by_id.get(exp["id"], WORSE)))
    monkeypatch.setattr(
        ex, "evaluate_split", lambda root, exp, now, data=None: dict(by_id.get(exp["id"], WORSE), n_a=30, n_b=30)
    )


def tick(root, a1, now):
    """Tick once. A verdict never writes policy.json (byte-identical across the tick, 11-12); each row it ended
    measured on a worse verdict then has its own key put back through ex._restore, the key-level restore the kernel
    still uses to roll back a failed registration (and verdicts used before 11-12): the put-back these tests check."""
    before = _bytes(root)
    ended = ex.tick(root, now, _traj(root), CATS, validate=a1.load)
    assert _bytes(root) == before
    for e in ended:
        if e["status"] == "measured" and e["verdict"].get("decision") == "worse":
            for x in e["items"] if e.get("mode") in ex.GROUP_MODES else [e]:
                if isinstance(x.get("raw"), dict):
                    r = ex._restore(root, x["section"], x["key"], x["raw"], _traj(root), now, "test",
                                    pe.kernel_tag(now, e["id"]), a1.load)
                    assert r["ok"], r
    return [(e["id"], e["status"]) for e in ended]


def st(root):
    return {e["id"]: e["status"] for e in ex.load(root)}


def _bytes(root):
    return open(os.path.join(root, "policy.json"), "rb").read()


def _write_pol(root, pol):
    with open(os.path.join(root, "policy.json"), "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False)


def _old_whole_base_restore(pol, section, raw):
    """The revert before 11-12 (whole raw base back), as policy_edit wrote the file."""
    pol = copy.deepcopy(pol)
    blk = pe._block(pol, section, create=bool(raw["present"]))
    if raw["present"]:
        blk[raw["base"]] = copy.deepcopy(raw["value"])
    elif isinstance(blk, dict):
        blk.pop(raw["base"], None)
    cats = pol.get("categories")
    if isinstance(cats, dict) and section in cats and not cats[section]:
        del cats[section]
    return (json.dumps(pol, ensure_ascii=False, indent=2) + "\n").encode()


def _with_accept_c(root):
    P = _pol(root)
    for k in ("food", "goods"):
        P["buyer"]["defaults"][k]["accept_c"] = {"max_pay_frac": 0.6, "open_frac": 0.4}
    _write_pol(root, P)


# --- 1. a revert puts back its own key only


def test_later_direct_write_of_a_sibling_grade_survives_the_revert(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "fresh_discount.B", 0.2, test="before_after")])
    out = ap(root, a1, [it("defaults.goods", "fresh_discount.C", 0.45)], now=S + 600)
    assert out["voided"] == [] and st(root) == {1: "running"}
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["defaults"]["goods"]["fresh_discount"] == {"B": 0.08, "C": 0.45}
    v = ex.load(root)[0]["verdict"]
    assert v["mixed_by"] == ["defaults.goods fresh_discount.C"]
    assert "진행 중 같은 범위의 다른 변경 defaults.goods fresh_discount.C (비교가 섞임)" in "\n".join(
        ex.lines(root, S + 100 * H)
    )


def test_same_done_direct_sibling_survives_the_revert(root, a1, monkeypatch):
    out = ap(root, a1, [it("defaults.goods", "fresh_discount.B", 0.2, test="before_after"),
                        it("defaults.goods", "fresh_discount.C", 0.45)])
    assert all(r["ok"] for r in out["results"]), out
    assert ex.load(root)[0]["raw"] == {"base": "fresh_discount", "present": True, "value": {"B": 0.08, "C": 0.3}}
    force(monkeypatch, {})
    tick(root, a1, S + 100 * H)
    assert _pol(root)["defaults"]["goods"]["fresh_discount"] == {"B": 0.08, "C": 0.45}


def test_two_experiments_on_sibling_grades_in_one_done(root, a1, monkeypatch):
    out = ap(root, a1, [it("defaults.goods", "fresh_discount.B", 0.2, test="before_after", window_h=2),
                        it("defaults.goods", "fresh_discount.C", 0.45, test="before_after", window_h=4)])
    assert all(r["ok"] for r in out["results"]), out
    e = {x["id"]: x for x in ex.load(root)}
    force(monkeypatch, {1: BETTER, 2: WORSE})
    assert tick(root, a1, e[1]["eval_ts"] + 1) == [(1, "measured")]
    assert tick(root, a1, e[2]["eval_ts"] + 1) == [(2, "measured")]
    assert _pol(root)["defaults"]["goods"]["fresh_discount"] == {"B": 0.2, "C": 0.3}  # #1's kept B stays


def test_shop_grade_floor_list_sibling(root, a1, monkeypatch):
    out = ap(root, a1, [it("shop", "grade_floor_list.A", 0.6, test="before_after"),
                        it("shop", "grade_floor_list.B", 0.5)])
    assert all(r["ok"] for r in out["results"]), out
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["shop"]["grade_floor_list"] == {"C": 0.55, "B": 0.5}  # A had no floor: removed again


def test_buyer_accept_c_later_sibling_write_survives(root, a1, monkeypatch):
    _with_accept_c(root)
    out = ap(root, a1, [it("buyer.defaults.food", "accept_c.max_pay_frac", 0.7, test="before_after")], role="buyer")
    assert out["results"][0]["ok"], out
    out = ap(root, a1, [it("buyer.defaults.food", "accept_c.open_frac", 0.3)], now=S + 600, role="buyer")
    assert out["results"][0]["ok"] and out["voided"] == [], out
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["buyer"]["defaults"]["food"]["accept_c"] == {"max_pay_frac": 0.6, "open_frac": 0.3}


def test_c_first_leaves_restores_that_leaf_only(root, a1, monkeypatch):
    _write_pol(root, dict(_pol(root), shop=dict(_pol(root)["shop"], c_first_leaves=["소파/의자/스툴"])))
    ap(root, a1, [it("shop", "c_first_leaves.침대/매트리스", 1, test="before_after")])
    assert _pol(root)["shop"]["c_first_leaves"] == ["소파/의자/스툴", "침대/매트리스"]
    ap(root, a1, [it("shop", "c_first_leaves.소파/의자/스툴", 0)], now=S + 600)
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["shop"]["c_first_leaves"] == []  # the later removal of the other leaf stays


def test_group_revert_restores_each_members_own_key(root, a1, monkeypatch):
    out = ap(root, a1, [it("defaults.goods", "fresh_discount.B", 0.2, test="before_after", group="G"),
                        it("shop", "max_spend_frac", 0.4, test="before_after", group="G")])
    assert all(r["ok"] for r in out["results"]), out
    ap(root, a1, [it("defaults.goods", "fresh_discount.C", 0.45)], now=S + 600)
    assert st(root) == {1: "running"}
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    P = _pol(root)
    assert P["defaults"]["goods"]["fresh_discount"] == {"B": 0.08, "C": 0.45}
    assert "max_spend_frac" not in P["shop"]


def test_group_with_sibling_grades_reverts_to_the_exact_file(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "fresh_discount.B", 0.2, test="before_after", group="G"),
                  it("defaults.goods", "fresh_discount.C", 0.45, test="before_after", group="G"),
                  it("shop", "max_spend_frac", 0.4, test="before_after", group="G")])
    force(monkeypatch, {})
    tick(root, a1, S + 100 * H)
    assert _pol(root) == POLICY


# --- unchanged base: byte-identical to the whole-base restore


def test_unchanged_base_revert_is_byte_identical_to_the_whole_base_restore(root, a1, monkeypatch):
    _with_accept_c(root)
    cases = [
        ("shop", it("defaults.goods", "fresh_discount.B", 0.2)),  # present dict, grade present
        ("shop", it(BED, "fresh_discount.B", 0.1)),  # inherited dict, category entry held another key
        ("shop", it(SOFA, "fresh_discount.C", 0.2)),  # inherited dict, no category entry
        ("shop", it("shop", "grade_floor_list.A", 0.6)),  # present dict, grade absent
        ("shop", it("shop", "c_first_leaves.침대/매트리스", 1)),  # no list
        ("buyer", it("buyer.defaults.goods", "accept_c.open_frac", 0.3)),
        ("shop", it(BED, "stock_target", 40)),  # scalar
    ]
    force(monkeypatch, {})
    for n, (role, x) in enumerate(cases):
        t = S + n * 1000 * H
        before = _pol(root)
        out = ap(root, a1, [dict(x, test="before_after")], now=t, role=role)
        assert out["results"][0]["ok"], (x, out)
        e = ex.load(root)[-1]
        want = _old_whole_base_restore(_pol(root), x["section"], e["raw"])
        assert tick(root, a1, t + 100 * H) == [(e["id"], "measured")]
        assert _bytes(root) == want, x
        assert _pol(root) == before, x


# --- legacy ledger rows


def test_legacy_raw_row_without_inherited_reverts(root, a1, monkeypatch):
    ap(root, a1, [it(BED, "fresh_discount.B", 0.1, test="before_after")])
    rows = ex.load(root)
    assert rows[0]["raw"]["inherited"] == {"B": 0.08, "C": 0.3}
    rows[0]["raw"] = {k: rows[0]["raw"][k] for k in ("base", "present", "value")}  # as older code wrote it
    ex.save(root, rows)
    ap(root, a1, [it(BED, "price_mult", 0.85)], now=S + 600)  # another key of the same category entry
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["categories"][BED] == {"stock_target": 36, "price_mult": 0.85}


def test_legacy_raw_row_present_dict_reverts_its_grade(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.food", "fresh_discount.C", 0.4, test="before_after")])
    ap(root, a1, [it("defaults.food", "fresh_discount.B", 0.1)], now=S + 600)
    force(monkeypatch, {})
    tick(root, a1, S + 100 * H)
    assert _pol(root)["defaults"]["food"]["fresh_discount"] == {"B": 0.1, "C": 0.3}


def test_row_without_raw_ends_measured_and_keeps_the_value(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="before_after")])
    rows = ex.load(root)
    del rows[0]["raw"]
    ex.save(root, rows)
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["defaults"]["goods"]["price_mult"] == 0.8


# --- inherited per-grade dicts: no pinned copy stays


def test_inherited_dict_goes_back_to_inheriting_after_a_parent_change(root, a1, monkeypatch):
    ap(root, a1, [it(BED, "fresh_discount.B", 0.1, test="before_after")])
    assert _pol(root)["categories"][BED]["fresh_discount"] == {"B": 0.1, "C": 0.3}  # materialised copy
    ap(root, a1, [it("defaults.goods", "fresh_discount.C", 0.45)], now=S + 600)
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    P = _pol(root)
    assert P["categories"][BED] == {"stock_target": 36}  # no pinned C=0.3
    assert pe.effective(P, BED, "fresh_discount.C") == 0.45


def test_inherited_dict_keeps_a_later_own_grade(root, a1, monkeypatch):
    ap(root, a1, [it(SOFA, "fresh_discount.B", 0.1, test="before_after")])
    ap(root, a1, [it(SOFA, "fresh_discount.C", 0.2)], now=S + 600)
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [(1, "measured")]
    assert _pol(root)["categories"][SOFA] == {"fresh_discount": {"B": 0.08, "C": 0.2}}


# --- voiding: own key only


def test_unrelated_shop_write_voids_nothing_and_is_named_on_the_verdicts(root, a1, monkeypatch):
    r = ap(root, a1, [it(BED, "price_mult", 0.8, test="before_after"),
                      it("defaults.food", "max_rounds", 6, test="before_after"),
                      it(SOFA, "min_margin", 0.1, test="before_after")])
    assert all(x["ok"] for x in r["results"])
    r = ap(root, a1, [it("shop", "grade_floor_list.A", 0.6)], now=S + 60)
    assert r["voided"] == [] and set(st(root).values()) == {"running"}
    force(monkeypatch, {})
    got = tick(root, a1, S + 100 * H)
    assert sorted(got) == [(1, "measured"), (2, "measured"), (3, "measured")]
    assert all(e["verdict"]["mixed_by"] == ["shop grade_floor_list.A"] for e in ex.load(root))
    P = _pol(root)
    assert P["shop"]["grade_floor_list"] == {"C": 0.55, "A": 0.6}
    assert P["defaults"]["food"]["max_rounds"] == 4 and P["categories"][BED] == {"stock_target": 36}


def test_same_key_write_voids_and_never_reverts(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="before_after")])
    out = ap(root, a1, [it("defaults.goods", "price_mult", 0.7)], now=S + 600)
    assert out["results"][0]["ok"] and [v["id"] for v in out["voided"]] == [1]
    assert ex.load(root)[0]["verdict"]["reason"] == "overwritten"
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [] and _pol(root)["defaults"]["goods"]["price_mult"] == 0.7


def test_group_member_own_key_write_voids_the_group(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="before_after", group="G"),
                  it("defaults.food", "max_rounds", 7, test="before_after", group="G")])
    out = ap(root, a1, [it("defaults.food", "max_rounds", 9)], now=S + 600)
    assert [v["id"] for v in out["voided"]] == [1]
    force(monkeypatch, {})
    assert tick(root, a1, S + 100 * H) == [] and _pol(root)["defaults"]["food"]["max_rounds"] == 9


def test_split_whole_dict_arm_is_voided_by_a_sibling_grade_in_its_section(root, a1):
    out = ap(root, a1, [it(BED, "fresh_discount.B", 0.2, test="split")], split_ok=True)
    assert out["results"][0]["ok"], out
    assert ex.load_arms(root)[0]["path"] == ["categories", BED, "fresh_discount"]  # carries the whole dict
    out = ap(root, a1, [it(BED, "fresh_discount.C", 0.45)], now=S + 600, split_ok=True)
    assert [v["id"] for v in out["voided"]] == [1] and ex.load_arms(root) == []


def test_cross_role_write_is_caught_by_the_tick_guard(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="before_after")], role="shop")
    out = ap(root, a1, [it("defaults.goods", "price_mult", 0.7)], now=S + 600, role="buyer")
    assert out["voided"] == []
    force(monkeypatch, {})
    tick(root, a1, S + 100 * H)
    e = ex.load(root)[0]
    assert e["status"] == "voided" and e["verdict"]["reason"] == "value_changed"
    assert _pol(root)["defaults"]["goods"]["price_mult"] == 0.7


# --- split overwrite: the arm goes before policy.json changes


def test_split_arm_is_removed_before_the_policy_write(root, a1, monkeypatch):
    out = ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="split")], split_ok=True)
    assert out["results"][0]["ok"] and ex.load_arms(root), out
    seen = []
    real = pe._swap

    def spy(root_, *a, **k):
        seen.append(ex.load_arms(root_))
        return real(root_, *a, **k)

    monkeypatch.setattr(pe, "_swap", spy)
    out = ap(root, a1, [it("defaults.goods", "price_mult", 0.85)], now=S + 600, split_ok=True)
    assert seen == [[]] and [v["id"] for v in out["voided"]] == [1]
    assert _pol(root)["defaults"]["goods"]["price_mult"] == 0.85


def test_split_arm_comes_back_when_the_write_fails(root, a1, monkeypatch):
    ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="split")], split_ok=True)
    arms = ex.load_arms(root)

    def boom(*a, **k):
        raise ValueError("injected")

    monkeypatch.setattr(pe, "_swap", boom)
    out = ap(root, a1, [it("defaults.goods", "price_mult", 0.85)], now=S + 600, split_ok=True)
    assert not out["results"][0]["ok"] and out["voided"] == []
    assert st(root) == {1: "running"} and ex.load_arms(root) == arms


# --- item checks


def test_refused_first_duplicate_does_not_block_the_valid_second(root, a1):
    r = ap(root, a1, [it("defaults.goods", "price_mult", 9), it("defaults.goods", "price_mult", 0.8)])
    assert [x["ok"] for x in r["results"]] == [False, True]
    assert _pol(root)["defaults"]["goods"]["price_mult"] == 0.8


def test_two_valid_items_on_one_key_refuse_the_later(root, a1):
    r = ap(root, a1, [it("defaults.goods", "price_mult", 0.8, test="before_after"),
                      it("defaults.goods", "price_mult", 0.7)])
    assert [x["ok"] for x in r["results"]] == [True, False] and "두 번" in r["results"][1]["error"]


def test_refused_group_member_is_refused_alone(root, a1):
    r = ap(root, a1, [it(BED, "price_mult", 0.8, test="before_after", group="G"),
                      it("defaults.food", "max_rounds", 50, test="before_after", group="G"),
                      it("defaults.goods", 1, 0.5, test="before_after", group="G")])
    assert [x["ok"] for x in r["results"]] == [True, False, False]
    e = ex.load(root)[0]
    assert e["mode"] == "group" and [(x["section"], x["key"]) for x in e["items"]] == [(BED, "price_mult")]
