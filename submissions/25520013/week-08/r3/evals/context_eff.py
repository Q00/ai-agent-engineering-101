"""v1.18 context efficiency of the memory layers (report 6.2): python -m evals.context_eff --synthetic --seed 7

For each question of the evals.memory_eval set, compares what the full memory layers return (the layer payload as
compact JSON) with the raw ledger a no-memory agent would have to read: "raw" = every events.jsonl row in the
question's time scope (ts < t for as-of questions, t0 <= ts < t1 for windows), "grep" = only the rows of that scope
whose data mentions the question's buyer / shop / SKU (a lower bound for an agent that can filter before reading).
Sizes are UTF-8 bytes and a token estimate chars / 4; ratio = ledger / memory. Per ability and in total.
"""

import argparse
import json
import sys

from evals import ABILITIES, synth
from evals.answer import Answerer


def dumps(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)


def scope(q, events):
    a = q["args"]
    if "t0" in a:
        rows = [e for e in events if a["t0"] <= e["ts"] < a["t1"]]
    else:
        rows = [e for e in events if e["ts"] < a["t"]]
    keys = {a[k] for k in ("buyer", "shop", "sku") if k in a}
    grep = [e for e in rows if keys & {str(v) for v in _values(e["data"])}]
    return rows, grep


def _values(d):
    for v in d.values():
        if isinstance(v, dict):
            yield from _values(v)
        else:
            yield v


def measure(seed=7, per_kind=12):
    """-> {ability or "total": {n, mem_b, mem_tok, raw_b, raw_tok, grep_b, grep_tok}}."""
    events, cat, meta = synth.make(seed)
    qs = synth.questions(events, cat, meta, seed, per_kind)
    A = Answerer(events, cat)
    lines = {e["seq"]: dumps(e) + "\n" for e in events}
    out = {a: dict.fromkeys(("n", "mem_b", "mem_c", "raw_b", "raw_c", "grep_b", "grep_c"), 0) for a in ABILITIES}
    for q in qs:
        _, payload = A.ask(q)
        mem = dumps(payload)
        rows, grep = scope(q, events)
        raw, gr = "".join(lines[e["seq"]] for e in rows), "".join(lines[e["seq"]] for e in grep)
        o = out[q["ability"]]
        o["n"] += 1
        for k, s in (("mem", mem), ("raw", raw), ("grep", gr)):
            o[f"{k}_b"] += len(s.encode("utf-8"))
            o[f"{k}_c"] += len(s)
    out["total"] = {k: sum(o[k] for o in out.values()) for k in out["IE"]}
    for o in out.values():
        for k in ("mem", "raw", "grep"):
            o[f"{k}_tok"] = o.pop(f"{k}_c") / 4
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--synthetic", action="store_true", help="measure on the seeded synthetic ledger (required)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--per-kind", type=int, default=12)
    a = ap.parse_args(argv)
    if not a.synthetic:
        ap.error("only --synthetic is supported")
    m = measure(a.seed, a.per_kind)
    print(f"context_eff seed={a.seed} (tokens = chars/4, ratio = ledger / memory)")
    head = (f"{'ability':<8} {'n':>4} {'mem_B':>9} {'mem_tok':>9} {'raw_B':>11} {'raw_tok':>10} {'grep_B':>10} "
            f"{'grep_tok':>9} {'raw/mem':>9} {'grep/mem':>9}")
    print(head)
    print("-" * len(head))
    for k, o in m.items():
        print(f"{k:<8} {o['n']:>4} {o['mem_b']:>9} {o['mem_tok']:>9.0f} {o['raw_b']:>11} {o['raw_tok']:>10.0f} "
              f"{o['grep_b']:>10} {o['grep_tok']:>9.0f} {o['raw_b'] / o['mem_b']:>9.1f} "
              f"{o['grep_b'] / o['mem_b']:>9.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
