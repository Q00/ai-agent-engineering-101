"""Fixed-scenario exact tests, uncertainty, and multiplicity adjustment."""
from collections import defaultdict
from fractions import Fraction
from math import comb

from scipy.stats import binomtest
from statsmodels.stats.multitest import multipletests


def score(row):
    return int(str(row["correct"]) == "1")


def stratified_test(a, b):
    """Conditional permutation distribution of total successes in A (balanced strata)."""
    scenarios = sorted({r["scenario"] for r in a})
    assert scenarios == sorted({r["scenario"] for r in b})
    distribution = {0: Fraction(1)}
    observed, total_successes = 0, 0
    details = []
    intervals = []
    for scenario in scenarios:
        aa, bb = [r for r in a if r["scenario"] == scenario], [r for r in b if r["scenario"] == scenario]
        n, m = len(aa), len(bb)
        assert n == m > 0
        x, y = sum(map(score, aa)), sum(map(score, bb))
        k = x + y
        layer = {j: Fraction(comb(k, j) * comb(n + m - k, n - j), comb(n + m, n))
                 for j in range(max(0, n - (n + m - k)), min(n, k) + 1)}
        updated = defaultdict(Fraction)
        for u, p in distribution.items():
            for v, q in layer.items(): updated[u + v] += p * q
        distribution = dict(updated)
        observed += x; total_successes += k
        details.append({"scenario": scenario, "a_success": x, "a_n": n, "b_success": y, "b_n": m})
        # Simultaneous coverage for all 2*S stratum proportions, then linear bounds.
        confidence = 1 - 0.05 / (2 * len(scenarios))
        ca = binomtest(x, n).proportion_ci(confidence_level=confidence, method="exact")
        cb = binomtest(y, m).proportion_ci(confidence_level=confidence, method="exact")
        intervals.append((ca.low - cb.high, ca.high - cb.low))
    assert sum(distribution.values()) == 1
    threshold = abs(2 * observed - total_successes)
    p = sum(prob for value, prob in distribution.items() if abs(2 * value - total_successes) >= threshold)
    delta = 100 * (sum(map(score, a)) / len(a) - sum(map(score, b)) / len(b))
    return {"a_success": sum(map(score, a)), "b_success": sum(map(score, b)), "n_per_group": len(a),
            "difference_pp": delta, "p_raw": float(p), "p_fraction": str(p),
            "difference_ci95_conservative_pp": [float(100 * sum(t[i] for t in intervals) / len(intervals)) for i in (0, 1)],
            "by_scenario": details}


def adjust(tests):
    corrected = multipletests([t["p_raw"] for t in tests], alpha=0.05, method="holm")
    for test, reject, p in zip(tests, corrected[0], corrected[1]):
        test.update(p_holm=float(p), significant_holm=bool(reject))


def paired_test(before, after):
    aa = {(r["run"], r["scenario"]): r for r in before}
    bb = {(r["run"], r["scenario"]): r for r in after}
    assert aa.keys() == bb.keys()
    gain = sum(score(aa[k]) == 0 and score(bb[k]) == 1 for k in aa)
    loss = sum(score(aa[k]) == 1 and score(bb[k]) == 0 for k in aa)
    p = binomtest(gain, gain + loss, 0.5).pvalue if gain + loss else 1.0
    return {"before_success": sum(map(score, before)), "after_success": sum(map(score, after)),
            "gain": gain, "loss": loss, "difference_pp": 100 * (gain - loss) / len(before),
            "p_raw": float(p), "interpretation": "descriptive nested observation only; not a randomized treatment effect"}
