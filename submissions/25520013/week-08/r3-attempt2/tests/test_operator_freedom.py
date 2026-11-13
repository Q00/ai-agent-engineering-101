"""11-11 성수님 (the bitter lesson): the role operators own the strategy. One done.policy applies any number of items in
one write, each measured as the operator asks (test none, before_after, split; group judges several together); a
change voids the same role's running experiment it overwrites; done.next_wake_min sets the next wake; done.strategy is
a memo shown at the top, with no review wake. The kernel keeps only the safety lines (bounds, human-only keys, role
separation). Old ledger rows (HEAD's sequential and split, the uncommitted bet mode) are judged as before."""

import json
import math
import os
import shutil

import pytest
from kernel import analyst as an
from kernel import common as c
from kernel import experiments as ex
from kernel import operator as op
from kernel import policy_edit as pe
from tests import test_operator_roles as R
from tests.test_experiments import (  # noqa: F401 - root and a1 are fixtures
    BED,
    CATS,
    POLICY,
    H,
    S,
    _hourly,
    _pol,
    _rows,
    _traj,
    _write_events,
    a1,
    root,
)

NOW = R.NOW
FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "head_ledger_compat.json"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("A2_OPERATOR_COMPACT", "1")
    for k in (
        "A2_OPERATOR_INTERVAL_S",
        "A2_SHOP_LEAD_S",
        "A2_SPLIT",
        "A2_COMPACT_STEPS",
    ):
        monkeypatch.delenv(k, raising=False)
    root = R._root(tmp_path)

    def make(role):
        traj = R.Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role=role)
        return op.Operator(
            root, None, traj, harness_root=str(tmp_path / "noharness"), role=role
        )

    return root, make


def _it(key, value, section="defaults.goods", **kw):
    return dict({"section": section, "key": key, "value": value}, **kw)


def _apply(root, a1, items, now=S, role="shop", **kw):
    return ex.apply_policy(
        root,
        items,
        now,
        _traj(root),
        role=role,
        categories=CATS,
        validate=a1.load,
        **kw,
    )


# ------------------------------------------------------------ many items, one write, safety lines kept


def test_many_items_in_one_write_within_bounds(root, a1):
    items = [
        _it("price_mult", 0.6),  # 0.3 at once: no step limit
        _it("max_rounds", 10, "defaults.food"),
        _it("stock_target", 60, BED),
        _it("min_margin", 0.3),
        _it("fresh_discount.C", 0.5),
        _it("price_mult", 5.0, "defaults.food"),  # above the bound
        _it("price_mult", 1.0, "bounds"),  # human only
    ]
    out = _apply(root, a1, items)
    ok = [r["ok"] for r in out["results"]]
    assert ok == [True] * 5 + [False, False], out
    assert "사람만" in out["results"][6]["error"]
    pol = _pol(root)
    assert (
        pol["defaults"]["goods"]["price_mult"] == 0.6
        and pol["categories"][BED]["stock_target"] == 60
    )
    assert (
        pol["defaults"]["food"]["max_rounds"] == 10
        and pol["bounds"] == POLICY["bounds"]
    )
    edits = _rows(root, "policy_edit")
    assert (
        len(edits) == 5 and len({x["backup"] for x in edits}) == 1
    )  # one write, one backup
    assert {x["by"] for x in edits} == {"direct"} and ex.load(
        root
    ) == []  # test none: no experiment
    assert all(r.get("test") == "none" for r in out["results"][:5])


def test_role_operator_refuses_the_other_side_and_applies_the_rest(env):
    root, make = env
    r = make("buyer")._finish_compact(
        {
            "policy": [
                _it("open_frac", 0.4, "buyer.defaults.food"),
                _it("price_mult", 0.8),
                _it("max_pay_frac", 0.95, "buyer.defaults.goods"),
            ]
        },
        NOW,
    )
    assert [x["ok"] for x in r["policy"]] == [True, False, True]
    assert "가게 운영자(shop)" in r["policy"][1]["error"]
    pol = json.load(open(os.path.join(root, "policy.json"), encoding="utf-8"))
    assert (
        pol["buyer"]["defaults"]["food"]["open_frac"] == 0.4
        and pol["defaults"]["goods"]["price_mult"] == 0.9
    )


def test_duplicate_key_and_unknown_test_are_refused(root, a1):
    out = _apply(
        root,
        a1,
        [
            _it("price_mult", 0.8),
            _it("price_mult", 0.7),
            _it("min_margin", 0.2, test="ab"),
        ],
    )
    assert [r["ok"] for r in out["results"]] == [True, False, False]
    assert (
        "두 번" in out["results"][1]["error"] and "test는" in out["results"][2]["error"]
    )
    assert _pol(root)["defaults"]["goods"]["price_mult"] == 0.8


# ------------------------------------------------------------ none, before_after, split


def test_before_after_worse_is_measured_and_none_is_left_alone(root, a1):
    out = _apply(
        root,
        a1,
        [
            _it("price_mult", 0.8, BED, test="before_after", window_h=4),
            _it("max_rounds", 5, "defaults.food"),
        ],
    )
    r = out["results"]
    assert (
        r[0]["ok"] and r[0]["test"] == "before_after" and r[0]["mode"] == "sequential"
    )
    assert r[1]["ok"] and r[1]["test"] == "none" and "experiment" not in r[1]
    assert [e["key"] for e in ex.load(root)] == ["price_mult"]
    _write_events(
        root, _hourly(S - 4 * H, 4, "BED1", 300_000) + _hourly(S, 4, "BED1", 100_000)
    )
    e = ex.load(root)[0]
    assert [
        x["status"]
        for x in ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    ] == ["measured"]
    pol = _pol(root)
    assert (
        pol["categories"][BED]["price_mult"] == 0.8
        and pol["defaults"]["food"]["max_rounds"] == 5
    )


def test_split_only_for_sell_keys_and_when_the_seller_reads_arms(root, a1):
    out = _apply(
        root,
        a1,
        [
            _it("price_mult", 0.8, BED, test="split"),
            _it("stock_target", 48, BED, test="split"),
            _it("open_frac", 0.5, "buyer.defaults.food", test="split"),
        ],
        split_ok=True,
    )
    r = out["results"]
    assert r[0]["ok"] and r[0]["mode"] == "split" and r[0]["test"] == "split"
    assert not r[1]["ok"] and "split으로 잴 수 없음" in r[1]["error"]
    assert not r[2]["ok"]
    assert (
        _pol(root) == POLICY
    )  # the split leaves policy.json, the refused ones wrote nothing
    assert [a["id"] for a in ex.load_arms(root)] == [r[0]["experiment"]]
    off = _apply(
        root, a1, [_it("min_margin", 0.2, test="split")], now=S + 60, split_ok=False
    )
    assert (
        not off["results"][0]["ok"] and "지금 쓸 수 없음" in off["results"][0]["error"]
    )


# ------------------------------------------------------------ group


GROUP = [
    _it("price_mult", 0.6, BED, test="before_after", group="침대 공세"),
    _it("stock_target", 60, BED, test="before_after", group="침대 공세"),
    _it("max_rounds", 10, "defaults.food", test="before_after", group="침대 공세"),
]


def test_group_is_one_experiment_measured_together_when_worse(root, a1):
    out = _apply(root, a1, GROUP)
    applied = _pol(root)
    r = out["results"]
    assert all(x["ok"] and x["experiment"] == 1 and x["mode"] == "group" for x in r), r
    e = ex.load(root)[0]
    assert (
        e["mode"] == "group"
        and e["key"] == "침대 공세"
        and len(e["items"]) == 3
        and e["role"] == "shop"
    )
    assert e["window_h"] == ex.GROUP_WINDOW_H and e["scope"] == "shop:all"
    assert f"묶음 '침대 공세' 키 3개 (가게 전체 판매로 잼), 판정 {c.kst(e['eval_ts'], '%H:%M')}" in "\n".join(
        ex.lines(root, S)
    )
    wh = e["window_h"]
    _write_events(
        root, _hourly(S - wh * H, wh, "BED1", 300_000) + _hourly(S, wh, "BED1", 100_000)
    )
    assert [
        x["status"]
        for x in ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    ] == ["measured"]
    assert _pol(root) == applied
    assert not [x for x in _rows(root, "policy_edit") if x.get("by") == "kernel"]


def test_group_refused_member_refuses_only_itself(root, a1):
    bad = GROUP[:2] + [
        _it("max_rounds", 50, "defaults.food", test="before_after", group="침대 공세")
    ]
    out = _apply(root, a1, bad)  # 11-12: a refused member refuses only itself
    assert [x["ok"] for x in out["results"]] == [True, True, False]


def test_group_of_two_sides_is_refused_whole(root, a1):
    mixed = [
        GROUP[0],
        _it(
            "open_frac",
            0.5,
            "buyer.defaults.food",
            test="before_after",
            group="침대 공세",
        ),
    ]
    out = _apply(root, a1, mixed)
    assert [x["ok"] for x in out["results"]] == [False, False]
    assert _pol(root) == POLICY and ex.load(root) == []


def test_legacy_bet_row_is_measured_like_a_group(root, a1):
    _apply(root, a1, GROUP)
    applied = _pol(root)
    rows = ex.load(root)
    rows[0].update(mode="bet")  # an entry written by the 11-11 evening bet code
    ex.save(root, rows)
    e = rows[0]
    wh = e["window_h"]
    _write_events(
        root, _hourly(S - wh * H, wh, "BED1", 300_000) + _hourly(S, wh, "BED1", 100_000)
    )
    assert [
        x["status"]
        for x in ex.tick(root, e["eval_ts"] + 1, _traj(root), CATS, validate=a1.load)
    ] == ["measured"]
    assert _pol(root) == applied
    assert [
        x["id"] for x in ex.overlaps(root, BED, "price_mult")
    ] == []  # ended: overlaps nothing


# ------------------------------------------------------------ overwrite


def test_a_change_voids_the_same_roles_experiment_it_overwrites(env):
    root, make = env
    shop, buyer = make("shop"), make("buyer")
    r = shop._finish_compact(
        {"policy": [_it("price_mult", 0.85, test="before_after")]}, NOW
    )
    assert r["policy"][0]["ok"] and r["voided"] == []
    r = buyer._finish_compact(
        {"policy": [_it("open_frac", 0.5, "buyer.defaults.food", test="before_after")]},
        NOW + 60,
    )
    assert r["policy"][0]["ok"]
    r = shop._finish_compact(
        {"policy": [_it("price_mult", 0.8)]}, NOW + 120
    )  # test none on the running scope
    assert r["policy"][0]["ok"] and [v["id"] for v in r["voided"]] == [1]
    rows = {e["id"]: e for e in ex.load(root)}
    assert (
        rows[1]["status"] == "voided" and rows[1]["verdict"]["reason"] == "overwritten"
    )
    assert (
        rows[2]["status"] == "running"
    )  # the buyer's experiment is never touched by the shop
    assert (
        ex.void_overlaps(
            root, [("buyer.defaults.food", "open_frac")], "shop", NOW, shop.traj
        )
        == []
    )
    lines = op.done_lines({"policy": [_it("price_mult", 0.8)]}, r, NOW + 120)
    assert any("진행 중이던 실험 #1" in x and "무효로 끝남" in x for x in lines)
    assert any("적용됨 (실험 없음)" in x for x in lines)
    assert "운영자가 덮어씀" in "\n".join(ex.lines(root, NOW + 130))


def test_a_new_before_after_right_after_an_overwrite_names_the_mixed_window(root, a1):
    _apply(root, a1, [_it("price_mult", 0.8, BED, test="before_after")])
    out = _apply(
        root, a1, [_it("price_mult", 0.75, BED, test="before_after")], now=S + 600
    )
    assert [v["id"] for v in out["voided"]] == [1]
    e = ex.load(root)[1]
    assert e["before_mixed"] == [1] and e["before_from"] is None


# ------------------------------------------------------------ next wake


def test_next_wake_min_sets_the_next_wake(env):
    root, make = env
    o = make("buyer")
    o.terra = R.Script({"done": {"next_wake_min": 90, "note": "n"}})
    res = o.wake("주기 점검", NOW)
    assert res["next_wake"]["ok"] and o._state()["next_wake_ts"] == NOW + 90 * 60
    assert o.wake_reason(NOW + 1200) is None  # the 20 min default no longer applies
    why = o.wake_reason(NOW + 90 * 60)
    assert "네가 정한 다음 점검 시각" in why and "90분 뒤로 정함" in why
    o.terra = R.Script({"done": {}})
    o.wake(why, NOW + 90 * 60)
    st = o._state()
    assert "next_wake_ts" not in st  # used once
    assert "기본 시각" in o.wake_reason(NOW + 90 * 60 + op.ROLE_INTERVAL_S)
    p = o.terra.prompts[0]
    assert "- 다음 점검:" in p and "90분 뒤로 정함" in p


@pytest.mark.parametrize(
    "value", [0.2, "x", -3, 0, True, None, float("nan"), float("inf"), [5]]
)
def test_next_wake_min_floor_and_fallback(env, value):
    root, make = env
    o = make("shop")
    r = o._finish_compact({"next_wake_min": value}, NOW)["next_wake"]
    if value == 0.2:
        assert (
            r["ok"] and o._state()["next_wake_ts"] == NOW + op.POLL_S
        )  # the poll interval is the floor
    else:
        assert not r["ok"] and "next_wake_ts" not in o._state()


def test_next_wake_min_is_ignored_by_the_analyst_and_the_single_operator(env):
    root, make = env
    a = an.Analyst(
        root,
        None,
        R.Trajectory(os.path.join(root, "a2/trajectory.jsonl"), role="analyst"),
        harness_root=os.path.join(root, "noharness"),
    )
    a._question = {"id": 1}
    res = a._finish_compact(
        {"next_wake_min": 5, "strategy": "x", "answer": {"finding": ""}}, NOW
    )
    assert "next_wake" not in res and "strategy" not in res
    assert not os.path.exists(os.path.join(root, "a2/strategy_analyst.md"))
    assert "next_wake" not in make(None)._finish_compact({"next_wake_min": 5}, NOW)


# ------------------------------------------------------------ strategy memo, no review wake


def test_strategy_memo_is_saved_and_shown_at_the_top_with_no_review_wake(env):
    root, make = env
    for name in (
        "STRATEGY_INTERVAL_S",
        "STRATEGY_STEPS",
        "STRATEGY_RETRY_S",
        "STRATEGY_TASK",
    ):
        assert not hasattr(op, name)
    assert not hasattr(op.Operator, "_strategy_due")
    o = make("shop")
    memo = "가게: 침대 가격을 낮춰 매출 순위를 올림. 실패 숫자: 시간당 순이익 20% 감소"
    o.terra = R.Script({"done": {"strategy": memo, "note": "n"}})
    res = o.wake("주기 점검", NOW)
    assert (
        "전략"
        not in o.terra.prompts[0]
        .split("## 감사자가")[0]
        .split("# 가게 운영자")[1]
        .split("## 목표")[0]
    )
    assert res["strategy"]["ok"] and res["calls"] == 1
    wake = [r for r in R._rows(root) if r["kind"] == "operator_wake"][-1]
    assert wake["reason"] == "주기 점검"
    assert (
        open(o._strategy_path(), encoding="utf-8").read()
        == f"<!-- ts: {int(NOW)} -->\n{memo}"
    )
    o.terra = R.Script({"done": {}})
    o.wake("주기 점검", NOW + 60)
    p = o.terra.prompts[0]
    head = f"## 전략 메모 (네가 {c.kst(NOW, '%m-%d %H:%M')}에 씀)\n\n{memo}"
    assert head in p and p.index(head) < p.index("## 상태 카드")
    assert "- 전략 메모: 저장됨" in p


# ------------------------------------------------------------ no stated limits, bounded context


LIMIT_WORDS = (
    "3개까지",
    "개까지",
    "번까지",
    "자 안",
    "한 점검에",
    "20자 이상",
    "도구 단계",
    "모델 호출은",
)


def test_no_limits_are_stated_to_the_agents(root):
    for text in (
        op.role_schema("shop"),
        op.role_schema("buyer"),
        op.COMPACT_SCHEMA,
        an.SCHEMA,
    ):
        assert not any(w in text for w in LIMIT_WORDS), [
            w for w in LIMIT_WORDS if w in text
        ]
    assert "한 번에 바꿀 수 있는 폭" not in pe.view(
        root, ()
    ) and "(최소, 최대)" in pe.view(root, ())
    job = open(an.JOB, encoding="utf-8").read()
    assert not any(w in job for w in LIMIT_WORDS) and "일하는 순서" not in job
    assert op.COMPACT_STEPS == 60 and op.COMPACT_TOOLS == 12 and an._steps() == 60


def test_kept_and_observations_stay_bounded_over_a_long_wake():
    kept = [f"[단계 {i}] " + "결론 " * 400 for i in range(op.COMPACT_STEPS)]
    text = op._kept_text(kept)
    assert len(text) <= op.KEPT_FULL + op.KEPT_HEADS + 200
    assert (
        "[단계 59]" in text and "앞선 결론" in text
    )  # the newest in full, the oldest counted
    obs = {
        (i, "t"): (i, f"tool{i} {{}}", "x" * op.OBS_BUDGET)
        for i in range(op.COMPACT_STEPS * op.COMPACT_TOOLS)
    }
    shown, full = op._obs_text(obs)
    body = "\n\n".join(shown)
    assert (
        len(body)
        <= op.OBS_BUDGET + op.OBS_TRIM_BUDGET + op.OBS_TRIM + op.OBS_HEADS + 2_000
    )
    assert "더 오래된 관찰" in body and len(full) == 1
    done = op._last_done_text(["- 값 " + "x" * 100] * 500)
    assert len(done) <= op.LAST_DONE_CHARS + 100 and "줄 생략" in done
    assert op._last_done_text(["a", "b"]) == "a\nb"


# ------------------------------------------------------------ HEAD ledger compatibility


def test_head_ledger_ends_measured_on_the_same_numbers(tmp_path, a1, monkeypatch):
    """The fixture was made by HEAD's kernel/experiments.py (git archive; sequential and split entries, their files and
    HEAD's tick results). Since 11-12 the same running rows end on the same verdict numbers, but as "measured": the
    statuses HEAD wrote (reverted, dropped, kept) and its split "adopt" flag are gone, and policy.json never changes."""
    # written on the pre-3.6 saved scale: the share of list kept, s0 as recorded
    monkeypatch.setattr(ex, "COST_FRAC", {"food": 0.0, "goods": 0.0})
    monkeypatch.setattr(ex, "SAVED_V36_TS", 0)
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    r = str(tmp_path / "data")
    for rel, text in fx["files"].items():
        os.makedirs(os.path.dirname(os.path.join(r, rel)), exist_ok=True)
        with open(os.path.join(r, rel), "w", encoding="utf-8") as f:
            f.write(text)
    os.makedirs(os.path.join(r, "a2/board"), exist_ok=True)
    traj = _traj(r)
    pol0 = open(os.path.join(r, "policy.json"), "rb").read()
    rest = lambda e: {k: v for k, v in e.items() if k not in ("status", "verdict")}
    for t, want in zip(fx["ticks"], fx["after"]):
        ex.tick(r, t, traj, CATS, validate=a1.load)
        got = ex.load(r)
        assert [e["status"] for e in got] == [
            "running" if w["status"] == "running" else "measured" for w in want["ledger"]
        ]
        for g, w in zip(got, want["ledger"]):
            assert rest(g) == rest(w)
            assert {k: v for k, v in (g.get("verdict") or {}).items() if k != "note"} == {
                k: v for k, v in (w.get("verdict") or {}).items() if k != "adopt"
            }
        assert open(os.path.join(r, "policy.json"), "rb").read() == pol0
        assert (
            json.load(open(os.path.join(r, "policy_arms.json"), encoding="utf-8"))
            == want["arms"]
        )
    assert {e["mode"] for e in ex.load(r)} == {"sequential", "split"}
    assert "\n".join(ex.lines(r, fx["ticks"][-1]))  # renders


def test_old_state_files_without_the_new_fields(env):
    root, make = env
    o = make("shop")
    with open(o.state_path, "w", encoding="utf-8") as f:
        json.dump(
            {"last_wake_ts": NOW - 7200, "strategy_try_ts": NOW - 100, "last_rank": 3},
            f,
        )
    assert o.wake_reason(NOW) is None or isinstance(o.wake_reason(NOW), str)
    o.terra = R.Script({"done": {}})
    assert o.wake("주기 점검", NOW)["status"] == "done"
    assert math.isfinite(o._state()["last_wake_ts"])
    shutil.rmtree(os.path.join(root, "a2"), ignore_errors=True)
