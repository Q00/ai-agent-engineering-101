"""Agents decide all strategy: an experiment verdict never changes a policy value. It reports its
numbers and ends the experiment as "measured"; a split's arm ends and policy.json keeps the old value. Rows started
by the code before (running, or a group left "reverting") end the same way. Fixed clock values."""

import os

import pytest
from kernel import audit
from kernel import experiments as ex
from kernel import memfeed
from tests.test_experiments import BED, CATS, H, S, _rows, _traj
from tests.test_experiments import a1, root  # noqa: F401  (fixtures)
from tests.test_revert_key import BETTER, WORSE, _bytes, ap, force, it

CASES = {
    "before_after_worse": dict(items=[it(BED, "price_mult", 0.8, test="before_after")]),
    "group_worse": dict(items=[it(BED, "price_mult", 0.8, test="before_after", group="G"),
                               it("defaults.food", "max_rounds", 6, test="before_after", group="G")]),
    "split_better": dict(items=[it(BED, "price_mult", 0.8, test="split")], split_ok=True, verdict=BETTER),
    "split_worse": dict(items=[it(BED, "price_mult", 0.8, test="split")], split_ok=True),
}


@pytest.mark.parametrize("case", list(CASES))
def test_verdict_never_writes_policy(root, a1, monkeypatch, case):
    c = CASES[case]
    out = ap(root, a1, c["items"], split_ok=c.get("split_ok", False))
    assert all(r["ok"] for r in out["results"]), out
    before = _bytes(root)
    force(monkeypatch, {1: c.get("verdict", WORSE)})
    ended = ex.tick(root, S + 100 * H, _traj(root), CATS, validate=a1.load)
    assert [e["status"] for e in ended] == ["measured"]
    assert _bytes(root) == before  # byte-identical: the verdict wrote nothing
    e = ex.load(root)[0]
    assert e["status"] == "measured" and e["verdict"]["decision"] == c.get("verdict", WORSE)["decision"]
    assert not [r for r in _rows(root, "policy_edit") if r.get("by") == "kernel"]
    assert _rows(root, "experiment_end")[0]["status"] == "measured"
    if e["mode"] == "split":
        assert ex.load_arms(root) == [] and "적용되지 않음" in e["verdict"]["note"]
        assert "새 값은 네가 쓰지 않으면 적용 안 됨" in "\n".join(ex.lines(root, S + 100 * H))
    else:
        assert "그대로" in e["verdict"]["note"]


def test_legacy_running_row_ends_measured(root, a1, monkeypatch):
    """A running row as the code before 11-12 left it (no note, its revert retry pending as last_error)."""
    ap(root, a1, [it(BED, "price_mult", 0.8, test="before_after")])
    rows = ex.load(root)
    rows[0].update(last_error="RuntimeError: revert failed: check failed", fails=2, last_try_ts=S + 99 * H)
    ex.save(root, rows)
    before = _bytes(root)
    force(monkeypatch, {})
    ended = ex.tick(root, S + 100 * H, _traj(root), CATS, validate=a1.load)
    assert [e["status"] for e in ended] == ["measured"] and _bytes(root) == before
    assert "last_error" not in ex.load(root)[0]


def test_legacy_reverting_group_ends_measured_without_restoring_the_rest(root, a1, monkeypatch):
    ap(root, a1, [it(BED, "price_mult", 0.8, test="before_after", group="G"),
                  it("defaults.food", "max_rounds", 6, test="before_after", group="G")])
    rows = ex.load(root)
    v = dict(WORSE, final="decision")
    rows[0]["reverting"] = v  # the old code's pending restore: the first item was already put back
    rows[0]["items"][0]["restored"] = True
    rows[0].update(last_error="RuntimeError: revert failed: defaults.food max_rounds", fails=1)
    ex.save(root, rows)
    before = _bytes(root)
    monkeypatch.setattr(ex, "evaluate", lambda *a, **k: pytest.fail("a reverting row keeps its verdict"))
    ended = ex.tick(root, S + 100 * H, _traj(root), CATS, validate=a1.load)
    e = ex.load(root)[0]
    assert [x["status"] for x in ended] == ["measured"] and _bytes(root) == before
    assert "reverting" not in e and e["verdict"]["restored"] == [f"{BED} price_mult"]
    assert e["verdict"]["decision"] == "worse" and "되돌린 값 그대로" in e["verdict"]["note"]


def test_finished_old_rows_are_left_as_they_are(root, a1):
    old = [{"id": i, "status": s, "section": BED, "key": "price_mult", "old": 0.9, "new": 0.8, "ended_ts": S,
            "verdict": {}} for i, s in enumerate(("kept", "reverted", "adopted", "dropped", "voided"), 1)]
    ex.save(root, old)
    assert ex.tick(root, S + 100 * H, _traj(root), CATS, validate=a1.load) == []
    assert ex.load(root) == old


def test_measured_rows_in_lines_summary_audit_and_memory(root):
    base = {"section": BED, "key": "price_mult", "old": 0.9, "new": 0.8, "side": "shop", "scope": "침대/매트리스",
            "started_ts": S - 5 * H, "ended_ts": S, "status": "measured"}
    seq_v = {"decision": "worse", "reason": "clear", "final": "decision", "before_adj": 900_000.0, "after": 300_000.0,
             "effect": -600_000.0, "se": 27_386.0, "n_before": 9, "n_after": 9, "note": ex.VALUE_STAYS}
    split_v = {"decision": "better", "reason": "clear", "final": "decision", "a": 800_000.0, "b": 920_000.0,
               "effect": 120_000.0, "se": 50_000.0, "n_a": 31, "n_b": 28}
    ex.save(root, [dict(base, id=1, mode="sequential", verdict=seq_v),
                   dict(base, id=2, mode="split", verdict=split_v),
                   dict(base, id=3, mode="sequential", verdict=dict(seq_v, decision="unclear", reason="noise"))])
    out = ex.lines(root, S)
    assert out[0] == "[실험] 진행 0건, 최근 판정 3건 중 판정만 (값 그대로) 3"
    assert any(x.startswith("  #1 price_mult 0.9→0.8 판정만 (값 그대로) 나빠짐: 효과 -0.60M/h") for x in out)
    assert any(x.startswith("  #2 price_mult 0.9→0.8 판정만 (값 그대로) 좋아짐: 효과 +0.12M/h")
               and x.endswith("새 값은 네가 쓰지 않으면 적용 안 됨") for x in out)
    assert any(x.startswith("  #3 price_mult 0.9→0.8 판정만 (값 그대로): ") for x in out)
    s = ex.summary(root, S)
    assert s["counts"]["measured"] == 3 and s["hit_rate"] == 0.5  # better 1 of better or worse 2
    assert audit.report(root, S + 1)["experiments"]["measured"] == 3
    [(_, _, text)] = memfeed.sentence({"kind": "experiment_end", "status": "measured", "id": 1, "section": BED,
                                       "key": "price_mult", "old": 0.9, "new": 0.8, "verdict": seq_v})
    assert "판정 판정만 (값 그대로)" in text and "결과 worse" in text


def test_a_measured_split_is_gone_for_before_windows_a_measured_sequential_is_not():
    rows = [{"id": 1, "status": "measured", "mode": "split", "section": BED, "key": "price_mult", "ended_ts": S},
            {"id": 2, "status": "measured", "mode": "sequential", "section": BED, "key": "min_margin",
             "ended_ts": S}]
    assert ex._before(rows, [(BED, "price_mult")], S + 3 * H) == (S, [])
    assert ex._before(rows[1:], [(BED, "min_margin")], S + 3 * H) == (None, [])


def test_schema_says_the_kernel_never_reverts_or_adopts():
    from kernel import operator as op

    assert "되돌리거나 채택하지 않음" in op.COMPACT_SCHEMA
    assert "나빠지면 커널이 되돌림" not in op.COMPACT_SCHEMA and "채택하거나 버림" not in op.COMPACT_SCHEMA
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("OPERATOR_JOB_SHOP.md", "OPERATOR_JOB_BUYER.md"):
        assert "재서 숫자로 알려 주기만" in open(os.path.join(here, name), encoding="utf-8").read()


def test_job_docs_define_the_goal_as_final_rank_not_proof():
    """11-13: operators held values for hours waiting for causal certainty; the goal lines say what counts."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f in ("OPERATOR_JOB_SHOP.md", "OPERATOR_JOB_BUYER.md"):
        text = open(os.path.join(here, f), encoding="utf-8").read()
        assert "인과를 증명하는 것은 목표가 아니고" in text
        assert "그대로 두는 것도 결정임" in text
        assert "걸린 크기와 남은 시간에 비춰 네가 정함" in text
