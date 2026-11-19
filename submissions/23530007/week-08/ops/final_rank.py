"""Projected final class TOTAL ranks (used by the /market-whatif skill and the market tool 'final_rank').

    .venv/bin/python ops/final_rank.py                      # us at 30 deals/h x v .40
    .venv/bin/python ops/final_rank.py --rate 40 --v 0.42   # our buyer scenario
    .venv/bin/python ops/final_rank.py --json

Per student:
- buyer: now + (saved rank now - projected saved rank at the end) x .5/45 (the saved half of the buyer score is a
  percentile among 46 students; forum, deals and posts parts held where they are). Saved ranks come from
  whatif.finals (every other buyer at its last-6-h pace, us at the scenario).
- shop: two views from the 15-min class history, capped at 1:
    trend   = the 24-h slope carried to the end
    damped  = half the 48-h slope carried to the end (percentiles saturate: the higher a shop is, the fewer to pass)
Read-only; no model call. Rough: the shop part is an extrapolation, not a model of books and stars.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "ops"))
import whatif  # noqa: E402

PER_SAVED_RANK = 0.5 / 45


def _history():
    rows = []
    for l in (HERE / ".market" / "class_history.jsonl").read_text().splitlines():
        try:
            rows.append(json.loads(l))
        except ValueError:
            pass
    return rows


DB = HERE / "data" / "market_archive.db"


def _pct(vals):
    """Percentile among students as the board computes it (ties count half); checked against student_buyer: max
    error .0005. Shops carry up to +-.01 because the board rounds stars_rank to 2 decimals."""
    n = len(vals)
    out = {}
    for k, x in vals.items():
        below = sum(1 for u in vals.values() if u < x)
        ties = sum(1 for u in vals.values() if u == x) - 1
        out[k] = (below + 0.5 * ties) / (n - 1)
    return out


def _board_pair(hours=6.0, new=None):
    """(old board, its time, new board, its time): the live board (or `new`, one the caller already fetched) and the
    archived snapshot nearest `hours` ago (data/market_archive.db, one snapshot every 15 min), at least 2 h apart;
    None without one."""
    import sqlite3
    import urllib.request
    now = time.time()
    sys.path.insert(0, str(HERE))
    import board_api
    new = new or board_api.fetch(timeout=20)                    # never the zero-history board after a URL move
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
        import board_api as _ba
        row = None
        for cand in db.execute("SELECT ts, data FROM board WHERE ts <= ? ORDER BY ABS(ts - ?) LIMIT 6",
                               (now - 2 * 3600, now - hours * 3600)):
            if _ba.sane(json.loads(cand[1])):          # skip snapshots taken in the zero-history minute
                row = cand
                break
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return None
    return json.loads(row[1]), row[0], new, now


PATCH36_T = 1794472800          # 2026-11-12 ~08:40 UTC, the board rescored saved (patch 3.6)


def project_components(rate=None, v=None, hours=6.0, overrides=None, top=10, board=None):
    """11-11 (user: 'build the scenario that keeps us 1st'): the final class totals projected PART BY PART. Each
    student's book, revenue, deals and posts continue at their slope over the last `hours` (two board snapshots);
    saved continues with the per-deal value the board implies over that window; stars_rank and the forum rating
    stay. Then the board's own formula: shop = .5 book + .3 revenue + .2 stars (percentiles among students),
    buyer = .5 saved + .3 forum rating + .1 deals + .1 posts. `rate`, `v` set OUR buyer's deals/h and per-deal
    value; `overrides` = {(player, field): value} with fields book_rate, rev_rate, deals_rate, posts_rate, v,
    stars, forum for what-if scenarios. Returns None when no archived snapshot is old enough."""
    pair = _board_pair(hours, board)
    if not pair:
        return None
    A, tA, B, tB = pair
    span, left = (tB - tA) / 3600, max(0.0, (whatif.END - tB) / 3600)

    def idx(b):
        sh = {s["name"]: s for s in b["shops"]}
        bu = {x["name"]: x for x in b["buyers"]}
        return {p["player"]: (sh.get((p.get("shop") or {}).get("name")), bu.get((p.get("buyer") or {}).get("name")), p)
                for p in b["players"] if p.get("student")}
    a, b = idx(A), idx(B)
    over = dict(overrides or {})
    if rate:
        over[("yms979", "deals_rate")] = float(rate)
    if v:
        over[("yms979", "v")] = float(v)
    cur, end, slopes = {}, {}, {}
    for p, (s, x, _) in b.items():
        if not s or not x or x.get("saved") is None:
            continue
        s0, x0, _ = a.get(p) or (None, None, None)
        c = {"book": s.get("book") or 0, "rev": s.get("revenue") or 0, "stars": s.get("stars_rank") or 0,
             "saved": x["saved"], "deals": x.get("deals") or 0, "posts": x.get("posts") or 0, "forum": x.get("forum") or 0}
        sl = {"book": (c["book"] - (s0.get("book") or 0)) / span if s0 else 0.0,
              "rev": (c["rev"] - (s0.get("revenue") or 0)) / span if s0 else 0.0,
              "deals": (c["deals"] - (x0.get("deals") or 0)) / span if x0 else 0.0,
              "posts": (c["posts"] - (x0.get("posts") or 0)) / span if x0 else 0.0}
        dn = c["deals"] - (x0.get("deals") or 0) if x0 else 0
        # 11-12: across patch 3.6 (~08:40 UTC, every buyer rescored to (list-price)/(list-cost)) the two snapshots are on
        # different scales; until the window lies after the patch, the per-deal value is the current mean
        same = not x0 or tA >= PATCH36_T or tB < PATCH36_T
        vv = ((c["saved"] * (c["deals"] + 100) - x0["saved"] * (x0["deals"] + 100)) / dn) if x0 and dn > 20 and same else c["saved"]
        for f in ("book", "rev", "deals", "posts"):
            sl[f] = over.get((p, f + "_rate"), sl[f])
        vv = over.get((p, "v"), vv)
        k = max(0.0, sl["deals"]) * left
        cur[p] = c
        end[p] = {"book": c["book"] + sl["book"] * left, "rev": c["rev"] + sl["rev"] * left,
                  "stars": over.get((p, "stars"), c["stars"]),
                  "saved": (c["saved"] * (c["deals"] + 100) + k * vv) / (c["deals"] + 100 + k),
                  "deals": c["deals"] + k, "posts": c["posts"] + max(0.0, sl["posts"]) * left,
                  "forum": over.get((p, "forum"), c["forum"])}
        slopes[p] = dict(sl, v=vv)

    def score(vals):
        fields = ("book", "rev", "stars", "saved", "deals", "posts")
        pc = {f: _pct({p: x[f] for p, x in vals.items()}) for f in fields}
        rk = {f: {p: 1 + sum(1 for u in vals.values() if u[f] > x[f]) for p, x in vals.items()} for f in fields}
        return {p: {"shop": 0.5 * pc["book"][p] + 0.3 * pc["rev"][p] + 0.2 * pc["stars"][p],
                    "buyer": 0.5 * pc["saved"][p] + 0.3 * x["forum"] + 0.1 * pc["deals"][p] + 0.1 * pc["posts"][p],
                    "rk": {f: rk[f][p] for f in fields}} for p, x in vals.items()}
    sc, se = score(cur), score(end)
    order = sorted(se, key=lambda p: -(se[p]["shop"] + se[p]["buyer"]))
    rows = []
    for i, p in enumerate(order):
        c, e, sl = sc[p], se[p], slopes[p]
        rows.append({"player": p, "shop_name": b[p][0]["name"], "rank_end": i + 1, "total_now": round(c["shop"] + c["buyer"], 3),
                     "total_end": round(e["shop"] + e["buyer"], 3),
                     "shop": [round(c["shop"], 3), round(e["shop"], 3)], "buyer": [round(c["buyer"], 3), round(e["buyer"], 3)],
                     "ranks_now_end": {f: [c["rk"][f], e["rk"][f]] for f in ("book", "rev", "stars", "saved", "deals", "posts")},
                     "pace": {"book_M_h": round(sl["book"] / 1e6, 2), "rev_M_h": round(sl["rev"] / 1e6, 1),
                              "deals_h": round(sl["deals"], 1), "v": round(sl["v"], 3)}})
    me = next(r for r in rows if r["player"] == "yms979")
    return {"method": "components", "window_h": round(span, 1), "hours_left": round(left, 1),
            "us": {"rank_end": me["rank_end"], "total_end": me["total_end"]},
            "rows": [r for r in rows if r["rank_end"] <= top or r["player"] == "yms979"],
            "note": "each part continues at its last-window slope; stars and forum held; shop +-.01 (stars rounding); "
                    "a 6 h window carried 140 h is rough: read gaps under ~.03 as a tie"}


def project(rate=30.0, v=0.40, top=10):
    board, names, fin, hours = whatif.finals(rate, v)
    H = _history()
    last = H[-1]
    now_t = last["t"]

    def at(h):
        t = now_t - h * 3600
        return min(H, key=lambda r: abs(r["t"] - t))

    o24, o48 = at(24), at(48)
    d24, d48 = (now_t - o24["t"]) / 86400, (now_t - o48["t"]) / 86400
    days = hours / 24
    rank_now = {n: i + 1 for i, n in enumerate(sorted(board, key=lambda n: -board[n]["saved"]))}
    rank_end = {n: i + 1 for i, n in enumerate(sorted(fin, key=lambda n: -fin[n]))}
    by_player = {p: n for n, p in names.items()}
    out = []
    for player, cur in last["p"].items():
        if None in cur or player not in by_player or by_player[player] not in board:
            continue
        bn = by_player[player]
        buyer = min(1.0, max(0.0, cur[2] + (rank_now[bn] - rank_end[bn]) * PER_SAVED_RANK))
        s24 = (cur[1] - o24["p"][player][1]) / d24 if player in o24["p"] and None not in o24["p"][player] else 0.0
        s48 = (cur[1] - o48["p"][player][1]) / d48 if player in o48["p"] and None not in o48["p"][player] else 0.0
        shop_t = min(1.0, max(0.0, cur[1] + s24 * days))
        shop_d = min(1.0, max(0.0, cur[1] + 0.5 * s48 * days))
        out.append({"player": player, "now": cur[0], "shop_now": cur[1], "buyer_now": cur[2],
                    "saved_rank": [rank_now[bn], rank_end[bn]], "buyer_end": round(buyer, 3),
                    "shop_slope_day_24h": round(s24, 3), "shop_slope_day_48h": round(s48, 3),
                    "total_trend": round(shop_t + buyer, 3), "total_damped": round(shop_d + buyer, 3)})
    for key in ("total_trend", "total_damped"):
        for i, r in enumerate(sorted(out, key=lambda r: -r[key])):
            r["rank_" + key.split("_")[1]] = i + 1
    out.sort(key=lambda r: r["rank_damped"])
    keep = [r for r in out if r["rank_damped"] <= top or r["rank_trend"] <= top or r["player"] == "yms979"]
    me = next(r for r in out if r["player"] == "yms979")
    return {"hours_left": round(hours, 1), "our_scenario": {"deals_h": rate, "v": v},
            "us": {"rank_trend": me["rank_trend"], "rank_damped": me["rank_damped"],
                   "total_trend": me["total_trend"], "total_damped": me["total_damped"]},
            "rows": keep,
            "note": "buyer = saved-rank change x .011; shop = 24-h trend or half the 48-h trend, capped at 1. Rough."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=None)
    ap.add_argument("--v", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--linear", action="store_true", help="the old view: shop score trend carried forward")
    a = ap.parse_args()
    a.rate_given, a.v_given = a.rate is not None, a.v is not None
    a.rate, a.v = a.rate or 30.0, a.v or 0.40
    comp = None if a.linear else project_components(a.rate if a.rate_given else None, a.v if a.v_given else None)
    if comp:
        if a.json:
            print(json.dumps(comp, ensure_ascii=False, indent=1))
            return
        print(f"{comp['hours_left']} h left | by parts, slopes over the last {comp['window_h']} h | we end {comp['us']['rank_end']}"
              f" at {comp['us']['total_end']}")
        print(f"{'':2s} {'player':13s} {'now':>6s} {'end':>6s} | shop now>end book  rev stars | buyer now>end saved (v, deals/h) deals posts")
        for r in comp["rows"]:
            k, pc = r["ranks_now_end"], r["pace"]
            print(f"{r['rank_end']:2d} {r['player'][:13]:13s} {r['total_now']:.3f} {r['total_end']:.3f} | {r['shop'][0]:.3f}>{r['shop'][1]:.3f}"
                  f" {k['book'][0]:2d}>{k['book'][1]:<2d} {k['rev'][0]:2d}>{k['rev'][1]:<2d} {k['stars'][0]:2d}"
                  f"  | {r['buyer'][0]:.3f}>{r['buyer'][1]:.3f} {k['saved'][0]:2d}>{k['saved'][1]:<2d} ({pc['v']:.3f}, {pc['deals_h']:.0f}/h)"
                  f" {k['deals'][0]:2d}>{k['deals'][1]:<2d} {k['posts'][0]:2d}>{k['posts'][1]:<2d}")
        print(comp["note"])
        return
    out = project(a.rate, a.v)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    u = out["us"]
    print(f"{out['hours_left']} h left | our buyer at {a.rate:g}/h x v {a.v:.2f} -> "
          f"final rank {u['rank_damped']} (damped, {u['total_damped']}) / {u['rank_trend']} (trend, {u['total_trend']})")
    print(f"{'player':14s} {'now':>6s} {'saved rk':>9s} {'buyer':>6s} {'shop/day 24h 48h':>17s} {'damped':>7s} {'trend':>7s}")
    for r in out["rows"]:
        print(f"{r['player']:14s} {r['now']:6.3f} {r['saved_rank'][0]:>4d}->{r['saved_rank'][1]:<3d} {r['buyer_end']:6.3f}"
              f" {r['shop_slope_day_24h']:+8.3f} {r['shop_slope_day_48h']:+7.3f}"
              f" {r['total_damped']:6.3f}({r['rank_damped']}) {r['total_trend']:6.3f}({r['rank_trend']})")


if __name__ == "__main__":
    main()
