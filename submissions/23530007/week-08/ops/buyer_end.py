"""Where the buyer (and the class total) ends, with uncertainty (11-12, user: 'compute it carefully').

Every student's parts (book, revenue, deals, posts, saved via per-deal v) continue at their own pace over the
window since patch 3.6 (ops/final_rank.project_components); stars and forum are held. Monte Carlo: each run draws,
for every OTHER student, deals/h x lognormal(sd RATE_SD) and v + normal(V_SD), and for us the same around the
scenario; it reports our final class buyer rank and class total rank as distributions.

    .venv/bin/python ops/buyer_end.py [--runs 300] [--scen 75:0.846 58:0.894 80:0.88]
"""
import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ops"))
import final_rank as fr  # noqa: E402

BUYER_POLICY_T = 1794493474   # 11-12 14:24 UTC: break-even floors (the last buyer policy change); update on the next
RATE_SD = 0.30      # a 3-4 h pace carried 120 h: deals/h of one student easily moves +-30%
V_SD = 0.025        # per-deal v of one student over 5 days


def board_pace(name="yms979", hours=2.0, board=None):
    """(deals/h, per-deal v) of a buyer over the last `hours`, implied by the board (saved x (deals + 100))."""
    import sqlite3
    board = board or __import__("board_api").fetch(timeout=20)
    db = sqlite3.connect(f"file:{fr.DB}?mode=ro", uri=True, timeout=20)
    now = time.time()
    r = db.execute("SELECT ts, data FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (now - hours * 3600,)).fetchone()
    old = json.loads(r[1])
    x = next(y for y in board["buyers"] if y["name"] == name)
    y = next(y for y in old["buyers"] if y["name"] == name)
    dd = x["deals"] - y["deals"]
    v = (x["saved"] * (x["deals"] + 100) - y["saved"] * (y["deals"] + 100)) / max(1, dd)
    return dd / max(0.25, (now - r[0]) / 3600), v


def total_odds(runs=120, window=4.0, us_hours=2.0, seed=11):
    """11-12 (user: 'review the final rank the phone report shows'): the class TOTAL as odds, not one point. Every
    other student continues at its own pace over `window` h (+-RATE_SD deals/h, +-V_SD v per run); our buyer at its
    board pace of the last `us_hours` (our policy changed during the day; theirs we cannot see). Returns P(1st),
    our lead over the best other (median, p10, p90), the rival that most often ends ahead or nearest, and the
    point projection's ranks."""
    board = __import__("board_api").fetch(timeout=20)
    # our pace since the last buyer policy change (at least 1 h, at most us_hours)
    us_hours = max(1.0, min(us_hours, (time.time() - BUYER_POLICY_T) / 3600))
    rate, v = board_pace("yms979", us_hours, board)
    base = fr.project_components(rate, v, hours=window, top=60, board=board)
    pace = {r["player"]: r["pace"] for r in base["rows"]}
    rng = random.Random(seed)
    leads, firsts, rivals = [], 0, {}
    for _ in range(runs):
        ov = {}
        for p, pc in pace.items():
            if p == "yms979":
                continue
            ov[(p, "deals_rate")] = max(0.0, pc["deals_h"] * rng.lognormvariate(0, RATE_SD))
            ov[(p, "v")] = min(1.0, pc["v"] + rng.gauss(0, V_SD))
        r = fr.project_components(rate * rng.lognormvariate(0, RATE_SD / 2), min(1.0, v + rng.gauss(0, V_SD / 2)),
                                  hours=window, top=60, board=board, overrides=ov)
        me = next(x for x in r["rows"] if x["player"] == "yms979")
        best = max((x for x in r["rows"] if x["player"] != "yms979"), key=lambda x: x["total_end"])
        leads.append(me["total_end"] - best["total_end"])
        firsts += me["rank_end"] == 1
        rivals[best["player"]] = rivals.get(best["player"], 0) + 1
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
    return {"p_first": round(firsts / runs, 2), "lead_med": round(statistics.median(leads), 3),
            "lead_p10": round(q(leads, .1), 3), "lead_p90": round(q(leads, .9), 3),
            "rival": max(rivals, key=rivals.get), "us_pace": (round(rate, 1), round(v, 3)), "us_hours": round(us_hours, 1), "window_h": window,
            "runs": runs, "point": [(x["rank_end"], x["player"], x["total_end"]) for x in base["rows"][:4]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=300)
    ap.add_argument("--scen", nargs="*", default=["75:0.846", "58:0.894", "80:0.88", "100:0.86"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--window", type=float, default=None, help="hours of pace for everyone (default: since patch 3.6, 2.5-6)")
    a = ap.parse_args()
    board = __import__("board_api").fetch(timeout=20)
    win = a.window or max(2.5, min(6.0, (time.time() - fr.PATCH36_T) / 3600 - 0.2))
    base = fr.project_components(None, None, hours=win, top=60, board=board)
    pace = {r["player"]: r["pace"] for r in base["rows"]}
    rng = random.Random(7)
    out = {"hours_left": base["hours_left"], "window_h": round(win, 1), "runs": a.runs, "scenarios": []}
    # the deterministic view (no noise) of the field
    rows = sorted(base["rows"], key=lambda x: -x["buyer"][1])
    out["field_buyer_end"] = [{"player": r["player"], "buyer_now": r["buyer"][0], "buyer_end": r["buyer"][1],
                               "deals_h": r["pace"]["deals_h"], "v": r["pace"]["v"],
                               "saved_rank": r["ranks_now_end"]["saved"]} for r in rows[:12]]
    for sc in a.scen:
        rate, v = (float(x) for x in sc.split(":"))
        br, tr_, bend, gap1, gap_tot = [], [], [], [], []
        for _ in range(a.runs):
            ov = {}
            for p, pc in pace.items():
                if p == "yms979":
                    continue
                ov[(p, "deals_rate")] = max(0.0, pc["deals_h"] * rng.lognormvariate(0, RATE_SD))
                ov[(p, "v")] = min(1.0, pc["v"] + rng.gauss(0, V_SD))
            r = fr.project_components(rate * rng.lognormvariate(0, RATE_SD / 2), min(1.0, v + rng.gauss(0, V_SD / 2)),
                                      hours=win, top=60, board=board, overrides=ov)
            byb = sorted(r["rows"], key=lambda x: -x["buyer"][1])
            i = next(k for k, x in enumerate(byb) if x["player"] == "yms979")
            br.append(i + 1)
            bend.append(byb[i]["buyer"][1])
            gap1.append(byb[i]["buyer"][1] - (byb[1]["buyer"][1] if i == 0 else byb[0]["buyer"][1]))
            me = next(x for x in r["rows"] if x["player"] == "yms979")
            tr_.append(me["rank_end"])
            gap_tot.append(me["total_end"] - max(x["total_end"] for x in r["rows"] if x["player"] != "yms979"))
        q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
        out["scenarios"].append({
            "deals_h": rate, "v": v,
            "buyer_rank": {"median": statistics.median(br), "p10": q(br, .1), "p90": q(br, .9),
                           "P(1st)": round(sum(1 for x in br if x == 1) / len(br), 2),
                           "P(top3)": round(sum(1 for x in br if x <= 3) / len(br), 2)},
            "buyer_end": {"median": round(statistics.median(bend), 3), "gap_to_1st_or_2nd_median": round(statistics.median(gap1), 3)},
            "total_rank": {"P(1st)": round(sum(1 for x in tr_ if x == 1) / len(tr_), 2),
                           "lead_median": round(statistics.median(gap_tot), 3), "lead_p10": round(q(gap_tot, .1), 3)}})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"{out['hours_left']} h left, others' pace over the last {out['window_h']} h, {a.runs} runs each")
    print("field (no noise), top 12 by projected buyer:")
    for f in out["field_buyer_end"]:
        print(f"  {f['player']:16} now {f['buyer_now']:.3f} -> end {f['buyer_end']:.3f}  ({f['deals_h']}/h x {f['v']}, saved rank {f['saved_rank']})")
    for s in out["scenarios"]:
        b, t = s["buyer_rank"], s["total_rank"]
        print(f"us {s['deals_h']:g}/h x {s['v']}: buyer rank median {b['median']} (p10 {b['p10']}, p90 {b['p90']}), "
              f"P(1st) {b['P(1st)']}, P(top3) {b['P(top3)']}, buyer {s['buyer_end']['median']} "
              f"(gap {s['buyer_end']['gap_to_1st_or_2nd_median']:+}) | total P(1st) {t['P(1st)']}, lead {t['lead_median']:+} (p10 {t['lead_p10']:+})")


if __name__ == "__main__":
    main()
