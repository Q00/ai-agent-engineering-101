"""Experiment statistics and the arm selector (DESIGN.md sec 6, brief items 5-6). Pure functions, no I/O.

Switchback: adjacent blocks form a pair, A/B order per pair from random.Random(f"{seed}:{pair}"); pair difference
= B - A; looks at 6, 8, 10, 12 pairs with a two-sided t CI at alpha 0.05/4 (Bonferroni over the 4 looks); adopt
when CI lo > 0, reject when CI hi < 0, keep A at 12 pairs without a conclusion. Re-test: 6 pairs, one look at 0.05.
Long block: adopt only when B beats the preceding A block and (when available) the same hours of the previous day.
Selector: Thompson sampling over arms with a Normal posterior on the standardized effect, prior N(m0, 1).
"""

import functools
import math
import random

LOOKS = (6, 8, 10, 12)
ALPHA = 0.05 / len(LOOKS)
RETEST_LOOKS = (6,)
RETEST_ALPHA = 0.05


def t_pdf(x, df):
    c = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    return math.exp(c - (df + 1) / 2 * math.log1p(x * x / df))


def t_cdf(x, df, n=2000):
    """Simpson's rule on [0, |x|]; accurate to ~1e-9 for the df and x used here."""
    a = abs(x)
    h = a / n
    s = (
        t_pdf(0, df)
        + t_pdf(a, df)
        + sum((4 if i % 2 else 2) * t_pdf(i * h, df) for i in range(1, n))
    )
    half = s * h / 3
    return 0.5 + half if x >= 0 else 0.5 - half


@functools.lru_cache(maxsize=256)
def t_ppf(p, df):
    lo, hi = 0.0, 100.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ci(diffs, alpha):
    n = len(diffs)
    m = sum(diffs) / n
    sd = (
        math.sqrt(sum((d - m) ** 2 for d in diffs) / (n - 1)) if n > 1 else float("inf")
    )
    half = t_ppf(1 - alpha / 2, n - 1) * sd / math.sqrt(n)
    return m, m - half, m + half, sd


def order(seed, pair):
    return "AB" if random.Random(f"{seed}:{pair}").random() < 0.5 else "BA"


def block_arm(seed, i):
    return order(seed, i // 2)[i % 2]


def pair_diffs(blocks, key="metric"):
    """blocks: [{i, arm, metric}] closed, in order -> [B - A] of key for every complete pair. v1.25: a pair with a
    block an outside policy edit fell into ("ext_edit") is left out, since its arms ran under different baselines.
    Blocks without the key (still open) are skipped."""
    by, cut = {}, set()
    for b in blocks:
        if key not in b:
            continue
        by.setdefault(b["i"] // 2, {})[b["arm"]] = b[key]
        if b.get("ext_edit"):
            cut.add(b["i"] // 2)
    return [p["B"] - p["A"] for k, p in sorted(by.items()) if "A" in p and "B" in p and k not in cut]


def switchback_verdict(diffs, retest=False):
    """-> (verdict or None to continue, stats dict). v1.27 (2026-11-05): at an interim look a mean B - A of zero or
    below ends the experiment as keep_a (futility, a non-binding stop: it never adopts, so the false-adopt rate stays).
    Seeded simulation, 20,000 identical paths per effect size, pair diffs N(delta, 1): adoption within 0.3 points of
    the rule without it at every delta (0.213 -> 0.210 at 0.5 SD, unchanged from 0.75 SD up), pairs used 11.9 -> 8.5
    with no effect and 11.4 -> 6.4 when B is 0.5 SD worse. Overnight 11-04/05 no experiment reached a verdict."""
    looks, alpha = (RETEST_LOOKS, RETEST_ALPHA) if retest else (LOOKS, ALPHA)
    n = len(diffs)
    if n not in looks:
        return None, {"pairs": n}
    m, lo, hi, sd = ci(diffs, alpha)
    st = {"pairs": n, "mean": m, "lo": lo, "hi": hi, "sd": sd, "alpha": alpha}
    if lo > 0:
        return "adopt", st
    if hi < 0:
        return "reject", st
    if n >= looks[-1]:
        return "keep_a", st
    if m <= 0:
        return "keep_a", dict(st, futility=f"futility: mean {m:.4g} <= 0 at {n} of {looks[-1]} pairs")
    return None, st


def long_verdict(b, a, prev=None):
    d1 = b - a
    d2 = None if prev is None else b - prev
    ok = d1 > 0 and (d2 is None or d2 > 0)
    return ("adopt" if ok else "reject"), {
        "diff_prev_block": d1,
        "diff_prev_day": d2,
        "evidence": "weak",
    }


def std_effect(verdict_stats, protocol, a_level=None):
    """-> (standardized effect d, n) for the posterior update."""
    if protocol == "switchback":
        sd = verdict_stats.get("sd") or 0
        return (verdict_stats["mean"] / sd if sd > 0 else 0.0), verdict_stats["pairs"]
    rel = verdict_stats["diff_prev_block"] / max(abs(a_level or 0), 1e-9)
    return max(-3.0, min(3.0, rel / 0.1)), 1


def posterior(post, arm, prior=0.0):
    m, prec = post.get(arm, (prior, 1.0))
    return m, prec


def update(post, arm, d, n, prior=0.0):
    m, prec = posterior(post, arm, prior)
    post[arm] = [(m * prec + d * n) / (prec + n), prec + n]
    return post[arm]


def thompson(arms, post, priors, seed, k=3):
    """arms: [arm id]; -> top-k [(arm, sampled value, posterior mean)] by a Normal posterior draw."""
    rng = random.Random(seed)
    out = []
    for a in arms:
        m, prec = posterior(post, a, priors.get(a, 0.0))
        out.append((a, rng.gauss(m, 1 / math.sqrt(prec)), m))
    out.sort(key=lambda x: -x[1])
    return out[:k]
