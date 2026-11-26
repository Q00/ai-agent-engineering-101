"""Patch 3.6 (11/12 ~17:00 KST, the market's "last scoring change in Round 3"): a buyer's saved counts, per deal, the
share of the shop's MARGIN the buyer kept, (list - price) / (list - unit cost), at most 1 (a price under the cost: 1);
a deal that served nothing -1, a refunded one 0, and the 100-deal prior stays. Until then it was (list - price) / list.

The buyer never sees a unit's cost. The market draws it on arrival, by grade, as a share of list (agent.md): A 55-75%,
B 40-60%, C 25-45% (taken as uniform), with the supply's grade mix: food A 25%, B 50%, C 25%; goods A 70%, B 20%,
C 10%. So a price is judged by its EXPECTED margin share over that cost. Checked on 11/13 over our 14,077 purchases:
0.7239 against the board's 0.7309. Under it grade A keeps the most (48 h to 11/13: A 0.848, B 0.802, C 0.719 at the
prices we paid), the reverse of the old measure (A 0.309, B 0.395, C 0.464): its cost sits closest to the price.
"""
RANGE = {"A": (0.55, 0.75), "B": (0.40, 0.60), "C": (0.25, 0.45)}
MIX = {"food": {"A": 0.25, "B": 0.5, "C": 0.25}, "goods": {"A": 0.7, "B": 0.2, "C": 0.1}}
STEPS = 41               # points over a grade's cost range


def kept(share_paid: float, grade: str | None = None, kind: str = "goods") -> float:
    """The expected margin share kept at a price of share_paid of list: over one grade's cost range, or over the kind's
    supply mix when the grade is not known."""
    if grade in RANGE:
        lo, hi = RANGE[grade]
        tot = 0.0
        for i in range(STEPS):
            c = lo + (hi - lo) * i / (STEPS - 1)
            tot += 1.0 if share_paid <= c else (1 - share_paid) / (1 - c)
        return tot / STEPS
    return sum(w * kept(share_paid, g) for g, w in MIX.get(kind, MIX["goods"]).items())


def expected(share_paid: float, grade: str | None, kind: str, covered: bool = True, p: float = 0.0) -> float:
    """kept() with the breaks counted: goods break with p, a break under the warranty counts 0, without one -1."""
    k = kept(share_paid, grade, kind)
    if kind != "goods":
        return k
    return (1 - p) * k - (0.0 if covered else p)


def cap(k: float, grade: str | None = None, kind: str = "goods", covered: bool = True, p: float = 0.0) -> float:
    """The most to pay, as a share of list, for an expected margin share of k or more (0 when no price gives it)."""
    if expected(0.0, grade, kind, covered, p) < k:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if expected(mid, grade, kind, covered, p) >= k:
            lo = mid
        else:
            hi = mid
    return round(lo, 4)
