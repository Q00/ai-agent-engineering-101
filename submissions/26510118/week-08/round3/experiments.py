"""The seller's first-ask experiment (Kiro 2.0 stage 2, 11/6, the person's call: "build stages 1 and 2 now; let the
analysis turn on what needs data once it is there").

A unit anchored to its SKU's market (pricing.plan) is first asked at the market + ASK_OVER. Which ASK_OVER earns most
is not something the last day can tell: the buyers' AIs changed with every patch. So each talk is given one of ARMS
by a hash of its task id (the same talk keeps its arm; the arms mix evenly across products, grades and hours), the
seller records the arm with the negotiation, and every 20 minutes run() scores each arm over WINDOW by its reward
per talk, a deal's lambda_book x (price - cost) + lambda_revenue x price (objective.json's prices of one more won in
each part; without them the margin alone). Talks are what the buyers bring and the shelf is refilled every 20
minutes, so a talk, not a unit, is the scarce thing an arm spends.

Until every arm has MIN_TALKS talks the weights are even; then the best arm gets BEST and each other REST, so the
others keep being measured (the buyers keep changing). analysis/experiments.json {"at", "on", "weights", "best",
"stats", "lambda"}; the seller reads it while it is FRESH, else asks at pricing.ASK_OVER as before. "on": false (a
person) stops it.
"""
import hashlib
import time

import common

ARMS = (-0.02, 0.0, 0.02, 0.04)
MIN_TALKS = 150
WINDOW = 24 * 3600
BEST, REST = 0.7, 0.1
FRESH = 3 * 3600


def path():
    return common.MARKET_DIR / "analysis" / "experiments.json"


def arm(task_id: str, now: float | None = None, state: dict | None = None) -> float | None:
    """The first ask over the market for this talk, or None (pricing.ASK_OVER) without a fresh experiments.json."""
    st = state if state is not None else common.read_json(path(), None)
    now = now or time.time()
    if not isinstance(st, dict) or not st.get("on", True) or now - (st.get("at") or 0) > FRESH:
        return None
    arms = sorted((float(a), float(w)) for a, w in (st.get("weights") or {}).items() if float(w) > 0)
    if not arms:
        return None
    h = int(hashlib.sha1(str(task_id).encode()).hexdigest()[:8], 16) / 0x100000000
    total, acc = sum(w for _, w in arms), 0.0
    for a, w in arms:
        acc += w / total
        if h < acc:
            return a
    return arms[-1][0]


def score(rows: list, lam: dict) -> dict:
    """{arm: {talks, deals, close, margin_per_talk, reward_per_talk}} over the anchored talks that carry an arm."""
    lb, lr = lam.get("book"), lam.get("revenue")
    if not lb and not lr:
        lb, lr = 1.0, 0.0
    out = {}
    for a in ARMS:
        rs = [r for r in rows if abs(float(r["arm"]) - a) < 1e-9]
        deals = [r for r in rs if r.get("outcome") == "deal" and r.get("price")]
        margin = sum(r["price"] - (r.get("cost") or 0) for r in deals)
        reward = sum(lb * (r["price"] - (r.get("cost") or 0)) + lr * r["price"] for r in deals)
        out[str(a)] = {"talks": len(rs), "deals": len(deals), "close": round(len(deals) / len(rs), 3) if rs else None,
                       "margin_per_talk": round(margin / len(rs)) if rs else None,
                       "reward_per_talk": reward / len(rs) if rs else None}
    return out


def run(now: float | None = None) -> str:
    now = now or time.time()
    old = common.read_json(path(), {}) or {}
    rows = [r for r in common.records("negotiations")
            if (r.get("ts") or 0) >= now - WINDOW and r.get("arm") is not None and r.get("anchored")]
    lam = (common.read_json(common.MARKET_DIR / "analysis" / "objective.json", {}) or {}).get("lambda") or {}
    stats = score(rows, lam)
    ready = all(s["talks"] >= MIN_TALKS for s in stats.values())
    if ready:
        best = max(stats, key=lambda a: stats[a]["reward_per_talk"])
        weights = {a: BEST if a == best else REST for a in stats}
    else:
        best, weights = None, {a: round(1 / len(ARMS), 4) for a in stats}
    common.write_json(path(), {"at": int(now), "on": old.get("on", True), "weights": weights, "best": best, "stats": stats,
                               "lambda": {"book": lam.get("book"), "revenue": lam.get("revenue")}})
    talks = ", ".join(f"{a}: {s['talks']} talks, close {s['close']}" for a, s in stats.items())
    return f"first-ask arms ({'best ' + best if best else 'even until ' + str(MIN_TALKS) + ' talks each'}): {talks}"


def brief(now: float) -> dict | None:
    e = common.read_json(path(), None)
    if not isinstance(e, dict):
        return None
    return {k: e.get(k) for k in ("at", "on", "best", "weights", "stats")}
