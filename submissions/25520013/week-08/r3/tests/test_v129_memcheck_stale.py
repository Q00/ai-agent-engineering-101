"""v1.29 (2026-11-05): memcheck leaves out the temporal question of an experiment whose recorded A value is not the
value of its registered A policy (A_version), and the meta-harness counts only memcheck rows of version
MEMCHECK_V. Live case: e0014 recorded juice breadth_qty A 2 and e0015 food max_pay_frac A 0.88 (the B values of
e0011 and e0012, which ended in the same tick at 02:14:51) while their A policy v16 held 3 and 0.85; from 03:26 every
hourly memcheck counted both as memory misses (accuracy 0.87-0.88), evidence the meta-harness would act on."""

import json

from icrl import loop, meta
from icrl import space as SP

from tests import test_icrl_memory as TM
from tests import test_icrl_meta as TT


def test_the_temporal_question_of_a_stale_a_record_is_left_out(tmp_path):
    sdir = tmp_path / "icrl"
    sdir.mkdir()
    pol = json.loads((TM.R3 / "policy.json").read_bytes())
    base_p2 = SP.get(pol, TM.P2)[0]
    ctx = TM.ctx_for(tmp_path, sdir, tmp_path / "nomem")
    SP.put(pol, TM.P1, 1.0)
    v1 = ctx.runs.register(json.dumps(pol).encode(), TM.T1 - 3600, "baseline")
    ok = dict(
        TM.end_row(1, TM.A1, "reject", TM.T1, 1.0, 1.03), A_version=v1
    )  # A as registered
    stale = dict(
        TM.end_row(2, TM.A1, "reject", TM.T2, 0.95, 1.0), A_version=v1
    )  # v1 holds 1.0, not 0.95
    no_version = TM.end_row(
        3, TM.A2, "keep_a", TM.T3, base_p2 + 1, base_p2 + 100000
    )  # cannot be checked
    TM.jl(sdir / "experiments.jsonl", [ok, stale, no_version])
    TM.jl(
        sdir / "lessons.jsonl",
        [
            TM.lesson(1, TM.A1, "reject", TM.T1),
            TM.lesson(2, TM.A1, "reject", TM.T2),
            TM.lesson(3, TM.A2, "keep_a", TM.T3),
        ],
    )
    qs = loop.memquestions(ctx)  # no list given: still left out
    asked = [q for k, q, _, _ in qs if k == "temporal"]
    assert asked == [
        f"value of {TM.P1} at {TM.T1 + 1}?",
        f"value of {TM.P2} at {TM.T3 + 1}?",
    ]
    row = loop.memcheck(ctx, {}, TM.T3 + 60)
    assert row["v"] == loop.MEMCHECK_V == meta.MEMCHECK_V == 2
    assert row["stale_a"] == ["e0002"]
    assert row["types"]["temporal"] == {"n": 2, "correct": 1, "acc": 0.5}
    # the row without A_version keeps its question, and its wrong record still shows as a miss
    assert row["misses"] == [["temporal", f"value of {TM.P2} at {TM.T3 + 1}?"]]


def test_an_unreadable_a_policy_keeps_the_question(tmp_path):
    sdir = tmp_path / "icrl"
    sdir.mkdir()
    pol = json.loads((TM.R3 / "policy.json").read_bytes())
    ctx = TM.ctx_for(tmp_path, sdir, tmp_path / "nomem")
    SP.put(pol, TM.P1, 1.0)
    ctx.runs.register(json.dumps(pol).encode(), TM.T1 - 3600, "baseline")
    rows = [
        dict(TM.end_row(1, TM.A1, "reject", TM.T1, 0.95, 1.0), A_version=99)
    ]  # v99 was never registered
    TM.jl(sdir / "experiments.jsonl", rows)
    TM.jl(sdir / "lessons.jsonl", [TM.lesson(1, TM.A1, "reject", TM.T1)])
    row = loop.memcheck(ctx, {}, TM.T1 + 60)
    assert row["stale_a"] == [] and row["types"]["temporal"]["n"] == 1


def test_meta_counts_only_memcheck_rows_of_the_current_version(tmp_path):
    ctx = TT.ctx_at(tmp_path)
    TT.init(ctx)
    live = [
        ["temporal", "value of buyer.defaults.food.max_pay_frac at 1793814560.7642179?"]
    ]
    TT.jl(  # the 03:26 and 04:27 rows on labpc: no "v", 2 misses of 15 and 17
        ctx.sdir / "memcheck.jsonl",
        [
            {
                "ts": TT.T0 + 600 + i * 1800,
                "n": 17,
                "acc": 0.882,
                "correct": 15,
                "misses": live,
            }
            for i in range(6)
        ],
    )
    q = TT.meta.lane_quality(ctx.sdir, "shop", TT.T0, 0, TT.T0 + meta.META_S)
    assert (
        q["samples"]["memcheck_rows"] == 0 and q["mem_acc"] is None and not q["enough"]
    )
    assert meta._misses(ctx.sdir, TT.T0) == []
    TT.memrows(ctx, TT.T0, 6)  # rows of the current version, all correct
    for ln in meta.SIDES:
        TT.cycles(ctx, TT.T0, ln, 6)
    q = meta.lane_quality(ctx.sdir, "shop", TT.T0, 0, TT.T0 + meta.META_S)
    assert q["samples"]["memcheck_rows"] == 6 and q["mem_acc"] == 1.0
    llm = TT.Fake(TT.cand())
    out = meta.step(ctx, TT.T0 + meta.META_S, llm=llm)
    assert out["status"] == "kept" and out["reason"] == "no defect" and not llm.prompts


def test_a_miss_in_a_current_row_is_still_evidence(tmp_path):
    ctx = TT.ctx_at(tmp_path)
    TT.init(ctx)
    TT.memrows(ctx, TT.T0, 6)
    TT.jl(
        ctx.sdir / "memcheck.jsonl",
        [
            {
                "ts": TT.T0 + 700,
                "v": meta.MEMCHECK_V,
                "n": 4,
                "acc": 0.75,
                "correct": 3,
                "misses": ["e0007 verdict?"],
            }
        ],
    )
    TT.cycles(ctx, TT.T0, "shop", 6)
    llm = TT.Fake(TT.cand())
    out = meta.step(ctx, TT.T0 + meta.META_S, llm=llm)
    assert out["status"] == "candidate" and "e0007 verdict" in llm.prompts[0]
