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


def rank(col, k):
    return 1 + sum(1 for q in col.values() if q > col[k])


def simulate(n=2000, seed=None):
    import board_api
    now = time.time()
    H = max(0.0, (END - now) / 3600)
    db = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
    R0 = rows_at(board_api.fetch(timeout=20))
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
