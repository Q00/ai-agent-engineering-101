"""A/B judgement: difference-in-differences with a stratified bootstrap CI, then a preregistered verdict.

  r = evaluate(rows, min_n=10, seed=SEED, n_boot=N_BOOT)   # -> {point, lo, hi, n_A, n_B, cap_hit, verdict, ...}
  verdict(point, lo, hi, n_A, n_B, min_n, cap_hit=False)   # -> "compose" | "keep" | "rollback"

rows: dicts {"group": "A"|"B", "period": "before"|"after", "value": number} (e.g. judge profit per category-hour).
DiD = (mean B after - mean B before) - (mean A after - mean A before). The bootstrap resamples each of the four
(group, period) cells with replacement inside the cell (stratified), random.Random(seed), n_boot draws; the CI is
the percentile interval. n_X = min(#X before, #X after).
verdict: cap_hit -> rollback; any missing number or n_A / n_B < min_n -> keep (too few samples: no adoption);
lo > 0 -> compose (adopt B); hi < 0 -> rollback; else keep.
"""

import math
import random

SEED = 20261103
N_BOOT = 2000
CELLS = (("A", "before"), ("A", "after"), ("B", "before"), ("B", "after"))


def _cells(rows):
    c = {k: [] for k in CELLS}
    for r in rows:
        k = (r.get("group"), r.get("period"))
        v = r.get("value")
        if (
            k in c
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
        ):
            c[k].append(float(v))
    return c


def _did(c):
    m = {k: sum(v) / len(v) for k, v in c.items()}
    return (m["B", "after"] - m["B", "before"]) - (m["A", "after"] - m["A", "before"])


def _pct(xs, q):
    i = q * (len(xs) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def bootstrap_ci(rows, seed=SEED, n_boot=N_BOOT, alpha=0.05):
    """-> (point, lo, hi), or (None, None, None) when a cell is empty."""
    c = _cells(rows)
    if any(not v for v in c.values()):
        return None, None, None
    rng = random.Random(seed)
    stats = sorted(
        _did({k: rng.choices(v, k=len(v)) for k, v in c.items()}) for _ in range(n_boot)
    )
    return _did(c), _pct(stats, alpha / 2), _pct(stats, 1 - alpha / 2)


def verdict(point, lo, hi, n_A, n_B, min_n, cap_hit=False):
    if cap_hit:
        return "rollback"
    if point is None or lo is None or hi is None or n_A < min_n or n_B < min_n:
        return "keep"
    if lo > 0:
        return "compose"
    if hi < 0:
        return "rollback"
    return "keep"


def evaluate(rows, min_n, seed=SEED, n_boot=N_BOOT, alpha=0.05, cap_hit=False):
    rows = list(rows)
    c = _cells(rows)
    n_a = min(len(c["A", "before"]), len(c["A", "after"]))
    n_b = min(len(c["B", "before"]), len(c["B", "after"]))
    point, lo, hi = bootstrap_ci(rows, seed, n_boot, alpha)
    return {
        "point": point,
        "lo": lo,
        "hi": hi,
        "n_A": n_a,
        "n_B": n_b,
        "cap_hit": bool(cap_hit),
        "verdict": verdict(point, lo, hi, n_a, n_b, min_n, cap_hit),
        "min_n": min_n,
        "seed": seed,
        "n_boot": n_boot,
        "alpha": alpha,
    }
