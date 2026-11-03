"""v1.18 LongMemEval-V2-style evaluation of the shadow memory layers on five abilities (report 6.1):
python -m evals.memory_eval --synthetic --seed 7 [--assert-order] [--per-kind 12]

IE information extraction (one fact: last price, last category, last failure reason), MS multi-session reasoning
(aggregates across days: top categories, failures per shop, trades per pair), TR temporal reasoning (state as of t
via `at`, incl. favourite category before vs after the planted taste shift), KU knowledge update (reference price
after a price move, taste after a refund / a trajectory DELETE), AB abstention (unknown key or before the record).
A question scores 1 when the answer equals the TRUTH answer exactly (evals.truth, raw-event scan), else 0; the
ability score is the mean. Rows: full layers and each broken variant (evals.answer.VARIANTS).
--assert-order exits 1 unless full >= every variant on every ability and full > each variant on the ability it
breaks.
"""

import argparse
import sys

from evals import ABILITIES, synth
from evals.answer import VARIANTS, Answerer
from evals.truth import Truth


def evaluate(seed=7, per_kind=12, full="full"):
    """-> (scores {variant: {ability: score}}, info). `full` picks the layers scored as the full row (tests pass a
    broken one to see the order check fail)."""
    events, cat, meta = synth.make(seed)
    qs = synth.questions(events, cat, meta, seed, per_kind)
    T = Truth(events, cat)
    want = [T.answer(q) for q in qs]
    scores, wrong = {}, {}
    for name, impl in [("full", full)] + [(v, v) for v in VARIANTS]:
        A = Answerer(events, cat, impl)
        hit = {a: [] for a in ABILITIES}
        for q, w in zip(qs, want):
            ok = A.answer(q) == w
            hit[q["ability"]].append(ok)
            if not ok:
                wrong.setdefault(name, []).append(q)
        scores[name] = {a: sum(h) / len(h) if h else float("nan") for a, h in hit.items()}
    traj = {}
    for q, w in zip(qs, want):
        if q["tag"].startswith("trajectory"):
            traj.setdefault(q["args"]["buyer"], {})[q["tag"][-2:]] = w
    info = {
        "seed": seed,
        "events": len(events),
        "questions": len(qs),
        "per_ability": {a: sum(q["ability"] == a for q in qs) for a in ABILITIES},
        "shifted": sum(1 for v in traj.values() if v.get("t1") != v.get("t2")),
        "buyers": len(traj),
        "wrong": wrong,
    }
    return scores, info


def order_problems(scores):
    """[] when full >= every variant on every ability and full > each variant on the ability it breaks."""
    full, out = scores["full"], []
    for v, target in VARIANTS.items():
        for a in ABILITIES:
            if scores[v][a] > full[a]:
                out.append(f"{v} beats full on {a}: {scores[v][a]:.3f} > {full[a]:.3f}")
        if not scores[v][target] < full[target]:
            out.append(f"{v} does not lose on {target}: {scores[v][target]:.3f} vs full {full[target]:.3f}")
    return out


def table(scores):
    head = f"{'variant':<12} {'breaks':<6} " + " ".join(f"{a:>6}" for a in ABILITIES)
    rows = [head, "-" * len(head)]
    for v, s in scores.items():
        rows.append(f"{v:<12} {VARIANTS.get(v, '-'):<6} " + " ".join(f"{s[a]:>6.3f}" for a in ABILITIES))
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--synthetic", action="store_true", help="evaluate on the seeded synthetic ledger (required)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-kind", type=int, default=12, help="questions per kind and ability")
    ap.add_argument("--assert-order", action="store_true")
    a = ap.parse_args(argv)
    if not a.synthetic:
        ap.error("only --synthetic is supported (the truth side needs the planted ledger)")
    scores, info = evaluate(a.seed, a.per_kind)
    print(f"memory_eval seed={info['seed']} events={info['events']} questions={info['questions']} "
          + " ".join(f"{k}={n}" for k, n in info["per_ability"].items()))
    print(f"trajectory: favourite category differs between t1 and t2 for {info['shifted']}/{info['buyers']} buyers")
    print(table(scores))
    for q in info["wrong"].get("full", [])[:10]:
        print(f"full wrong: {q['ability']} {q['kind']} {q['tag']} {q['args']}")
    if a.assert_order:
        bad = order_problems(scores)
        print("assert-order: " + ("OK" if not bad else "FAIL"))
        for b in bad:
            print(f"  {b}")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
