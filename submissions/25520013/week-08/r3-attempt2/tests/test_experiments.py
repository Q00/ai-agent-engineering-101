"""DESIGN 14.4 step 1: experiment ledger, before/after verdict and automatic revert, on synthetic data roots.
Reverted policy files are checked with attempt 1's real policy.load (r3/policy.py, read only). Fixed clock values."""

import copy
import importlib.util
import json
import os

import pytest
from kernel import common as c
from kernel import experiments as ex
from kernel import policy_edit as pe
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A1_POLICY = os.path.join(os.path.dirname(HERE), "r3", "policy.py")
LOGIN = "secret-login"
H = 3600
S = 1_794_000_000.0  # experiment start
BED = "가구/침구>가구>침대/매트리스"
SOFA = "가구/침구>가구>소파/의자/스툴"
SNACK = "식품>과자"
CATS = {BED, SOFA, SNACK}

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
    "categories": {BED: {"stock_target": 36}},
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
CATALOG = {
    "BED1": {"category": BED, "kind": "goods", "list_price": 2_000_000},
    "SOFA1": {"category": SOFA, "kind": "goods", "list_price": 1_000_000},
    "SNK1": {"category": SNACK, "kind": "food", "list_price": 10_000},
}


@pytest.fixture(scope="module")
def a1():
    if not os.path.exists(A1_POLICY):
        pytest.skip("attempt 1 policy.py not found next to attempt 2")
    spec = importlib.util.spec_from_file_location("_r3a1_policy_exp", A1_POLICY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    (r / "a2/board").mkdir(parents=True)
    (r / ".market").mkdir()
    (r / ".r3").mkdir()
    (r / "policy.json").write_text(json.dumps(POLICY, ensure_ascii=False))
    (r / ".market/catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False))
    (r / ".r3/events.jsonl").write_text("")
    return str(r)


def _traj(root):
    return Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=(LOGIN,))


def _pol(root):
    return json.load(open(os.path.join(root, "policy.json"), encoding="utf-8"))


def _set_pol(root, fn):
    pol = _pol(root)
    fn(pol)
    with open(os.path.join(root, "policy.json"), "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False)


def _rows(root, kind):
    return [r for r in _traj(root).since(0) if r.get("kind") == kind]


def _ev(ts, type_, data, src="market", mine=False, role="market"):
    return {
        "ts": ts,
        "type": type_,
        "src": src,
        "role": role,
        "mine": mine,
        "data": data,
    }


_n = [0]


def _sale(ts, sku, amount, cost, wh=0, buyer=None, unit=None):
    """Our shop's sale plus the unit_gone row that carries its cost."""
    _n[0] += 1
    unit = unit or f"u{_n[0]}"
    d = {
        "sku": sku,
        "buyer": buyer or f"b{_n[0]}",
        "unit": unit,
        "amount": amount,
        "own_owner": True,
        "shop": "our shop",
        "order": f"o-{unit}",
        "grade": "A",
    }
    if wh:
        d["warranty_hours"] = wh
    rows = [_ev(ts, "settle", d)]
    if cost is not None:
        rows.append(
            _ev(
                S - 30 * H,
                "unit_gone",
                {"unit": unit, "cost": cost, "category": CATALOG[sku]["category"]},
                src="inventory",
                mine=True,
            )
        )
    return rows


def _other(ts, sku, amount):
    return [
        _ev(
            ts,
            "settle",
            {
                "sku": sku,
                "buyer": "x",
                "shop": "rival",
                "amount": amount,
                "own_owner": False,
            },
        )
    ]


def _buy(ts, sku, amount, list_price=None):
    d = {
        "sku": sku,
        "buyer": f"{LOGIN}-buyer",
        "shop": "rival",
        "amount": amount,
        "own_owner": True,
    }
    if list_price:
        d["list_price"] = list_price
    return [_ev(ts, "settle", d)]


def _broke(at, sale_rows):
    d = sale_rows[0]["data"]
    return [
        _ev(
            at,
            "broken",
            {"sku": d["sku"], "buyer": d["buyer"], "broke_at": at},
            mine=True,
        )
    ]


def _review(ts, sale_rows, stars):
    return [
        _ev(
            ts,
            "review",
            {"order": sale_rows[0]["data"]["order"], "stars": stars},
            mine=True,
            role="shop",
        )
    ]


def _write_events(root, rows):
    with open(os.path.join(root, ".r3/events.jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _hourly(t0, hours, sku, gross, per_h=3):
    """per_h sales per hour from t0 with the given gross each (amount 1M + gross, cost 1M), gross varying a little."""
    rows = []
    for h in range(hours):
        for i in range(per_h):
            g = gross * (1 + 0.05 * (h % 3 - 1))
            rows += _sale(t0 + h * H + i * 600 + 60, sku, 1_000_000 + g, 1_000_000)
    return rows


def _put_ledger(root, exps):
    ex.save(root, exps)


def _exp(
    section,
    key="price_mult",
    old=0.9,
    new=0.8,
    started=S,
    window_h=3,
    status="running",
    eid=1,
    **kw,
):
    side, scope = ex.scope_of(section, key)
    e = {
        "id": eid,
        "section": section,
        "key": key,
        "old": old,
        "new": new,
        "started_ts": started,
        "window_h": window_h,
        "maturity_h": ex.MATURITY_H[side],
        "scope": scope,
        "side": side,
        "metric": ex.METRIC[side],
        "expect": "가설",
        "status": status,
        "extended": False,
        **kw,
    }
    e["eval_ts"] = ex._eval_ts(e)
    return e


# --- scope and conflicts


def test_scope_of():
    assert ex.scope_of(BED, "stock_target") == ("shop", "침대/매트리스")
    assert ex.scope_of("소파/의자/스툴", "price_mult") == ("shop", "소파/의자/스툴")
    assert ex.scope_of("defaults.food", "price_mult") == ("shop", "kind:food")
    assert ex.scope_of("defaults.goods", "price_mult") == ("shop", "kind:goods")
    assert ex.scope_of("shop", "grade_floor_list.C") == ("shop", "shop:all")
    assert ex.scope_of("buyer.defaults.food", "max_pay_frac") == ("buyer", "buyer:food")
    assert ex.scope_of("buyer.defaults.goods", "max_pay_frac") == (
        "buyer",
        "buyer:goods",
    )


@pytest.mark.parametrize(
    "running_sec,edit_sec,hit",
    [
        (BED, BED, True),
        (BED, "침대/매트리스", True),  # leaf entry and full path share the leaf scope
        (BED, SOFA, False),
        ("shop", BED, True),
        (BED, "shop", True),
        ("shop", "defaults.food", True),
        ("shop", "shop", True),
        ("defaults.goods", BED, True),
        (BED, "defaults.goods", True),
        ("defaults.food", BED, False),
        ("defaults.food", SNACK, True),
        (SNACK, "defaults.food", True),
        ("defaults.goods", "defaults.goods", True),
        ("defaults.goods", "defaults.food", False),
        ("buyer.defaults.food", "buyer.defaults.food", True),
        ("buyer.defaults.food", "buyer.defaults.goods", False),
        ("buyer.defaults.food", "defaults.food", False),
        ("shop", "buyer.defaults.goods", False),
        ("buyer.defaults.goods", "shop", False),
    ],
)
def test_blocked_pairs(root, running_sec, edit_sec, hit):
    e = _exp(running_sec, eid=7)
    _put_ledger(root, [e])
    msg = ex.blocked(root, edit_sec, "price_mult", S + 60)
    assert (msg is not None) is hit
    if hit:
        assert "#7" in msg and f"{c.kst(e['eval_ts'], '%H:%M')} 판정" in msg


def test_ended_experiment_does_not_block(root):
    _put_ledger(
        root, [_exp(BED, status="kept"), _exp("shop", status="reverted", eid=2)]
    )
    assert ex.blocked(root, BED, "price_mult", S) is None


# --- register


def test_register_defaults_and_clamps(root):
    with open(os.path.join(root, "a2/board", f"{int(S - 600)}.json"), "w") as f:
        json.dump(
            {
                "buyers": [
                    {"name": "us", "owner": LOGIN, "saved": 0.31},
                    {"name": "x", "saved": 0.5},
                ]
            },
            f,
        )
    traj = _traj(root)
    r = lambda sec, key: {"section": sec, "key": key, "old": 1, "new": 2}
    a = ex.register(
        root, r(BED, "price_mult"), "값을 내리면 판매가 는다", None, S, traj
    )
    assert (a["id"], a["window_h"], a["maturity_h"], a["eval_ts"]) == (
        1,
        3,
        2,
        S + 5 * H,
    )
    assert (a["side"], a["scope"], a["metric"], a["status"], a["extended"]) == (
        "shop",
        "침대/매트리스",
        "net_profit_per_h",
        "running",
        False,
    )
    assert ex.register(root, r(BED, "stock_target"), "", None, S, traj)["window_h"] == 4
    assert (
        ex.register(root, r("defaults.goods", "qty_per_buy"), "", None, S, traj)[
            "window_h"
        ]
        == 4
    )
    b = ex.register(root, r("buyer.defaults.goods", "max_pay_frac"), "", None, S, traj)
    assert (b["window_h"], b["maturity_h"], b["eval_ts"], b["s0"], b["metric"]) == (
        2,
        0,
        S + 2 * H,
        0.31,
        "saved_gain_per_h",
    )
    assert ex.register(root, r(BED, "price_mult"), "", 1, S, traj)["window_h"] == 2
    assert ex.register(root, r(BED, "price_mult"), "", 20, S, traj)["window_h"] == 8
    assert ex.register(root, r(BED, "price_mult"), "", 5.6, S, traj)["window_h"] == 6
    assert [e["id"] for e in ex.load(root)] == [1, 2, 3, 4, 5, 6, 7]
    starts = _rows(root, "experiment_start")
    assert (
        len(starts) == 7
        and starts[0]["expect"] == "값을 내리면 판매가 는다"
        and starts[0]["scope"] == "침대/매트리스"
    )


# --- metrics


def test_shop_metric_maturity_cut(root):
    early = _sale(S + 10, "BED1", 1_500_000, 1_000_000, wh=6)
    late = _sale(S + 20, "BED1", 1_400_000, 1_000_000, wh=6)
    no_cost = _sale(S + 30, "BED1", 900_000, None)
    _write_events(
        root,
        early
        + late
        + no_cost
        + _broke(S + 10 + 1 * H, early)
        + _broke(S + 20 + 3 * H, late),
    )
    data = ex.read_events(root, S - 13 * H, S + 10 * H, login=LOGIN)
    m = ex.shop_metric(data, "침대/매트리스", S, S + 3 * H)
    # early: 500k gross - 1.5M refund (broke 1 h after, inside 2 h maturity and 6 h warranty); late: 400k, its 3 h break
    # is past the maturity cut; no_cost: counted, 0 gross
    assert m["n"] == 3
    assert m["value"] == pytest.approx((500_000 - 1_500_000 + 400_000) / 3)
    assert len(m["buckets"]) == 3 and m["buckets"][0] == pytest.approx(-600_000)


def test_shop_metric_scope_by_leaf_and_kind(root):
    rows = (
        _sale(S + 10, "BED1", 1_200_000, 1_000_000)
        + _sale(S + 20, "SOFA1", 1_100_000, 1_000_000)
        + _sale(S + 30, "SNK1", 9_000, 5_000)
        + _other(S + 40, "BED1", 3_000_000)
    )
    _write_events(root, rows)
    data = ex.read_events(root, S - H, S + 4 * H, login=LOGIN)
    bed = ex.shop_metric(data, "침대/매트리스", S, S + 2 * H)
    goods = ex.shop_metric(data, "kind:goods", S, S + 2 * H)
    food = ex.shop_metric(data, "kind:food", S, S + 2 * H)
    every = ex.shop_metric(data, "shop:all", S, S + 2 * H)
    assert (bed["n"], goods["n"], food["n"], every["n"]) == (1, 2, 1, 3)
    assert bed["value"] == pytest.approx(100_000)  # 200k over 2 h
    assert food["value"] == pytest.approx(2_000)
    assert bed["market"] == pytest.approx(4_200_000)  # ours 1.2M + the rival's 3M
    assert goods["market"] == pytest.approx(5_300_000)


def _data(
    before_g,
    after_g,
    mkt_before=10e6,
    mkt_after=10e6,
    n_before=3,
    n_after=3,
    hours=3,
    reviews=None,
):
    """In-memory read_events result: n sales per hour in each window with the given gross each."""
    sales, market = [], []
    for t0, g, n, m in (
        (S - hours * H, before_g, n_before, mkt_before),
        (S, after_g, n_after, mkt_after),
    ):
        for h in range(hours):
            for i in range(n):
                gi = g * (1 + 0.05 * (h % 3 - 1))
                ts = t0 + h * H + i * 300 + 1
                sales.append(
                    {
                        "ts": ts,
                        "amount": 1e6 + gi,
                        "cost": 1e6,
                        "leaf": "침대/매트리스",
                        "kind": "goods",
                        "order": f"o{ts}",
                        "wh": 0,
                        "broke_at": None,
                    }
                )
        market.append((t0 + 1, m, "침대/매트리스", "goods"))
    return {"sales": sales, "market": market, "deals": [], "reviews": reviews or {}}


def test_market_adjustment_clamp():
    e = _exp(BED)
    assert ex.evaluate(None, e, S + 6 * H, _data(1e5, 1e5, 1e6, 10e6))["r"] == 2.0
    assert ex.evaluate(None, e, S + 6 * H, _data(1e5, 1e5, 10e6, 1e6))["r"] == 0.5
    v = ex.evaluate(None, e, S + 6 * H, _data(1e5, 1e5, 10e6, 15e6))
    assert v["r"] == pytest.approx(1.5) and v["before_adj"] == pytest.approx(
        v["before"] * 1.5
    )
    assert ex.evaluate(None, e, S + 6 * H, _data(1e5, 1e5, 0, 15e6))["r"] == 1.0


def test_decision_stars_guard_first():
    d = _data(1e5, 5e5)
    orders = sorted(s["order"] for s in d["sales"])
    d["reviews"] = {
        o: (5 if i < 9 else 4.5) for i, o in enumerate(orders)
    }  # before 9 x 5.0 ... too few
    v = ex.evaluate(None, _exp(BED), S + 6 * H, d)
    assert (
        v["n_reviews_before"] == 9 and v["decision"] == "better"
    )  # guard needs 10 on each side
    d = _data(1e5, 5e5, n_before=4, n_after=4)
    before = [s["order"] for s in d["sales"] if s["ts"] < S]
    after = [s["order"] for s in d["sales"] if s["ts"] >= S]
    d["reviews"] = {**{o: 5 for o in before}, **{o: 4.8 for o in after}}
    v = ex.evaluate(None, _exp(BED), S + 6 * H, d)
    assert (v["decision"], v["reason"]) == ("worse", "stars") and v["effect"] > 0
    assert v["stars_before"] == 5 and v["stars_after"] == pytest.approx(4.8)


def test_decision_thin_clear_unclear():
    e = _exp(BED)
    thin = ex.evaluate(
        None, e, S + 6 * H, _data(1e5, 9e5, n_after=1)
    )  # 3 after-sales over 3 h
    assert (thin["decision"], thin["n_after"]) == ("thin", 3)
    up = ex.evaluate(None, e, S + 6 * H, _data(1e5, 3e5))
    assert (up["decision"], up["reason"]) == ("better", "clear") and up[
        "se"
    ] is not None
    assert up["effect"] > up["se"]
    down = ex.evaluate(None, e, S + 6 * H, _data(3e5, 1e5))
    assert down["decision"] == "worse"
    same = ex.evaluate(None, e, S + 6 * H, _data(1e5, 1.02e5))
    assert (same["decision"], same["reason"]) == ("unclear", "noise")
    # a clear but tiny change (< 10% of max(|before_adj|, FLOOR)) is unclear
    small = ex.evaluate(None, e, S + 6 * H, _data(10_000, 12_000))
    assert small["se"] < small["effect"] < 0.1 * ex.FLOOR["shop"]
    assert small["decision"] == "unclear"
    for k in (
        "before",
        "before_adj",
        "after",
        "r",
        "effect",
        "se",
        "n_before",
        "n_after",
        "stars_before",
        "stars_after",
        "market_before",
        "market_after",
        "decision",
        "reason",
    ):
        assert k in up


def test_buyer_metric_with_s0(root):
    rows = (
        _buy(S + 10, "BED1", 1_200_000)
        + _buy(S + 20, "BED1", 1_600_000)
        + _buy(S + 30, "SNK1", 5_000)
        + _buy(S + 40, "SOFA1", 500_000, list_price=2_000_000)
    )
    _write_events(root, rows)
    data = ex.read_events(root, S - H, S + 3 * H, login=LOGIN)
    assert len(data["deals"]) == 4 and data["sales"] == []
    m = ex.buyer_metric(data, "buyer:goods", S, S + 2 * H, 0.3)
    # surplus fracs 0.4, 0.2 (catalog list 2M) and 0.75 (settle list_price 2M wins over the catalog's 1M); minus s0
    assert m["n"] == 3
    assert m["value"] == pytest.approx((0.1 - 0.1 + 0.45) / 2)
    food = ex.buyer_metric(data, "buyer:food", S, S + 2 * H, 0.3)
    assert food["n"] == 1 and food["value"] == pytest.approx((0.5 - 0.3) / 2)


# --- tick


def _due(e):
    return e["eval_ts"] + 1


def test_tick_kept(root, a1):
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    _write_events(
        root, _hourly(S - 3 * H, 3, "BED1", 100_000) + _hourly(S, 3, "BED1", 300_000)
    )
    e = _exp(BED, "price_mult", old=0.9, new=0.8)
    _put_ledger(root, [e])
    out = ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["kept"]
    assert _pol(root)["categories"][BED]["price_mult"] == 0.8
    end = _rows(root, "experiment_end")
    assert end[0]["status"] == "kept" and end[0]["verdict"]["decision"] == "better"


def _worse(root):
    _write_events(
        root, _hourly(S - 3 * H, 3, "BED1", 300_000) + _hourly(S, 3, "BED1", 100_000)
    )


@pytest.mark.parametrize(
    "section,key,old,new,setup,check",
    [
        (
            BED,
            "price_mult",
            0.9,
            0.8,
            lambda p: p["categories"][BED].update(price_mult=0.8),
            lambda p: p["categories"][BED]["price_mult"] == 0.9,
        ),
        (
            "defaults.goods",
            "fresh_discount.C",
            0.3,
            0.25,
            lambda p: p["defaults"]["goods"]["fresh_discount"].update(C=0.25),
            lambda p: p["defaults"]["goods"]["fresh_discount"] == {"B": 0.08, "C": 0.3},
        ),
        (
            "shop",
            "c_first_leaves.침대/매트리스",
            0,
            1,
            lambda p: p["shop"].update(c_first_leaves=["침대/매트리스"]),
            lambda p: p["shop"]["c_first_leaves"] == [],
        ),
        (
            "shop",
            "grade_floor_list.C",
            0.55,
            0.6,
            lambda p: p["shop"]["grade_floor_list"].update(C=0.6),
            lambda p: p["shop"]["grade_floor_list"] == {"C": 0.55},
        ),
    ],
)
def test_tick_reverted(root, a1, section, key, old, new, setup, check):
    _set_pol(root, setup)
    _worse(root)
    e = _exp(section, key, old=old, new=new)
    _put_ledger(root, [e])
    out = ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["reverted"], ex.load(root)
    pol = _pol(root)
    assert check(pol)
    a1.validate(pol)
    a1.load(os.path.join(root, "policy.json"))
    assert pe.effective(pol, section, key) == old
    edit = [r for r in _rows(root, "policy_edit") if r.get("by") == "kernel"]
    assert (
        len(edit) == 1
        and edit[0]["new"] == old
        and edit[0]["backup"].startswith("a2/policy_bak/")
    )
    assert _rows(root, "experiment_end")[0]["status"] == "reverted"


def test_tick_reverts_a_created_grade_floor(root, a1):
    res = pe.apply(
        root,
        [
            {
                "section": "shop",
                "key": "grade_floor_list.A",
                "value": 0.5,
                "reason": "근거",
            }
        ],
        _traj(root),
        S,
        categories=CATS,
        validate=a1.load,
    )
    assert res[0]["ok"] and res[0]["old"] is None
    e = ex.register(root, res[0], "A 등급 최저가", None, S, _traj(root))
    _worse(root)
    assert [
        x["status"] for x in ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    ] == ["reverted"]
    pol = _pol(root)
    assert pol["shop"]["grade_floor_list"] == {"C": 0.55}
    a1.validate(pol)
    # the only grade: the dict goes too
    _set_pol(root, lambda p: p["shop"].update(grade_floor_list={"A": 0.5}))
    r = pe.kernel_set(
        root,
        "shop",
        "grade_floor_list.A",
        None,
        _traj(root),
        S,
        "되돌림",
        CATS,
        a1.load,
    )
    assert r["ok"] and "grade_floor_list" not in _pol(root)["shop"]
    a1.validate(_pol(root))


def test_kernel_set_removes_a_created_category_key(root, a1):
    res = pe.apply(
        root,
        [{"section": SOFA, "key": "stock_target", "value": 10, "reason": "근거"}],
        _traj(root),
        S,
        categories=CATS,
        validate=a1.load,
    )
    assert res[0]["ok"] and res[0]["old"] is None
    r = pe.kernel_set(
        root, SOFA, "stock_target", None, _traj(root), S, "되돌림", CATS, a1.load
    )
    assert r["ok"] and SOFA not in _pol(root)["categories"]
    a1.validate(_pol(root))


def test_tick_voided_when_value_changed(root, a1):
    _set_pol(
        root, lambda p: p["categories"][BED].update(price_mult=0.7)
    )  # someone else moved it from 0.8
    _worse(root)
    e = _exp(BED, "price_mult", old=0.9, new=0.8)
    _put_ledger(root, [e])
    out = ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["voided"]
    assert _pol(root)["categories"][BED]["price_mult"] == 0.7
    assert not [r for r in _rows(root, "policy_edit") if r.get("by") == "kernel"]


def test_tick_not_due_does_nothing(root, a1):
    e = _exp(BED)
    _put_ledger(root, [e])
    assert ex.tick(root, e["eval_ts"] - 1, _traj(root), CATS, validate=a1.load) == []
    assert ex.load(root)[0]["status"] == "running"


@pytest.mark.parametrize("after_g,status", [(-50_000, "reverted"), (50_000, "kept")])
def test_tick_extends_then_point_estimate(root, a1, after_g, status):
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    # 2 sales before (0 gross), 2 after: thin both times
    _write_events(
        root,
        _sale(S - H, "BED1", 1e6, 1e6)
        + _sale(S - 2 * H, "BED1", 1e6, 1e6)
        + _sale(S + 600, "BED1", 1e6 + after_g, 1e6)
        + _sale(S + 4 * H, "BED1", 1e6 + after_g, 1e6),
    )
    e = _exp(BED, "price_mult", old=0.9, new=0.8)
    _put_ledger(root, [e])
    out = ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    x = ex.load(root)[0]
    assert (
        len(out) == 1
        and x["status"] == "running"
        and x["extended"]
        and x["window_h"] == 6
    )
    assert x["eval_ts"] == S + 8 * H and x["first_verdict"]["decision"] == "thin"
    assert _rows(root, "experiment_extend")[0]["window_h"] == 6
    out = ex.tick(root, x["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    x = ex.load(root)[0]
    assert (
        x["status"] == status
        and x["verdict"]["final"] == "point_estimate"
        and x["verdict"]["n_after"] == 2
    )
    assert _pol(root)["categories"][BED]["price_mult"] == (
        0.9 if status == "reverted" else 0.8
    )


def test_extension_at_max_window_keeps_8(root, a1):
    e = _exp(BED, "price_mult", old=0.9, new=0.9, window_h=8)
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.9))
    _put_ledger(root, [e])
    ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    x = ex.load(root)[0]
    assert x["window_h"] == 8 and x["extended"] and x["status"] == "running"


def test_tick_zero_activity_kept(root, a1):
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    e = _exp(BED, "price_mult", old=0.9, new=0.8)
    _put_ledger(root, [e])
    ex.tick(root, _due(e), _traj(root), CATS, validate=a1.load)
    x = ex.load(root)[0]
    out = ex.tick(root, x["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    x = ex.load(root)[0]
    assert (
        len(out) == 1
        and x["status"] == "kept"
        and x["verdict"]["final"] == "no_activity"
    )


def test_tick_revert_failure_is_retried(root, a1):
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    _worse(root)
    e = _exp(BED, "price_mult", old=0.9, new=0.8)
    _put_ledger(root, [e])

    def bad(p):
        raise ValueError("disk full")

    t = _due(e)
    assert ex.tick(root, t, _traj(root), CATS, validate=bad) == []
    x = ex.load(root)[0]
    assert (
        x["status"] == "running"
        and "disk full" in x["last_error"]
        and x["last_try_ts"] == t
    )
    assert _pol(root)["categories"][BED]["price_mult"] == 0.8
    assert (
        ex.tick(root, t + 300, _traj(root), CATS, validate=a1.load) == []
    )  # inside the 10 min wait
    assert ex.load(root)[0]["status"] == "running"
    out = ex.tick(root, t + 601, _traj(root), CATS, validate=a1.load)
    assert [x["status"] for x in out] == ["reverted"] and "last_error" not in ex.load(
        root
    )[0]
    assert _pol(root)["categories"][BED]["price_mult"] == 0.9


def test_tick_never_raises_on_corrupt_entries(root, a1):
    _set_pol(root, lambda p: p["categories"][BED].update(price_mult=0.8))
    good = _exp(BED, "price_mult", old=0.9, new=0.8, eid=3)
    _put_ledger(
        root,
        [
            "junk",
            {"id": 1, "status": "running"},
            {"id": 2, "status": "running", "eval_ts": 0, "section": BED},
            good,
        ],
    )
    out = ex.tick(root, _due(good), _traj(root), CATS, validate=a1.load)
    rows = ex.load(root)
    assert rows[0] == "junk" and "last_error" in rows[1] and "last_error" in rows[2]
    assert out and out[0]["id"] == 3  # extended (no data), the good entry still ran
    with open(ex.ledger_path(root), "w") as f:
        f.write("{not json")
    assert ex.tick(root, S, _traj(root), CATS, validate=a1.load) == []
    assert ex.lines(root, S)[0].startswith("[실험] 진행 0건")


# --- briefing lines and summary


def test_lines_and_summary(root):
    v = ex.evaluate(None, _exp(BED), S + 6 * H, _data(3e5, 1e5))
    ended = [
        _exp(
            BED,
            "price_mult",
            0.9,
            0.8,
            status="reverted",
            eid=1,
            verdict=v,
            ended_ts=S + 5 * H,
        ),
        _exp(
            "defaults.food",
            "max_rounds",
            4,
            5,
            status="kept",
            eid=2,
            ended_ts=S + 6 * H,
            verdict=dict(
                v,
                decision="thin",
                reason="thin",
                final="point_estimate",
                effect=1000.0,
                se=None,
            ),
        ),
        _exp(
            "shop",
            "grade_floor_list.C",
            0.55,
            0.6,
            status="voided",
            eid=3,
            ended_ts=S + 6 * H,
            verdict={"decision": "voided", "reason": "value_changed"},
        ),
    ]
    run = _exp(
        "buyer.defaults.goods",
        "max_pay_frac",
        0.85,
        0.9,
        eid=4,
        started=S + 6 * H,
        expect="상한을 올리면 거래가 늘어 saved가 오른다" * 5,
    )
    _put_ledger(root, ended + [run])
    out = ex.lines(root, S + 7 * H)
    text = "\n".join(out)
    print(text)
    assert out[0] == "[실험] 진행 1건, 최근 판정 3건 중 유지 1, 되돌림 1"
    assert out[1].startswith(
        "  진행 #4 buyer.defaults.goods max_pay_frac 0.85→0.9, 판정 "
    )
    assert "가설: 상한을" in out[1] and len(out[1]) < 160
    assert any(
        line.startswith("  #1 price_mult 0.9→0.8 되돌림: 효과 -")
        and "se " in line
        and ", 순이익 0.9M/h→0.3M/h, 매출 " in line
        for line in out
    )
    assert any(
        line.startswith("  #2 max_rounds 4→5 유지") and "연장 후 점추정" in line
        for line in out
    )
    assert any(line.startswith("  #3 grade_floor_list.C 0.55→0.6 무효") for line in out)
    assert len(text) < 1200
    s = ex.summary(root, S + 7 * H)
    assert (
        s["counts"] == {"running": 1, "kept": 1, "reverted": 1, "voided": 1}
        and s["hit_rate"] == 0.5
    )
    assert ex.summary(root, S + 40 * H)["counts"]["kept"] == 0


# --- score proxy (rev_weight) and ledger helpers


def test_register_rev_weight_sets_the_metric(root):
    traj = _traj(root)
    r = lambda sec, key: {"section": sec, "key": key, "old": 1, "new": 2}
    a = ex.register(root, r(BED, "price_mult"), "", None, S, traj)
    assert (a["metric"], a["rev_weight"]) == ("net_profit_per_h", 0.0)
    b = ex.register(root, r(BED, "price_mult"), "", None, S, traj, rev_weight=0.12)
    assert (b["metric"], b["rev_weight"]) == ("score_proxy_per_h", 0.12)
    c_ = ex.register(root, r("buyer.defaults.food", "max_pay_frac"), "", None, S, traj, rev_weight=0.12)
    assert (c_["metric"], c_["rev_weight"]) == ("saved_gain_per_h", 0.0)
    assert _rows(root, "experiment_start")[1]["rev_weight"] == 0.12


def test_shop_metric_with_rev_weight(root):
    early = _sale(S + 10, "BED1", 1_500_000, 1_000_000, wh=6)
    _write_events(root, early + _sale(S + 20, "BED1", 1_400_000, 1_000_000) + _broke(S + 10 + H, early))
    data = ex.read_events(root, S - H, S + 4 * H, login=LOGIN)
    m0 = ex.shop_metric(data, "침대/매트리스", S, S + 2 * H)
    m = ex.shop_metric(data, "침대/매트리스", S, S + 2 * H, rev_weight=0.1)
    net, rev = (500_000 - 1_500_000 + 400_000) / 2, 2_900_000 / 2
    assert m0["value"] == pytest.approx(net) and m0["net"] == pytest.approx(net)
    assert m["net"] == pytest.approx(net) and m["revenue"] == pytest.approx(rev)
    assert m["value"] == pytest.approx(net + 0.1 * rev)
    assert sum(m["buckets"]) / 2 == pytest.approx(m["value"])


def test_rev_weight_changes_the_decision():
    # same net per hour, twice the sales and revenue after
    d = _data(2e5, 1e5, n_before=3, n_after=6)
    plain = ex.evaluate(None, _exp(BED), S + 6 * H, d)
    assert plain["decision"] == "unclear" and plain["rev_weight"] == 0.0
    w = ex.evaluate(None, _exp(BED, metric="score_proxy_per_h", rev_weight=0.1), S + 6 * H, d)
    assert (w["decision"], w["metric"], w["rev_weight"]) == ("better", "score_proxy_per_h", 0.1)
    assert w["net_after"] == pytest.approx(plain["net_after"]) and w["net_before"] == pytest.approx(plain["net_before"])
    assert w["revenue_after"] > 1.8 * w["revenue_before"]
    assert w["after"] == pytest.approx(w["net_after"] + 0.1 * w["revenue_after"])
    assert w["before"] == pytest.approx(w["net_before"] + 0.1 * w["revenue_before"])


def test_load_and_running_tolerate_missing_and_corrupt(root):
    assert ex.load(root) == [] and ex.running(root) == []
    with open(ex.ledger_path(root), "w") as f:
        f.write("{not json")
    assert ex.load(root) == [] and ex.running(root) == []
    with open(ex.ledger_path(root), "w") as f:
        json.dump({"not": "a list"}, f)
    assert ex.load(root) == []
    _put_ledger(root, ["junk", _exp(BED, eid=1), _exp(SOFA, eid=2, status="kept")])
    assert len(ex.load(root)) == 3 and [e["id"] for e in ex.running(root)] == [1]
