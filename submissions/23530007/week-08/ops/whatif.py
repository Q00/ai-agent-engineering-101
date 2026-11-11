"""What-if projection of the buyer's final saved and class rank (used by the /market-whatif skill).

    .venv/bin/python ops/whatif.py                           # default grid of deals/h x per-deal v
    .venv/bin/python ops/whatif.py --rate 35 --v 0.42        # one scenario
    .venv/bin/python ops/whatif.py --json

Every student buyer is projected at its own last-6-h pace (ledger cache: deals/h and per-deal v with breaks scored
as the rules do); we are projected at the scenario. saved' = (S + k v) / (N + 100 + k), S = saved x (N + 100).
Ranks among students only (46). Read-only; no model call.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
END = 1794927540            # 2026-11-17 23:59 KST


def project(rate=None, v=None):
    """The projection as a dict (also the market tool 'projection')."""
    now = time.time()
    hours = max(0.0, (END - now) / 3600)
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=20))
    P = [p for p in b["players"] if p.get("student")]
    names = {(p.get("buyer") or {}).get("name"): p["player"] for p in P}
    board = {x["name"]: x for x in b["buyers"] if x["name"] in names and x.get("saved") is not None}
    sv = json.loads((HERE / ".market" / "shop_value.json").read_text())
    broken = sv.get("broken") or {}
    deals = sv.get("deals") or []
    span = max(1.0, (max(d["ts"] for d in deals) - min(d["ts"] for d in deals)) / 3600) if deals else 6.0
    pace = {}
    for d in deals:
        u = d.get("unit")
        dv = (0.0 if broken[u] else -1.0) if u in broken else 1 - d["x"]
        e = pace.setdefault(d.get("buyer"), [0, 0.0])
        e[0] += 1
        e[1] += dv

    def final(saved, n, rate, v):
        k = rate * hours
        return (saved * (n + 100) + k * v) / (n + 100 + k)

    others = {}
    for name, x in board.items():
        if name == "yms979":
            continue
        e = pace.get(name)
        if e and e[0] >= 10 and x.get("alive", True):
            others[name] = final(x["saved"], x["deals"], e[0] / span, e[1] / e[0])
        else:
            others[name] = x["saved"]                     # frozen or unknown pace: stays where it is
    me = board["yms979"]
    scen = [(rate, v)] if rate and v else [(r, vv) for r in (30, 40, 50) for vv in (0.38, 0.40, 0.42, 0.44)]
    rows = []
    for r, vv in scen:
        f = final(me["saved"], me["deals"], r, vv)
        rank = 1 + sum(1 for s in others.values() if s > f)
        rows.append({"deals_h": r, "v": vv, "final_saved": round(f, 4), "saved_rank": rank})
    ladder = sorted(others.items(), key=lambda kv: -kv[1])
    return {"hours_left": round(hours, 1), "now": {"saved": me["saved"], "deals": me["deals"],
                                                    "saved_rank": 1 + sum(1 for x in board.values() if x["saved"] > me["saved"])},
            "scenarios": rows, "projected_ladder_ranks_8_20": [(i + 1, n, round(s, 4)) for i, (n, s) in enumerate(ladder) if 7 <= i <= 19],
            "note": "every other student at its last-6-h pace; a deal raises our final saved only if v > our final mean"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float)
    ap.add_argument("--v", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = project(a.rate, a.v)
    me = {"saved": out["now"]["saved"], "deals": out["now"]["deals"]}
    rows = out["scenarios"]
    board = range(46)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"{out['hours_left']} h left | now saved {me['saved']} on {me['deals']} deals, saved rank {out['now']['saved_rank']} of {len(board)}")
    print("scenario (deals/h, v) -> final saved, projected saved rank")
    for r in rows:
        print(f"  {r['deals_h']:>4} /h  v {r['v']:.2f}  ->  {r['final_saved']:.4f}  rank {r['saved_rank']}")
    print("others at their last-6-h pace (projected end), ranks 8-20 without us:")
    for i, n, s in out["projected_ladder_ranks_8_20"]:
        print(f"  {i:2d} {n:22s} {s}")


if __name__ == "__main__":
    main()
