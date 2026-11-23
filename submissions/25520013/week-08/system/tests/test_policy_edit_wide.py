"""DESIGN 14.3: wider policy edits (shop, buyer.defaults, per-grade keys, c_first_leaves), still one number per edit.
Files written here are checked with the rule code's real policy.load (system/policy.py, read only)."""

import importlib.util
import json
import os
import time

import pytest
from kernel import policy_edit as pe
from kernel import tools
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A1_POLICY = os.path.join(HERE, "policy.py")
LOGIN = "secret-login"
BED = "가구/침구>가구>침대/매트리스"
SOFA = "가구/침구>가구>소파/의자/스툴"
SNACK = "식품>과자"

POLICY = {
    "owner": {"github": LOGIN},
    "shop": {
        "liquidation_line": 1500000,
        "grade_floor_list": {"C": 0.55},
        "restock_priority": [BED],
    },
    "defaults": {
        "food": {
            "price_mult": 0.92,
            "max_rounds": 4,
            "fresh_discount": {"B": 0.06, "C": 0.3},
        },
        "goods": {
            "price_mult": 0.9,
            "max_rounds": 4,
            "fresh_discount": {"B": 0.08, "C": 0.3},
            "warranty_hours": {"A": 6, "B": 6, "C": 0},
            "max_warranty_hours": 6,
        },
    },
    "categories": {BED: {"stock_target": 36}, "소파/의자/스툴": {"price_mult": 0.7}},
    "buyer": {
        "defaults": {
            "food": {
                "open_frac": 0.55,
                "max_pay_frac": 0.85,
                "step_frac": 0.07,
                "max_rounds": 5,
            },
            "goods": {
                "open_frac": 0.55,
                "max_pay_frac": 0.85,
                "step_frac": 0.07,
                "max_rounds": 5,
            },
        },
        "categories": {},
    },
    "bounds": {"price_mult": [0.5, 1.5], "max_pay_frac": [0.3, 1.0]},
}


@pytest.fixture(scope="module")
def a1():
    if not os.path.exists(A1_POLICY):
        pytest.skip("policy.py not found at the system root")
    spec = importlib.util.spec_from_file_location("_r3a1_policy_wide", A1_POLICY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    (r / "a2").mkdir(parents=True)
    (r / ".market").mkdir()
    (r / "policy.json").write_text(json.dumps(POLICY, ensure_ascii=False))
    cat = {
        "S1": {"category": SOFA, "kind": "goods"},
        "S2": {"category": BED, "kind": "goods"},
    }
    (r / ".market/catalog.json").write_text(json.dumps(cat, ensure_ascii=False))
    return str(r)


def _traj(root):
    return Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))


def _pol(root):
    return json.load(open(os.path.join(root, "policy.json"), encoding="utf-8"))


def _edit(root, a1, items, now=None, traj=None):
    return pe.apply(
        root,
        items,
        traj or _traj(root),
        now or time.time(),
        categories={SOFA, BED, SNACK},
        validate=a1.load,
    )


def _it(section, key, value, reason="근거 숫자"):
    return {"section": section, "key": key, "value": value, "reason": reason}


def test_legal_edits_in_each_new_section_pass_attempt1_validate(root, a1):
    res = _edit(
        root,
        a1,
        [
            _it("buyer.defaults.goods", "max_pay_frac", 0.9),
            _it("shop", "grade_floor_list.C", 0.6),
            _it("defaults.goods", "fresh_discount.C", 0.25),
        ],
    )
    assert [r["ok"] for r in res] == [True, True, True], res
    res2 = _edit(
        root,
        a1,
        [
            _it("buyer.defaults.food", "max_pay_frac", 0.8),
            _it(BED, "warranty_hours.C", 6),
            _it("shop", "c_first_leaves.침대/매트리스", 1),
        ],
        now=time.time() + 1,
    )
    assert [r["ok"] for r in res2] == [True, True, True], res2
    pol = _pol(root)
    a1.validate(pol)
    assert pol["buyer"]["defaults"]["goods"]["max_pay_frac"] == 0.9
    assert pol["buyer"]["defaults"]["food"]["max_pay_frac"] == 0.8
    assert pol["shop"]["grade_floor_list"] == {"C": 0.6}
    assert pol["defaults"]["goods"]["fresh_discount"] == {"B": 0.08, "C": 0.25}
    assert pol["shop"]["c_first_leaves"] == ["침대/매트리스"]
    # what the rule code itself now reads
    assert a1.resolve(pol, BED, "goods")["warranty_hours"] == {"A": 6, "B": 6, "C": 6}
    assert a1.resolve(pol, BED, "goods", side="buyer")["max_pay_frac"] == 0.9


def test_trajectory_row_keeps_fields_with_dotted_key(root, a1):
    traj = _traj(root)
    assert _edit(
        root,
        a1,
        [_it("shop", "grade_floor_list.C", 0.6, "C 가구 시장 0.56")],
        traj=traj,
    )[0]["ok"]
    row = [r for r in traj.since(0) if r["kind"] == "policy_edit"][0]
    assert (row["section"], row["key"], row["old"], row["new"], row["reason"]) == (
        "shop",
        "grade_floor_list.C",
        0.55,
        0.6,
        "C 가구 시장 0.56",
    )
    assert row["backup"].startswith(pe.BAK_DIR)


def test_inherit_then_copy_keeps_the_other_grades(root, a1):
    res = _edit(
        root,
        a1,
        [_it(SOFA, "fresh_discount.B", 0.12), _it(SOFA, "warranty_hours.C", 6)],
    )
    assert [r["ok"] for r in res] == [True, True], res
    assert res[0]["old"] == 0.08 and res[1]["old"] == 0  # inherited from defaults.goods
    pol = _pol(root)
    assert pol["categories"][SOFA] == {
        "fresh_discount": {"B": 0.12, "C": 0.3},
        "warranty_hours": {"A": 6, "B": 6, "C": 6},
    }
    assert pol["defaults"] == POLICY["defaults"] and pol["categories"][
        "소파/의자/스툴"
    ] == {"price_mult": 0.7}
    r = a1.resolve(pol, SOFA, "goods")
    assert r["fresh_discount"] == {"B": 0.12, "C": 0.3} and r["price_mult"] == 0.7


def test_new_grade_floor_says_it_is_new(root, a1):
    res = _edit(root, a1, [_it("shop", "grade_floor_list.A", 0.85)])
    assert res[0]["ok"] and res[0]["old"] is None and "처음 두는 값" in res[0]["note"]
    assert _pol(root)["shop"]["grade_floor_list"] == {"C": 0.55, "A": 0.85}
    bad = _edit(root, a1, [_it("shop", "grade_floor_list.B", 0.95)])
    assert not bad[0]["ok"] and "outside" in bad[0]["error"]


def test_c_first_leaves_toggles_add_and_remove_idempotently(root, a1):
    t0, traj, leaf = time.time(), _traj(root), "c_first_leaves.소파/의자/스툴"
    assert _edit(root, a1, [_it("shop", leaf, 1)], now=t0, traj=traj)[0]["ok"]
    again = _edit(root, a1, [_it("shop", leaf, 1)], now=t0 + 3600, traj=traj)
    assert not again[0]["ok"] and "already" in again[0]["error"]
    assert _pol(root)["shop"]["c_first_leaves"] == ["소파/의자/스툴"]
    out = _edit(root, a1, [_it("shop", leaf, 0)], now=t0 + 7200, traj=traj)
    assert (
        out[0]["ok"]
        and out[0]["old"] == 1
        and _pol(root)["shop"]["c_first_leaves"] == []
    )
    gone = _edit(root, a1, [_it("shop", leaf, 0)], now=t0 + 10800, traj=traj)
    assert not gone[0]["ok"] and _pol(root)["shop"]["c_first_leaves"] == []
    a1.validate(_pol(root))


def test_refusals_leave_the_file_untouched(root, a1):
    before = open(os.path.join(root, "policy.json"), "rb").read()
    bad = [
        _it("buyer.defaults.goods", "max_pay_frac", 0.45),  # below 0.5
        _it(
            "buyer.defaults.goods", "price_mult", 0.9
        ),  # shop-side key in a buyer section
        _it("shop", "price_mult", 0.9),  # shop-side key in shop
        _it(BED, "max_pay_frac", 0.85),  # buyer key in a shop-side section
        _it("defaults.food", "warranty_hours.A", 6),  # goods-only key in food
        _it(SNACK, "warranty_hours.A", 6),  # goods-only key in a food category
        _it("defaults.goods", "grade_floor_list.C", 0.6),  # shop-only key
        _it("defaults.goods", "fresh_discount.A", 0.1),  # no A in fresh_discount
        _it("defaults.goods", "fresh_discount.C", 0.6),  # above 0.5
        _it("defaults.goods", "warranty_hours.C", 12),  # above max_warranty_hours 6
        _it("defaults.goods", "warranty_hours.C", 3.5),  # whole hours only
        _it("shop", "grade_floor_list.C", "0.6"),  # not a number
        _it("shop", "c_first_leaves.없는잎", 1),  # not a known leaf
        _it("shop", "c_first_leaves.가구>소파", 1),  # a path, not a leaf
        _it("shop", "c_first_leaves.소파/의자/스툴", 2),  # 1 or 0 only
        _it("bounds", "price_mult", 1.0),  # human only
        _it("buyer.defaults", "max_pay_frac", 0.9),  # not a section
    ]
    res = _edit(root, a1, bad)
    assert [r["ok"] for r in res] == [False] * len(bad), [
        (i, r) for i, r in enumerate(res) if r["ok"]
    ]
    assert (
        "only in buyer.defaults" in res[3]["error"]
        and "only in shop" in res[6]["error"]
    )
    assert "사람만" in res[15]["error"]
    assert open(os.path.join(root, "policy.json"), "rb").read() == before


def test_no_cooldown_or_per_wake_limit_and_dotted_keys_are_separate(root, a1):
    # 11-11: the cooldown, the step limit and the per-wake count are gone
    t0, traj = time.time(), _traj(root)
    assert _edit(
        root, a1, [_it("defaults.goods", "fresh_discount.C", 0.26)], now=t0, traj=traj
    )[0]["ok"]
    res = _edit(
        root,
        a1,
        [
            _it(
                "defaults.goods", "fresh_discount.C", 0.22
            ),  # same dotted key a minute later
            _it(
                "defaults.goods", "fresh_discount.B", 0.1
            ),  # another grade of the same dict is a different key
        ],
        now=t0 + 60,
        traj=traj,
    )
    assert res[0]["ok"] and res[0]["old"] == 0.26 and res[1]["ok"]
    four = _edit(
        root,
        a1,
        [
            _it("shop", "grade_floor_list.C", 0.5),
            _it("buyer.defaults.food", "max_pay_frac", 0.8),
            _it(SOFA, "warranty_hours.C", 6),
            _it("shop", "c_first_leaves.침대/매트리스", 1),
        ],
        now=t0 + 120,
        traj=traj,
    )
    assert [r["ok"] for r in four] == [True, True, True, True]


def test_effective_overlays_the_leaf_entry_like_attempt1_resolve(root, a1):
    # categories has "소파/의자/스툴" (leaf name) price_mult 0.7; the full path SOFA has no entry. The rule code prices SOFA
    # at 0.7, so 0.62 is a 0.08 step (allowed); the old lookup saw defaults.goods 0.9, a 0.28 step, and refused it.
    pol = _pol(root)
    assert (
        pe.effective(pol, SOFA, "price_mult")
        == a1.resolve(pol, SOFA, "goods")["price_mult"]
        == 0.7
    )
    res = _edit(root, a1, [_it(SOFA, "price_mult", 0.62)])
    assert res[0]["ok"] and res[0]["old"] == 0.7
    assert a1.resolve(_pol(root), SOFA, "goods")["price_mult"] == 0.62


def test_policy_tool_shows_new_sections_and_keys(root):
    out = tools.call("policy", "operator", root)
    assert (
        '- shop: {"grade_floor_list.A": null, "grade_floor_list.B": null, "grade_floor_list.C": 0.55, "max_inventory_cost_frac":'
        in out
    )
    assert '- buyer.defaults.goods: {"max_pay_frac": 0.85' in out
    assert '"fresh_discount.C": 0.3' in out and '"warranty_hours.C": 0' in out
    food = [ln for ln in out.splitlines() if ln.startswith("- defaults.food")][0]
    assert "fresh_discount.C" in food and "warranty_hours" not in food
    assert "bounds 구역은 사람만" in out


def test_food_fresh_discount_and_grade_c_buy_cap(root, a1):
    """2026-11-10: the operator also sets the food grade discount and the buyer's grade C cap."""
    pol = _pol(root)
    pol["defaults"]["food"]["fresh_discount"] = {"B": 0.06, "C": 0.3}
    pol["buyer"]["defaults"]["goods"]["accept_c"] = {"max_pay_frac": 0.55, "open_frac": 0.35}
    json.dump(pol, open(os.path.join(root, "policy.json"), "w", encoding="utf-8"), ensure_ascii=False)
    res = _edit(
        root,
        a1,
        [
            _it("defaults.food", "fresh_discount.C", 0.25),
            _it("buyer.defaults.goods", "accept_c.max_pay_frac", 0.6),
            _it("buyer.defaults.food", "accept_c.max_pay_frac", 0.6),  # no accept_c block there
        ],
    )
    assert [r["ok"] for r in res] == [True, True, False], res
    pol = _pol(root)
    assert pol["defaults"]["food"]["fresh_discount"] == {"B": 0.06, "C": 0.25}
    assert pol["buyer"]["defaults"]["goods"]["accept_c"] == {"max_pay_frac": 0.6, "open_frac": 0.35}
    assert "accept_c" in res[2]["error"]


def test_buyer_open_and_step_and_bad_inputs(root, a1):
    """2026-11-10 verifier findings: a non-string key or a non-finite value is refused, not raised; a leaf-name
    section is refused (it cannot tell food from goods). The buyer's opening offer and step are editable."""
    before = open(os.path.join(root, "policy.json"), encoding="utf-8").read()
    res = _edit(
        root,
        a1,
        [
            _it("defaults.goods", ["price_mult"], 0.9),
            _it("defaults.goods", "max_rounds", float("nan")),
            _it("defaults.goods", "price_mult", float("inf")),
            _it("소파/의자/스툴", "price_mult", 0.75),
        ],
    )
    assert [r["ok"] for r in res] == [False] * 4, res
    assert open(os.path.join(root, "policy.json"), encoding="utf-8").read() == before
    res = _edit(
        root,
        a1,
        [
            _it("buyer.defaults.food", "open_frac", 0.5),
            _it("buyer.defaults.food", "step_frac", 0.09),
            _it("buyer.defaults.goods", "open_frac", 0.4),  # any distance inside the bounds
        ],
    )
    assert [r["ok"] for r in res] == [True, True, True], res
    assert _pol(root)["buyer"]["defaults"]["food"]["open_frac"] == 0.5
    assert _pol(root)["buyer"]["defaults"]["food"]["step_frac"] == 0.09
