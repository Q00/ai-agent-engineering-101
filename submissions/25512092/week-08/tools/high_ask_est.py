"""Read-only estimate (11/09 ~23:30, person asked): for my high-price SKUs (unit cost >= qc.HIGH_MIN), what would a
first ask of market average x 0.95 do to the close rate, the price and the margin? Last 24 h.
  negotiation  one sales first_ask decision (logs/decisions.jsonl: unit, cost, avg, ask, rule, case)
  closed       my settle of that unit (market.db settles) after the ask and before the unit's next first ask (<= 15 min)
  model        pooled logistic close ~ ratio (ask / avg) + rule dummies + log(avg); per SKU the logit shift from its mean
               ratio to 0.95 is added to its own observed close rate; the price if closed keeps the SKU's final / ask
               ratio, floored at cost x 1.0
    python tools/high_ask_est.py [--hours 24] [--target 0.95]
"""
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts.qc import HIGH_MIN  # noqa: E402

ME = "choo-chu"
WIN_S = 900


def arg(name, d):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else d


def negotiations(t0, t1):
    out = []
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"first_ask"' not in ln:
                continue
            e = json.loads(ln)
            if not (t0 <= e["ts"] < t1) or e.get("action") != "first_ask":
                continue
            m = re.search(r"unit (\S+), cost (\d+), avg (\d+)", e.get("reason", ""))
            if not m or int(m.group(2)) < HIGH_MIN or not int(m.group(3)):
                continue
            rule = (re.search(r"rule (\w+)", e["reason"]) or [None, "-"])[1]
            case = (re.search(r"case (\w+)", e["reason"]) or [None, "-"])[1]
            out.append({"ts": e["ts"], "sku": e["target"], "unit": m.group(1).rstrip(","), "cost": int(m.group(2)),
                        "avg": int(m.group(3)), "ask": int(e["value"]), "rule": rule, "case": case})
    out.sort(key=lambda x: x["ts"])
    nxt = {}
    for n in reversed(out):                     # the same unit's next first ask
        n["next"] = nxt.get(n["unit"])
        nxt[n["unit"]] = n["ts"]
    return out


def mark_closed(N):
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        st = {}
        for unit, ts, amt in db.execute("SELECT unit, ts, amount FROM settles WHERE shop=? AND ts>=?",
                                        (ME, min(n["ts"] for n in N) - 60)):
            st.setdefault(unit, []).append((ts, amt))
        names = dict(db.execute("SELECT sku, name FROM my_stock"))
    finally:
        db.close()
    for n in N:
        end = min(n["ts"] + WIN_S, n["next"] or 1e18)
        hit = [(ts, a) for ts, a in st.get(n["unit"], []) if n["ts"] - 5 <= ts <= end]
        n["closed"] = bool(hit)
        n["price"] = hit[0][1] if hit else None
        n["ratio"] = n["ask"] / n["avg"]
    return names


def fit(N, rules):
    """Logistic regression by plain gradient steps (Newton would need numpy): x = [1, ratio, log avg, rule dummies]."""
    def x(n):
        return [1.0, n["ratio"], math.log(n["avg"]) - 12] + [1.0 if n["rule"] == r else 0.0 for r in rules]
    X, y = [x(n) for n in N], [1.0 if n["closed"] else 0.0 for n in N]
    w = [0.0] * len(X[0])
    for it in range(6000):
        g = [0.0] * len(w)
        for xi, yi in zip(X, y):
            z = sum(a * b for a, b in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            for k in range(len(w)):
                g[k] += (yi - p) * xi[k]
        lr = 2.0 / len(X)
        w = [a + lr * b for a, b in zip(w, g)]
    return w


def logit(p):
    p = min(0.98, max(0.02, p))
    return math.log(p / (1 - p))


def sig(z):
    return 1 / (1 + math.exp(-z))


def main():
    H, T = arg("--hours", 24), arg("--target", 0.95)
    now = time.time()
    N = negotiations(now - H * 3600, now)
    names = mark_closed(N)
    rules = sorted({n["rule"] for n in N} - {"volume"})
    w = fit(N, rules)
    b = w[1]
    print(f"high-price negotiations (cost >= {HIGH_MIN:,}), last {H:.0f} h: {len(N)}, closed {sum(n['closed'] for n in N)} "
          f"({sum(n['closed'] for n in N) / len(N):.1%})")
    print(f"logistic: ratio coef {b:+.2f} (odds x{math.exp(b * -0.05):.2f} per -0.05 of ratio), rules {rules} vs volume")
    print("bands (ask / avg): n, close rate, mean final / avg")
    for lo, hi in ((0, 0.93), (0.93, 0.97), (0.97, 1.0), (1.0, 1.03), (1.03, 1.07), (1.07, 1.12), (1.12, 9)):
        S = [n for n in N if lo <= n["ratio"] < hi]
        if S:
            c = [n for n in S if n["closed"]]
            print(f"  [{lo:.2f}, {hi:.2f}) n {len(S):4d}  close {len(c) / len(S):5.1%}  "
                  f"final/avg {sum(n['price'] / n['avg'] for n in c) / len(c) if c else 0:.3f}")
    by = defaultdict(list)
    for n in N:
        by[n["sku"]].append(n)
    # within-SKU check (SKU-mix confounding): SKUs with asks on both sides of 0.98, weighted by the smaller side
    num = den = 0.0
    for S in by.values():
        lo = [n for n in S if n["ratio"] < 0.98]
        hi = [n for n in S if n["ratio"] >= 0.98]
        if lo and hi:
            wt = min(len(lo), len(hi))
            num += wt * (sum(n["closed"] for n in lo) / len(lo) - sum(n["closed"] for n in hi) / len(hi))
            den += wt
    print(f"within-SKU: close rate (ask/avg < 0.98) minus (>= 0.98), weighted over SKUs with both: "
          f"{num / den * 100:+.1f}%p (weight {den:.0f})" if den else "within-SKU: no SKU with both")
    rows, tot = [], {"now_rev": 0, "new_rev": 0, "now_prof": 0, "new_prof": 0, "n": 0}
    for sku, S in by.items():
        k = len(S)
        c = [n for n in S if n["closed"]]
        rate = len(c) / k
        r_now = sum(n["ratio"] for n in S) / k
        avg = sum(n["avg"] for n in S) / k
        cost = sum(n["cost"] for n in S) / k
        rate_new = sig(logit(rate) + b * (T - r_now)) if r_now > T else rate
        fa = (sum(n["price"] / n["ask"] for n in c) / len(c)) if c else 1.0          # final / first ask when closed
        p_now = (sum(n["price"] for n in c) / len(c)) if c else r_now * avg * fa
        p_new = min(p_now, T * avg * fa) if r_now > T else p_now            # no cost floor: shows below-cost risk
        m_now, m_new = (p_now - cost) / p_now, (p_new - cost) / p_new
        rows.append((k, sku, names.get(sku, "")[:22], rate, r_now, rate_new, m_now, m_new, p_now, p_new, cost))
        tot["n"] += k
        tot["now_rev"] += k * rate * p_now
        tot["new_rev"] += k * rate_new * p_new
        tot["now_prof"] += k * rate * (p_now - cost)
        tot["new_prof"] += k * rate_new * (p_new - cost)
    rows.sort(reverse=True)
    print(f"\nper SKU (top 12 by negotiations): n, close now, ask/avg now, close at {T}, margin now -> at {T}, price now -> at {T}")
    for k, sku, nm, rate, r_now, rn, mn, mw, pn, pw, cost in rows[:12]:
        print(f"  {sku} {nm:22s} n {k:3d}  {rate:5.1%}  {r_now:.3f}  -> {rn:5.1%}  margin {mn:5.1%} -> {mw:5.1%}  "
              f"{pn:,.0f} -> {pw:,.0f} (cost {cost:,.0f})")
    h = H
    print(f"\npooled (all {len(rows)} high SKUs, same negotiation count): revenue/h {tot['now_rev'] / h:,.0f} -> "
          f"{tot['new_rev'] / h:,.0f}, gross profit/h {tot['now_prof'] / h:,.0f} -> {tot['new_prof'] / h:,.0f}")
    print("cases:", {c: sum(1 for n in N if n["case"] == c) for c in {n["case"] for n in N}},
          "rules:", {r: sum(1 for n in N if n["rule"] == r) for r in {n["rule"] for n in N}})


if __name__ == "__main__":
    main()
