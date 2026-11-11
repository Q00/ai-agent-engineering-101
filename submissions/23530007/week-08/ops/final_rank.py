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
    ap.add_argument("--rate", type=float, default=30.0)
    ap.add_argument("--v", type=float, default=0.40)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
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
