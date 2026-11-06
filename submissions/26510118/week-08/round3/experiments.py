"""The seller's first-ask experiment (Kiro 2.0 stage 2, 11/6, the person's call: "build stages 1 and 2 now; let the
analysis turn on what needs data once it is there").

A unit anchored to its SKU's market (pricing.plan) is first asked at the market + ASK_OVER. Which ASK_OVER earns most
is not something the last day can tell: the buyers' AIs changed with every patch. So each talk is given one of ARMS
by a hash of its task id (the same talk keeps its arm; the arms mix evenly across products, grades and hours), the
seller records the arm with the negotiation, and every 20 minutes run() scores each arm over WINDOW by its reward
per talk: a deal's (price - cost + k x price) / its list price, k = lambda_revenue / lambda_book (objective.json's
prices of one more won in each part; without them the margin alone), 0 for a talk without a deal. Talks are what the
buyers bring and the shelf is refilled every 20 minutes, so a talk, not a unit, is the scarce thing an arm spends.
Per list price (11/6 15:40): in won the arms were judged by which happened to get the sofas. On 11/6 15:20 -0.03 led
in won (26,448 a talk against 17,252) and came third per list price (0.0258 against 0.0293 and 0.0386), with 58% of
its talks on products of 500,000 or more against 48-52% on the others.

Until every arm has MIN_TALKS talks, and until the best arm's reward is Z standard errors over the next one's, the
weights are even; then the best gets BEST and each other REST, so the others keep being measured (the buyers keep
changing). analysis/experiments.json {"at", "on", "weights", "best", "z", "stats", "lambda"}; the seller reads it
while it is FRESH, else asks at pricing.ASK_OVER as before. "on": false (a person) stops it.
"""
import hashlib
import statistics as st
import time

import common

ARMS = (-0.03, 0.0, 0.03, 0.06)      # (-0.02, 0, 0.02, 0.04) until 11/6 13:10 (the person's call: bolder, sooner)
MIN_TALKS = 100                      # 150 until then
WINDOW = 24 * 3600
BEST, REST = 0.8, round(0.2 / 3, 4)   # 0.7 / 0.1 until then
FRESH = 3 * 3600
Z = 2.0                              # 11/6 15:40: the best arm leads by this many standard errors, or the weights stay even


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
    """{arm: {talks, deals, close, margin_per_talk (won), reward_per_talk (per list price), se}} over the anchored talks
    that carry an arm and a list price."""
    lb, lr = lam.get("book"), lam.get("revenue")
    k = (lr or 0) / lb if lb else 0.0
    out = {}
    for a in ARMS:
        rs = [r for r in rows if abs(float(r["arm"]) - a) < 1e-9 and r.get("list_price")]
        deal = lambda r: r.get("outcome") == "deal" and r.get("price")
        vals = [(r["price"] - (r.get("cost") or 0) + k * r["price"]) / r["list_price"] if deal(r) else 0.0 for r in rs]
        deals = sum(1 for r in rs if deal(r))
        margin = sum(r["price"] - (r.get("cost") or 0) for r in rs if deal(r))
        out[str(a)] = {"talks": len(rs), "deals": deals, "close": round(deals / len(rs), 3) if rs else None,
                       "margin_per_talk": round(margin / len(rs)) if rs else None,
                       "reward_per_talk": round(sum(vals) / len(vals), 5) if vals else None,
                       "se": round(st.stdev(vals) / len(vals) ** 0.5, 5) if len(vals) >= 2 else None}
    return out


def leader(stats: dict) -> tuple[str | None, float | None]:
    """(the arm whose reward per talk leads the next by Z standard errors or more, or None; how many it leads by)."""
    order = sorted(stats, key=lambda a: -(stats[a]["reward_per_talk"] or 0))
    if len(order) < 2:
        return None, None
    b, n = stats[order[0]], stats[order[1]]
    gap = (b["reward_per_talk"] or 0) - (n["reward_per_talk"] or 0)
    spread = ((b["se"] or 0) ** 2 + (n["se"] or 0) ** 2) ** 0.5
    if not spread:
        return (order[0], None) if gap > 0 else (None, None)
    z = round(gap / spread, 2)
    return (order[0] if z >= Z else None), z


def run(now: float | None = None) -> str:
    now = now or time.time()
    old = common.read_json(path(), {}) or {}
    rows = [r for r in common.records("negotiations")
            if (r.get("ts") or 0) >= now - WINDOW and r.get("arm") is not None and r.get("anchored")]
    lam = (common.read_json(common.MARKET_DIR / "analysis" / "objective.json", {}) or {}).get("lambda") or {}
    stats = score(rows, lam)
    ready = all(s["talks"] >= MIN_TALKS for s in stats.values())
    best, z = leader(stats) if ready else (None, None)
    weights = {a: BEST if a == best else REST for a in stats} if best else {a: round(1 / len(ARMS), 4) for a in stats}
    common.write_json(path(), {"at": int(now), "on": old.get("on", True), "weights": weights, "best": best, "z": z,
                               "stats": stats, "lambda": {"book": lam.get("book"), "revenue": lam.get("revenue")}})
    talks = ", ".join(f"{a}: {s['talks']} talks, close {s['close']}, {s['reward_per_talk']}/talk" for a, s in stats.items())
    why = (f"best {best} by {z} SE" if best else f"even: no arm {Z} SE ahead yet (z {z})" if ready
           else f"even until {MIN_TALKS} talks each")
    return f"first-ask arms ({why}): {talks}"


# The buyer's haggling experiment (11/6 16:40, 몫 사냥꾼 ②): an order's arm by a hash of its id; scored over the
# orders where the choice arose (could_counter), each by (share of list kept - our saved now) if it closed, else 0:
# what the order adds to saved, times deals + 100.
HAGGLE_ARMS = ("accept", "counter")
HAGGLE_MIN = 100


def haggle_path():
    return common.MARKET_DIR / "analysis" / "haggle.json"


def haggle_arm(order_id: str, now: float | None = None, state: dict | None = None) -> str | None:
    """"accept" or "counter" for this order, or None (accept, as before) without a fresh haggle.json."""
    st_ = state if state is not None else common.read_json(haggle_path(), None)
    now = now or time.time()
    if not isinstance(st_, dict) or not st_.get("on", True) or now - (st_.get("at") or 0) > FRESH:
        return None
    arms = sorted((a, float(w)) for a, w in (st_.get("weights") or {}).items() if a in HAGGLE_ARMS and float(w) > 0)
    if not arms:
        return None
    h = int(hashlib.sha1(("haggle:" + str(order_id)).encode()).hexdigest()[:8], 16) / 0x100000000
    total, acc = sum(w for _, w in arms), 0.0
    for a, w in arms:
        acc += w / total
        if h < acc:
            return a
    return arms[-1][0]


def haggle_score(rows: list, saved: float, broken: set | None = None) -> dict:
    """broken: our orders whose goods broke (refunded under the warranty: a share of 0, 11/7)."""
    out, broken = {}, broken or set()
    keep = lambda r: 0.0 if r.get("order") in broken else 1 - r["price"] / r["list_price"]
    for a in HAGGLE_ARMS:
        rs = [r for r in rows if r.get("haggle") == a and r.get("could_counter") and r.get("list_price")]
        vals = [(keep(r) - saved) if r.get("deal") and r.get("price") else 0.0 for r in rs]
        deals = sum(1 for r in rs if r.get("deal") and r.get("price"))
        kept = [keep(r) for r in rs if r.get("deal") and r.get("price")]
        out[a] = {"talks": len(rs), "deals": deals, "close": round(deals / len(rs), 3) if rs else None,
                  "kept": round(sum(kept) / len(kept), 4) if kept else None,
                  "reward_per_talk": round(sum(vals) / len(vals), 5) if vals else None,
                  "se": round(st.stdev(vals) / len(vals) ** 0.5, 5) if len(vals) >= 2 else None}
    return out


def run_haggle(now: float | None = None) -> str:
    now = now or time.time()
    old = common.read_json(haggle_path(), {}) or {}
    rows = [r for r in common.records("orders") if (r.get("ts") or 0) >= now - WINDOW]
    sb = common.read_json(common.MARKET_DIR / "analysis" / "scoreboard.json", {}) or {}
    try:
        saved = float(sb["buyer"]["parts"]["saved"]["value"])
    except (KeyError, TypeError, ValueError):
        saved = 0.27
    stats = haggle_score(rows, saved, set(common.read_json(common.MARKET_DIR / "analysis" / "breakage.json", {})
                                          .get("our_broken_orders") or []))
    ready = all((s["talks"] or 0) >= HAGGLE_MIN for s in stats.values())
    best, z = leader(stats) if ready else (None, None)
    weights = {a: (BEST if a == best else round(1 - BEST, 4)) for a in stats} if best else {a: 0.5 for a in stats}
    common.write_json(haggle_path(), {"at": int(now), "on": old.get("on", True), "weights": weights, "best": best, "z": z,
                                      "saved": saved, "stats": stats})
    parts = ", ".join(f"{a}: {s['talks']} orders, close {s['close']}, kept {s['kept']}" for a, s in stats.items())
    why = f"best {best} by {z} SE" if best else (f"even: no arm {Z} SE ahead (z {z})" if ready else f"even until {HAGGLE_MIN} each")
    return f"haggling ({why}): {parts}"


def brief(now: float) -> dict | None:
    e = common.read_json(path(), None)
    if not isinstance(e, dict):
        return None
    return {k: e.get(k) for k in ("at", "on", "best", "z", "weights", "stats")}
