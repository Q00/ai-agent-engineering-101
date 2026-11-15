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


def exact_stars(db, d, now, hours=(3, 6, 12)):
    """11-15 21:00: the board shows stars_rank to 2 decimals, so one rounding tick in 3 h (SungJinho 4.42 -> 4.43, a
    real +.0024) was extrapolated as +.14 over 42 h and the run put SungJinho above us in 100% of runs (P 72% -> 33%).
    Rebuild each student shop's stars_rank from the ledger's verified reviews, (sum of per-account means + 15) / (n + 5),
    now and h hours ago. Returns {player: {0: x, h: x, ...}} and the median board - exact offset."""
    from collections import defaultdict
    import statistics
    st = {(p.get("shop") or {}).get("name"): p["player"] for p in d["players"] if p.get("student")}
    url = {}
    for (x,) in db.execute("SELECT data FROM ledger WHERE kind='join'"):
        x = json.loads(x).get("data") or {}
        if x.get("name") in st and x.get("url"):
            url[x["url"].rstrip("/")] = st[x["name"]]
    rev = defaultdict(list)
    for ts, x in db.execute("SELECT ts, data FROM ledger WHERE kind='result' AND round=3"):
        x = json.loads(x).get("data") or {}
        if x.get("stars") is None or not x.get("verified") or not x.get("receipt"):
            continue
        k = url.get(str(x.get("shop")).rstrip("/"))
        if k:
            rev[k].append((ts, x["buyer"], x["stars"]))
    out = {}
    for k, L in rev.items():
        out[k] = {}
        for h in (0,) + tuple(hours):
            m = defaultdict(list)
            for ts, b, s_ in L:
                if ts <= now - h * 3600:
                    m[b].append(s_)
            out[k][h] = (sum(statistics.mean(v) for v in m.values()) + 15) / (len(m) + 5)
    board = {st[s["name"]]: s.get("stars_rank") for s in d["shops"] if s["name"] in st}
    offs = [board[k] - v[0] for k, v in out.items() if board.get(k)]
    return out, (statistics.median(offs) if offs else 0.0)


def rank(col, k):
    return 1 + sum(1 for q in col.values() if q > col[k])


def simulate(n=2000, seed=None):
    import board_api
    now = time.time()
    H = max(0.0, (END - now) / 3600)
    db = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
    d0 = board_api.fetch(timeout=20)
    R0 = rows_at(d0)
    EX, OFF = exact_stars(db, d0, now)
    W = {h: rows_at(json.loads(db.execute("SELECT data FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                                          (now - h * 3600,)).fetchone()[0])) for h in (1.5, 3, 6, 12, 24)}
    rnd = random.Random(seed)
    who, ev, margins = Counter(), Counter(), []
    cond = {}                           # 11-15 (user: steer toward the scenarios we win): event -> {on: [runs, wins]}
    from collections import defaultdict
    cond = defaultdict(lambda: {True: [0, 0], False: [0, 0]})
    for _ in range(n):
        end = {}
        for k, v in R0.items():
            hb = rnd.choice((1.5, 3))
            m = rnd.uniform(0.85, 1.15)
            hs = {"book": rnd.choice((12, 24)), "revenue": rnd.choice((12, 24)), "stars": rnd.choice((3, 6, 12)),
                  "forum": rnd.choice((6, 12)), "S": hb, "deals": hb, "posts": hb}
            e = {}
            for f, h in hs.items():
                if f == "stars" and k in EX:
                    # slope from the exact ledger stars (not the 2-decimal board), anchored on the board value (the
                    # rebuilt level is off by a shop-specific amount, median -.01, so only its slope is used)
                    sl = (EX[k][0] - EX[k][h]) / h * rnd.uniform(0.85, 1.15)
                    x = v["stars"] + sl * H
                    e[f] = min(max(x, v["stars"] - 0.03), CEIL.get(k, v["stars"] + 0.04))
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
        flags = {"book_lost": rank(c["book"], OURS) > 1,
                 "revenue_lost": rank(c["revenue"], OURS) > 2,
                 "rival_stars_above": c["stars"][RIVAL] > c["stars"][OURS],
                 "our_stars_top3": rank(c["stars"], OURS) <= 3,
                 "our_saved_top12": rank(c["saved"], OURS) <= 12,
                 "our_saved_top13": rank(c["saved"], OURS) <= 13,
                 "rival_saved_top10": rank(c["saved"], RIVAL) <= 10,
                 "our_deals_top8": rank(c["deals"], OURS) <= 8,
                 "our_posts_1st": rank(c["posts"], OURS) == 1}
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
            "stars_exact": {k: {h: round(x, 4) for h, x in EX[k].items()} for k in (OURS, RIVAL) if k in EX},
            "stars_offset": round(OFF, 4),
            "events": {k: round(v / n, 3) for k, v in ev.items()},
            # P(we finish 1st | the event happens) and | it does not: the levers that separate won and lost runs
            "win_if": {k: {"yes": round(v[True][1] / v[True][0], 3) if v[True][0] else None,
                           "no": round(v[False][1] / v[False][0], 3) if v[False][0] else None,
                           "freq": round(v[True][0] / n, 3)} for k, v in cond.items()}}


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 2000
    try:
        old = json.loads(OUT.read_text())
        if time.time() - old.get("t", 0) < MAX_AGE and "--force" not in sys.argv:
            if "--print" in sys.argv:
                print(json.dumps(old, ensure_ascii=False, indent=1))
            return
    except (OSError, ValueError):
        pass
    out = simulate(n)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
