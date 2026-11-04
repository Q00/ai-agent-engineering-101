"""icrl meta-harness outer loop with a fake clock and a fake LLM (no network): 6 h gating, sample minimums,
per-side scoring over the side's lanes and side choice, per-side revert, atomic candidate writes, invalid /
oversized / PII replies, corrupted inputs and the write footprint. The "lane" fields of meta rows name the side."""

import json
from pathlib import Path
from types import SimpleNamespace

from icrl import meta

T0 = 1793800000.0


class Fake:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def complete(self, prompt, max_tokens=800):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"text": self.reply}


def ctx_at(tmp_path):
    sdir = tmp_path / "state"
    sdir.mkdir(parents=True)

    def no_llm():
        raise AssertionError("tests pass a fake llm")

    return SimpleNamespace(sdir=sdir, get_llms=no_llm)


def jl(p, rows):
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def memrows(ctx, t0, k, acc=1.0, n=4):
    jl(
        ctx.sdir / "memcheck.jsonl",
        [
            {"ts": t0 + 600 + i * 1800, "v": meta.MEMCHECK_V, "n": n, "acc": acc, "correct": acc * n}  # 6 rows in 3 h
            for i in range(k)
        ],
    )


def cycles(ctx, t0, lane, k, valid=True, hold=False, **extra):
    rows = []
    for i in range(k):
        r = {"ts": t0 + 900 + i * 1200, "ev": "cycle", "lane": lane, **extra}
        if hold:
            r.update(proposer="hold", approver="skipped")
        else:
            r.update(
                proposer="change",
                guards=[] if valid else ["shop.max_spend_per_run out of range"],
            )
        rows.append(r)
    jl(ctx.sdir / "log.jsonl", rows)


def cand(
    n=12,
    notes="raise prices only with N>=5 settles",
    recall="recent_first",
    features=None,
):
    return json.dumps(
        {
            "n_lessons": n,
            "recall": recall,
            "prompt_notes": notes,
            "features": features or ["time", "market"],
            "why": "w",
        }
    )


def harness(ctx):
    return json.loads((ctx.sdir / "harness.json").read_text())


def files(root):
    return sorted(
        str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()
    )


def init(ctx, llm=None):
    """First run: opens every lane's window at T0, nothing to judge."""
    out = meta.step(ctx, T0, llm=llm or Fake(cand()))
    assert out["status"] == "waiting"


def test_gating_6h(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    assert meta.step(ctx, T0 + meta.META_S - 1, llm=Fake(cand())) is None
    assert meta.step(ctx, T0 + meta.META_S, llm=Fake(cand())) is not None
    assert meta.step(ctx, T0 + meta.META_S + 600, llm=Fake(cand())) is None


def test_insufficient_samples_no_change(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 3)  # below MIN_MEM_ROWS
    for ln in meta.SIDES:
        cycles(ctx, T0, ln, 8)
    llm = Fake(cand())
    out = meta.step(ctx, T0 + meta.META_S, llm=llm)
    assert out["status"] == "waiting" and not llm.prompts
    assert not (ctx.sdir / "harness.json").exists()
    assert not (ctx.sdir / "meta" / "candidates.jsonl").exists()


def test_valid_candidate_only_chosen_lane_changes(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "price", 3)  # the shop side = the price and stock lanes together
    cycles(ctx, T0, "stock", 2)
    cycles(ctx, T0, "shop", 1, valid=False)  # an old shop-lane row counts as side shop
    cycles(ctx, T0, "forum", 6, valid=False)  # old forum-lane rows are ignored
    cycles(
        ctx, T0, "buyer", 6, hold=True
    )  # holds are not valid changes: 0.5 * 1 + 0.5 * 0, lowest score
    llm = Fake(cand(features=["time", "market", "no_such_feature"]))
    out = meta.step(ctx, T0 + meta.META_S, llm=llm)
    assert out["status"] == "candidate" and out["lane"] == "buyer"
    assert "GUARD_REASONS" in llm.prompts[0] and "'buyer'" in llm.prompts[0]
    h = harness(ctx)
    assert h["version"] == 1 and set(h["lanes"]) == {"shop", "buyer"}
    assert h["lanes"]["buyer"] == {
        "n_lessons": 12,
        "recall": "recent_first",
        "prompt_notes": "raise prices only with N>=5 settles",
        "features": ["time", "market"],
    }
    assert h["lanes"]["shop"] == meta.DEFAULT_LANE
    c = meta._rows(ctx.sdir / "meta" / "candidates.jsonl")
    assert {r["lane"] for r in c} == set(meta.SIDES) and all(
        r["version"] == 0 for r in c
    )
    by = {r["lane"]: r for r in c}
    assert (
        by["shop"]["samples"]["decisions"] == 6
        and by["shop"]["validity"] == 0.8333
        and by["buyer"]["hold_rate"] == 1.0
    )
    assert not list((ctx.sdir).glob(".*.tmp"))  # atomic: temp renamed away


def test_tie_rotates_lanes(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 30)
    for ln in meta.SIDES:
        cycles(ctx, T0, ln, 30)
    t, seen = T0, []
    for _ in range(3):
        t += meta.META_S
        out = meta.step(ctx, t, llm=Fake(cand(n=10 + len(seen))))
        seen.append(out["lane"])
    assert seen == ["shop", "buyer", "shop"]


def test_lane_without_samples_is_skipped(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "shop", 6, valid=False)  # worst, but only 3 decisions below
    cycles(ctx, T0, "buyer", 6)
    jl(
        ctx.sdir / "log.jsonl",
        [{"ts": T0 + 100, "ev": "cycle", "proposer": "change", "guards": []}],
    )  # no lane
    out = meta.step(ctx, T0 + meta.META_S, llm=Fake(cand()))
    assert out["lane"] == "shop"
    ctx2 = ctx_at(tmp_path / "b")
    init(ctx2)
    memrows(ctx2, T0, 6)
    cycles(ctx2, T0, "shop", 3, valid=False)  # below MIN_DECISIONS
    cycles(ctx2, T0, "buyer", 6)
    cycles(ctx2, T0, "buyer", 1, valid=False)  # v1.25: one guard reason = evidence, so the LLM is asked
    out = meta.step(ctx2, T0 + meta.META_S, llm=Fake(cand()))
    assert out["lane"] == "buyer" and out["lanes"]["shop"]["enough"] is False
    assert harness(ctx2)["lanes"]["shop"] == meta.DEFAULT_LANE


def test_worse_than_best_reverts_that_lane(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "shop", 6)
    cycles(ctx, T0, "shop", 1, valid=False)  # v0 shop 0.5 + 0.5 * 6/7 # v1.25: one guard reason = evidence, so the LLM is asked
    t1 = T0 + meta.META_S
    out = meta.step(ctx, t1, llm=Fake(cand(n=7)))
    assert (
        out["status"] == "candidate"
        and out["lane"] == "shop"
        and harness(ctx)["version"] == 1
    )
    memrows(ctx, t1, 6)
    cycles(
        ctx, t1, "shop", 8, valid=False, harness_version=1
    )  # v1 shop: 0.5 * 1 + 0.5 * 0
    cycles(
        ctx, t1, "shop", 4, harness_version=0
    )  # older version rows are not v1's evidence
    cycles(ctx, t1, "buyer", 8)
    llm = Fake(cand(n=30))
    out = meta.step(ctx, t1 + meta.META_S, llm=llm)
    assert out["status"] == "revert" and out["lane"] == "shop" and not llm.prompts
    h = harness(ctx)
    assert h["version"] == 2 and h["note"] == "revert shop to v0"
    assert (
        h["lanes"]["shop"] == meta.DEFAULT_LANE
        and h["lanes"]["buyer"] == meta.DEFAULT_LANE
    )
    assert out["best"]["margin"] >= meta.MARGIN_MIN


def test_invalid_oversized_pii_and_failing_llm(tmp_path):
    bad = [
        "not json at all",
        cand(n=99),
        cand(n=4),
        cand(recall="random"),
        cand(notes="x" * 601),
        cand(features=["nope"]),
        json.dumps({"recall": "graph_first"}),
        RuntimeError("llm off"),
        cand(
            n=20, notes="", recall="graph_first", features=list(meta.FEATURES)
        ),  # same as current
    ]
    for i, reply in enumerate(bad):
        ctx = ctx_at(tmp_path / str(i))
        init(ctx)
        memrows(ctx, T0, 6)
        cycles(ctx, T0, "shop", 6)
        cycles(ctx, T0, "shop", 1, valid=False)  # v1.25: one guard reason = evidence, so the LLM is asked
        out = meta.step(ctx, T0 + meta.META_S, llm=Fake(reply))
        assert out["status"] == "kept", (i, out)
        assert not (ctx.sdir / "harness.json").exists()
    ctx = ctx_at(tmp_path / "pii")
    init(ctx)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "shop", 6)
    cycles(ctx, T0, "shop", 1, valid=False)  # v1.25: one guard reason = evidence, so the LLM is asked
    out = meta.step(
        ctx,
        T0 + meta.META_S,
        llm=Fake(cand(notes="call 010-1234-5678 or kim@example.com")),
    )
    notes = harness(ctx)["lanes"]["shop"]["prompt_notes"]
    assert (
        "kim@example.com" not in notes
        and "010-1234-5678" not in notes
        and "[EMAIL]" in notes
    )
    llm_rows = meta._rows(ctx.sdir / "meta" / "llm.jsonl")
    assert (
        llm_rows
        and llm_rows[-1]["role"] == "meta"
        and "kim@" not in json.dumps(llm_rows)
    )


def test_never_raises_on_corrupted_inputs(tmp_path):
    ctx = ctx_at(tmp_path)
    (ctx.sdir / "harness.json").write_text("{garbage")
    jl(
        ctx.sdir / "memcheck.jsonl",
        ["{bad", "[1,2]", json.dumps({"ts": "x", "acc": "y", "n": None})],
    )
    assert (
        meta.step(ctx, T0, llm=Fake(cand()))["status"] == "waiting"
    )  # no log.jsonl at all
    memrows(ctx, T0, 6)
    jl(
        ctx.sdir / "log.jsonl",
        [
            "nope",
            json.dumps(
                {
                    "ev": "cycle",
                    "lane": "shop",
                    "ts": T0 + 5,
                    "proposer": "change",
                    "guards": "oops",
                }
            ),
        ],
    )
    cycles(ctx, T0, "shop", 6)
    (ctx.sdir / "meta" / "candidates.jsonl").write_text("garbage\n{\n")
    out = meta.step(ctx, T0 + meta.META_S, llm=Fake(cand()))
    assert out["status"] == "candidate" and harness(ctx)["version"] == 1
    assert out["lanes"]["shop"]["samples"]["decisions"] == 7 and out["lanes"]["shop"][
        "validity"
    ] == round(6 / 7, 4)
    (ctx.sdir / "meta" / "state.json").write_text("[]")
    assert meta.step(ctx, T0 + 2 * meta.META_S, llm=Fake(cand())) is not None
    bad_ctx = SimpleNamespace(sdir=tmp_path / "file")
    (tmp_path / "file").write_text("not a dir")
    assert meta.step(bad_ctx, T0)["status"] == "error"


def test_writes_only_meta_and_harness(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "shop", 6)
    cycles(ctx, T0, "shop", 1, valid=False)  # v1.25: one guard reason = evidence, so the LLM is asked
    before = set(files(ctx.sdir))
    snap = {f: (ctx.sdir / f).read_bytes() for f in before}
    meta.step(ctx, T0 + meta.META_S, llm=Fake(cand()))
    meta.step(ctx, T0 + 2 * meta.META_S, llm=Fake(RuntimeError("x")))
    new = set(files(ctx.sdir)) - before
    assert all(f == "harness.json" or f.startswith("meta/") for f in new), new
    assert all(
        (ctx.sdir / f).read_bytes() == b
        for f, b in snap.items()
        if not f.startswith("meta/")
    )


def test_sides_are_judged_from_the_side_field_or_the_lane(tmp_path):
    """A cycle row's "side" wins over its lane; price and stock rows add up on the shop side, forum rows nowhere."""
    ctx = ctx_at(tmp_path)
    memrows(ctx, T0, 6)
    cycles(ctx, T0, "price", 2, side="shop")
    cycles(ctx, T0, "stock", 2, valid=False)
    cycles(ctx, T0, "forum", 5)
    cycles(ctx, T0, "buyer", 3)
    shop = meta.lane_quality(ctx.sdir, "shop", T0, 0, T0 + meta.META_S)
    buyer = meta.lane_quality(ctx.sdir, "buyer", T0, 0, T0 + meta.META_S)
    assert shop["samples"]["decisions"] == 4 and shop["validity"] == 0.5 and shop["enough"]
    assert buyer["samples"]["decisions"] == 3 and not buyer["enough"]  # sample floor unchanged
    assert meta.LANE_SIDE == {"price": "shop", "stock": "shop", "buyer": "buyer", "shop": "shop"}


def test_v125_no_defect_keeps_the_config_without_asking_the_llm(tmp_path):
    """v1.25: score at the ceiling, no memcheck miss and no guard reason -> kept, no LLM call, no harness.json."""
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    for ln in meta.SIDES:
        cycles(ctx, T0, ln, 6)
    llm = Fake(cand())
    out = meta.step(ctx, T0 + meta.META_S, llm=llm)
    assert out["status"] == "kept" and out["reason"] == "no defect" and not llm.prompts
    assert not (ctx.sdir / "harness.json").exists()


def test_v125_a_memcheck_miss_is_evidence_even_above_the_score_floor(tmp_path):
    ctx = ctx_at(tmp_path)
    init(ctx)
    memrows(ctx, T0, 6)
    jl(
        ctx.sdir / "memcheck.jsonl",
        [{"ts": T0 + 700, "v": meta.MEMCHECK_V, "n": 4, "acc": 0.75, "correct": 3, "misses": ["e0003 verdict?"]}],
    )
    cycles(ctx, T0, "shop", 6)
    llm = Fake(cand())
    out = meta.step(ctx, T0 + meta.META_S, llm=llm)
    assert out["lanes"]["shop"]["score"] >= 1 - meta.MARGIN_MIN  # 0.5 * 27/28 + 0.5 * 1
    assert out["status"] == "candidate" and "e0003 verdict" in llm.prompts[0]
