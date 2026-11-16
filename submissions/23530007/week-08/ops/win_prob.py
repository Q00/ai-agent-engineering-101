"""Chance of finishing 1st on the class total, by simulation (user 11-15: 'put the probability in the regular report,
computed the way you just did').

Every student's parts move from the live board at their recent pace, each part on the window that fits it:
  buyer saved / deals / posts   last 1.5 or 3 h, ONE window per student for all three (saved = sum / (deals + 100),
                                so the sum and the deal count must move on the same window)
  book / revenue                last 12 or 24 h (slow, cumulative)
  stars_rank                    last 3, 6 or 12 h, kept inside [now - .03, ceiling]; ceilings from the per-account
                                analysis of 11-15 (SungJinho 4.49, us 4.48), others now + .04
  forum                         last 6 or 12 h
each pace times a random 0.85-1.15. N runs; the class total is recomputed with student percentiles (ties half).

    python ops/win_prob.py [--n 2000] [--print]      writes .market/win_prob.json (skips if under 30 min old)
"""
import json
import math
import random
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
OUT = HERE / ".market" / "win_prob.json"
END = 1794927540
OURS, RIVAL = "yms979", "SungJinho"
CEIL = {"SungJinho": 4.49, "yms979": 4.48}
WT = {"book": .5, "revenue": .3, "stars": .2, "saved": .5, "deals": .1, "posts": .1}
MAX_AGE = 1800


def rows_at(d):
    P = [p for p in d["players"] if p.get("student")]
    sh = {s["name"]: s for s in d["shops"]}
    bu = {x["name"]: x for x in d["buyers"]}
    out = {}
    for p in P:
        s = sh.get((p.get("shop") or {}).get("name")) or {}
        b = bu.get((p.get("buyer") or {}).get("name")) or {}
        out[p["player"]] = {"book": s.get("book") or 0, "revenue": s.get("revenue") or 0,
                            "stars": s.get("stars_rank") or 0,
                            "S": (b.get("saved") or 0) * ((b.get("deals") or 0) + 100), "deals": b.get("deals") or 0,
                            "posts": b.get("posts") or 0, "forum": b.get("forum") or 0}
    return out


def cols(vals):
    return {"book": {k: v["book"] for k, v in vals.items()}, "revenue": {k: v["revenue"] for k, v in vals.items()},
            "stars": {k: round(v["stars"], 2) for k, v in vals.items()},
            "saved": {k: v["S"] / (v["deals"] + 100) for k, v in vals.items()},
            "deals": {k: v["deals"] for k, v in vals.items()}, "posts": {k: v["posts"] for k, v in vals.items()}}


def total(vals):
    n = len(vals)
    T = {k: .3 * v["forum"] for k, v in vals.items()}
    for f, col in cols(vals).items():
        for k, x in col.items():
            T[k] += WT[f] * (sum(1 for q in col.values() if q < x) + .5 * (sum(1 for q in col.values() if q == x) - 1)) / (n - 1)
    return T


def exact_stars(db, d, now, hours=(3, 6, 9, 12, 24)):
    """Hidden (unrounded) stars_rank of every student shop, split into its two moving parts (user 11-15: 'simulate
    with these things accounted for').

    Why: the board shows stars_rank to 2 decimals and ranks on that, so one rounding tick (SungJinho 4.42 -> 4.43, a
    real +.0024 in 3 h) was extrapolated as +.14 (P 72% -> 33%); and a step from one new 5-star account (jiseokkk
    +.016 between 6 and 4 h ago) was extrapolated as a 42 h slope. Here stars_rank = (sum of per-account means + 15) /
    (n + 5) is rebuilt from the ledger's verified reviews, and
      drift   the change of the accounts already present h hours ago (their means moving), per hour
      joins   accounts new to the shop in the last 12 / 24 h (a rate), and the pool of active accounts (reviewed any
              shop in 24 h) that have not reviewed it yet, each with its mean at all shops (its rubric: sohyunkim11 4.0)
    The rebuilt level is off from the board by a shop-specific constant (median -.01), bracketed by requiring
    round(exact_t + off, 2) == board_t at every board snapshot of the last 24 h."""
    from collections import defaultdict
    import statistics
    st = {(p.get("shop") or {}).get("name"): p["player"] for p in d["players"] if p.get("student")}
    url = {}
    for (x,) in db.execute("SELECT data FROM ledger WHERE kind='join'"):
        x = json.loads(x).get("data") or {}
        if x.get("name") in st and x.get("url"):
            url[x["url"].rstrip("/")] = st[x["name"]]
    snaps = []                                          # (ts, {player: board stars})
    for ts, x in db.execute("SELECT ts, data FROM board WHERE ts > ? ORDER BY ts", (now - 24 * 3600,)):
        x = json.loads(x)
        snaps.append((ts, {st[s_["name"]]: s_.get("stars_rank") for s_ in x["shops"] if s_["name"] in st}))
    snaps.append((now, {st[s_["name"]]: s_.get("stars_rank") for s_ in d["shops"] if s_["name"] in st}))
    hmarks = {now - h * 3600: h for h in hours}
    marks = sorted({ts for ts, _ in snaps} | set(hmarks))
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))     # player -> buyer -> [sum, n]
    first = defaultdict(dict)                                     # player -> buyer -> first review ts
    rub = defaultdict(lambda: [0.0, 0])                           # buyer -> stars at all shops, last 24 h
    ex = defaultdict(dict)                                        # player -> mark -> exact
    kept = {}                                                     # h -> {player: {buyer: (sum, n)}}
    mi = 0

    def V(A):
        return (sum(a[0] / a[1] for a in A.values()) + 15) / (len(A) + 5)

    def snap(t):
        for k, A in acc.items():
            ex[k][t] = V(A)
        if t in hmarks:
            kept[hmarks[t]] = {k: {b: tuple(a) for b, a in A.items()} for k, A in acc.items()}
    for ts, x in db.execute("SELECT ts, data FROM ledger WHERE kind='result' AND round=3 ORDER BY ts"):
        while mi < len(marks) and marks[mi] < ts:
            snap(marks[mi])
            mi += 1
        x = json.loads(x).get("data") or {}
        if x.get("stars") is None or not x.get("verified") or not x.get("receipt"):
            continue
        if ts > now - 24 * 3600:
            r = rub[x["buyer"]]
            r[0] += x["stars"]
            r[1] += 1
        k = url.get(str(x.get("shop")).rstrip("/"))
        if k:
            a = acc[k][x["buyer"]]
            a[0] += x["stars"]
            a[1] += 1
            first[k].setdefault(x["buyer"], ts)
    while mi < len(marks):
        snap(marks[mi])
        mi += 1
    rubric = {b: r[0] / r[1] for b, r in rub.items() if r[1] >= 5}
    out = {}
    for k, E in ex.items():
        lo, hi, diffs = -9.0, 9.0, []
        for ts, B in snaps:
            if B.get(k) and ts in E:
                lo, hi = max(lo, B[k] - 0.005 - E[ts]), min(hi, B[k] + 0.005 - E[ts])
                diffs.append(B[k] - E[ts])
        ok = lo <= hi + 1.5e-3                          # board snapshots lag the ledger by a few minutes
        off = (lo + hi) / 2 if ok else (statistics.median(diffs) if diffs else 0.0)
        A = acc[k]
        drift = {}
        for h in hours:
            old = kept.get(h, {}).get(k) or {}
            if old:
                drift[h] = (V({b: A[b] for b in old}) - V({b: list(v) for b, v in old.items()})) / h
        out[k] = {"now": E[now] + off, "at": {h: E[now - h * 3600] + off for h in hours}, "off": round(off, 4),
                  "fit": round(hi - lo, 4) if ok else None, "n": len(A), "sum": sum(a[0] / a[1] for a in A.values()),
                  "drift": drift,
                  "joins": {h: sum(1 for t in first[k].values() if t > now - h * 3600) / h for h in (12, 24)},
                  "pool": [rubric[b] for b in rubric if b not in A]}
    return out


def poisson(rnd, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rnd.gauss(lam, lam ** 0.5)))
    L, k_, p_ = math.exp(-lam), 0, 1.0
    while True:
        p_ *= rnd.random()
        if p_ < L:
            return k_
        k_ += 1


def rank(col, k):
    return 1 + sum(1 for q in col.values() if q > col[k])


def simulate(n=2000, seed=None):
    import board_api
    now = time.time()
    H = max(0.0, (END - now) / 3600)
    db = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
    d0 = board_api.fetch(timeout=20)
    R0 = rows_at(d0)
    EX = exact_stars(db, d0, now)
    for k, x in EX.items():
        # decay of the stars climb (a mean converges as its reviews pile up): tau from the slope over the last 3 h
        # against 3-9 h ago (window centres 4.5 h apart); no slowdown seen -> a slow 24-96 h decay drawn per run
        # (on the drift of the accounts present, not on the board level, which also jumps when an account joins)
        s1, s2 = x["drift"].get(3, 0), x["drift"].get(9, 0)
        s2 = (s2 * 9 - s1 * 3) / 6                      # drift 3-9 h ago
        x["tau"] = (min(max(4.5 / math.log(s2 / s1), 4.0), 200.0) if s1 > 0 and s2 > s1 * 1.05 else None)
    W = {h: rows_at(json.loads(db.execute("SELECT data FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                                          (now - h * 3600,)).fetchone()[0])) for h in (1.5, 3, 6, 12, 24)}
    T_ = total(R0)
    CONTENDERS = {k for k, x in T_.items() if k != OURS and T_[OURS] - x <= 0.10}
    C0 = cols(R0)
    lost, gaps = Counter(), []
    rnd = random.Random(seed)
    who, ev, margins = Counter(), Counter(), []
    cond = {}                           # 11-15 (user: steer toward the scenarios we win): event -> {on: [runs, wins]}
    from collections import defaultdict
    cond = defaultdict(lambda: {True: [0, 0], False: [0, 0]})
    for _ in range(n):
        end = {}
        pushed = False
        for k, v in R0.items():
            hb = rnd.choice((1.5, 3))
            m = rnd.uniform(0.85, 1.15)
            # a contender answers (user 11-15: 'the rival will plan for your 20-30%'): in 25% of runs each student
            # within .10 of our total runs its parts at its best recent pace, with no slowdown of its stars climb
            push = k in CONTENDERS and rnd.random() < 0.25
            pushed |= push
            if push:
                hb = max((1.5, 3), key=lambda h: (v["S"] - W[h][k]["S"]) / h if k in W[h] else 0)
            hs = {"book": rnd.choice((12, 24)), "revenue": rnd.choice((12, 24)), "stars": rnd.choice((3, 6, 12)),
                  "forum": rnd.choice((6, 12)), "S": hb, "deals": hb, "posts": hb}
            if push and k in EX:
                hs["stars"] = max((3, 6, 12), key=lambda h: EX[k]["drift"].get(h, 0))
            e = {}
            for f, h in hs.items():
                if f == "stars" and k in EX:
                    # hidden (unrounded) value and its slope on a 3/6/12 h window, the climb decaying with tau; the
                    # end value is rounded to 2 decimals in cols(), as the board ranks it (ties count half)
                    X = EX[k]
                    x0 = X["now"]
                    sl = X["drift"].get(h, 0) * rnd.uniform(0.85, 1.15)
                    # tau is a noisy fit: drawn 0.5-4x around it; no slowdown seen -> 24-96 h; a pushing rival: none
                    tau = (X["tau"] * rnd.uniform(0.5, 4)) if X["tau"] else rnd.uniform(24, 96)
                    x = x0 + (sl * H if push else sl * tau * (1 - math.exp(-H / tau)))
                    x = min(max(x, x0 - 0.03), max(CEIL.get(k, x0 + 0.04), x0))
                    # accounts that join: Poisson at the 12 or 24 h join rate (x1.5 for a pushing rival), drawn from
                    # the active accounts that have not reviewed this shop, each at its mean at all shops
                    lam = X["joins"][rnd.choice((12, 24))] * H * (1.5 if push else 1)
                    j = min(poisson(rnd, lam), len(X["pool"]))
                    if j:
                        nb = X["n"] + 5
                        x = (x * nb + sum(rnd.sample(X["pool"], j))) / (nb + j)
                    e[f] = x
                    continue
                if k not in W[h]:
                    e[f] = v[f]
                    continue
                sl = (v[f] - W[h][k][f]) / h * (m if f in ("S", "deals", "posts") else rnd.uniform(0.85, 1.15))
                x = v[f] + sl * H
                if f == "stars":
                    x = min(max(x, v["stars"] - 0.03), CEIL.get(k, v["stars"] + 0.04))
                elif f == "posts":
                    x = min(x, v["posts"] + 12 * H)
                elif f == "forum":
                    x = min(max(x, 0), 1)
                e[f] = x
            end[k] = e
        T = total(end)
        c = cols(end)
        who[max(T, key=T.get)] += 1
        win = max(T, key=T.get) == OURS
        margins.append(T[OURS] - max(v for kk, v in T.items() if kk != OURS))
        if not win:
            # 11-16 (user: 'what are the cases where we are not 1st?'): the rank moves, against now, that a lost run has
            # for us and the winner (stars/saved/... ranks, posts 1st), kept as a pattern and counted
            wnr = max(T, key=T.get)
            mv = []
            for f in ("book", "revenue", "stars", "saved", "deals", "posts"):
                for who_, tag in ((OURS, "us"), (wnr, "them")):
                    d_ = rank(C0[f], who_) - rank(c[f], who_)          # + = rank improved
                    if d_ and (tag == "them") == (d_ > 0):
                        mv.append(f"{tag} {f} {rank(C0[f], who_)}->{rank(c[f], who_)}")
            lost[(wnr, tuple(mv))] += 1
            gaps.append(T[wnr] - T[OURS])
        flags = {"book_lost": rank(c["book"], OURS) > 1,
                 "revenue_lost": rank(c["revenue"], OURS) > 2,
                 "rival_stars_above": c["stars"][RIVAL] > c["stars"][OURS],
                 "our_stars_top3": rank(c["stars"], OURS) <= 3,
                 "our_saved_top12": rank(c["saved"], OURS) <= 12,
                 "our_saved_top13": rank(c["saved"], OURS) <= 13,
                 "rival_saved_top10": rank(c["saved"], RIVAL) <= 10,
                 "our_deals_top8": rank(c["deals"], OURS) <= 8,
                 "our_posts_1st": rank(c["posts"], OURS) == 1,
                 "a_rival_pushes": pushed}
        for f_, on in flags.items():
            ev[f_] += on
            cond[f_][on][0] += 1
            cond[f_][on][1] += win
    margins.sort()
    T0 = total(R0)
    second = max((v for kk, v in T0.items() if kk != OURS))
    return {"t": now, "n": n, "hours_left": round(H, 1), "p_first": round(who[OURS] / n, 3),
            "winners": {k: round(v / n, 3) for k, v in who.most_common(4)},
            "margin": {"p10": round(margins[int(.1 * n)], 3), "p50": round(margins[n // 2], 3),
                       "p90": round(margins[int(.9 * n)], 3)},
            "gap_now": round(T0[OURS] - second, 3),
            "stars_hidden": {k: {"now": round(EX[k]["now"], 4), "3h": round(EX[k]["at"][3], 4),
                                 "12h": round(EX[k]["at"][12], 4), "tau": EX[k]["tau"] and round(EX[k]["tau"], 1),
                                 "fit": EX[k]["fit"], "drift_3h_per_h": round(EX[k]["drift"].get(3, 0), 5),
                                 "joins_24h_per_h": round(EX[k]["joins"][24], 3), "pool": len(EX[k]["pool"]),
                                 "pool_mean": round(sum(EX[k]["pool"]) / len(EX[k]["pool"]), 2) if EX[k]["pool"] else None}
                             for k in sorted(EX, key=lambda q: -EX[q]["now"])[:8] + [RIVAL] if k in EX},
            "inputs": {k: {**{f: round(R0[k][f], 4) for f in ("book", "revenue", "stars", "deals", "posts", "forum")},
                           "saved": round(R0[k]["S"] / (R0[k]["deals"] + 100), 4)} for k in (OURS, RIVAL)},
            "events": {k: round(v / n, 3) for k, v in ev.items()},
            # P(we finish 1st | the event happens) and | it does not: the levers that separate won and lost runs
            "lose_patterns": [{"winner": k[0], "moves": list(k[1]), "share_of_runs": round(v / n, 3)}
                              for k, v in lost.most_common(8)],
            "win_if": {k: {"yes": round(v[True][1] / v[True][0], 3) if v[True][0] else None,
                           "no": round(v[False][1] / v[False][0], 3) if v[False][0] else None,
                           "freq": round(v[True][0] / n, 3)} for k, v in cond.items()}}


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 2000
    old = {}
    try:
        old = json.loads(OUT.read_text())
        if time.time() - old.get("t", 0) < MAX_AGE and "--force" not in sys.argv:
            if "--print" in sys.argv:
                print(json.dumps(old, ensure_ascii=False, indent=1))
            return
    except (OSError, ValueError):
        pass
    out = simulate(n)
    # a jump of >= 10 points comes with the inputs that moved since the last run (user 11-15: a 72% -> 33% drop was a
    # rounding artefact and nobody could say why)
    try:
        if abs(out["p_first"] - old["p_first"]) >= 0.10 and old.get("inputs"):
            out["jump"] = {"from": old["p_first"], "to": out["p_first"],
                           "moved": {k: {f: [old["inputs"][k].get(f), v] for f, v in out["inputs"][k].items()
                                         if old["inputs"].get(k, {}).get(f) != v} for k in out["inputs"]},
                           "stars_hidden_before": old.get("stars_hidden"),
                           # 11-16: the input levels barely move between runs (posts 3289 -> 3295); what moves P is a
                           # pace, which shows as an event's frequency: keep the events that moved most
                           "events": sorted(([k, old.get("events", {}).get(k), v] for k, v in out["events"].items()
                                             if old.get("events", {}).get(k) is not None
                                             and abs(v - old["events"][k]) >= 0.05),
                                            key=lambda e: -abs(e[2] - e[1]))[:3]}
    except (NameError, KeyError, TypeError):
        pass
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
