"""One-screen market status for the operator (used by the /market-status skill).

    .venv/bin/python ops/status.py            # ranks, shop health, buyer pace, guards, spend
    .venv/bin/python ops/status.py --json     # the same as JSON

Read-only: the board, the local logs and state files. No model call, no API credit.
"""
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MARKET = "https://market.ourolabs.ai"
NAME = "yms979"
END = 1794927540            # 2026-11-17 23:59 KST


def get(path):
    with urllib.request.urlopen(f"{MARKET}{path}", timeout=20) as r:
        return json.load(r)


def read(p, default=None):
    try:
        return json.loads((HERE / p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def score(t):
    if t.get("broken"):
        return 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
    return 1 - t["price"] / t["list"]


def buyer_pace(now, hours):
    trades = (read("buyer_memory.json", {}) or {}).get("trades", [])
    d = [t for t in trades if now - t["t"] < hours * 3600 and t.get("outcome") == "deal" and t.get("list") and t.get("price")]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    food = [score(t) for t in d if t.get("kind") == "food"]
    goods = [score(t) for t in d if t.get("kind") == "goods"]
    return {"hours": hours, "deals_h": round(len(d) / hours, 1), "v": mean([score(t) for t in d]),
            "food_h": round(len(food) / hours, 1), "food_v": mean(food),
            "goods_h": round(len(goods) / hours, 1), "goods_v": mean(goods),
            "breaks": sum(1 for t in d if t.get("broken"))}


def shop_pace(now, hours):
    rows = []
    try:
        raw = (HERE / "seller_r3_log.jsonl").read_bytes()[-20_000_000:].decode("utf-8", "ignore").splitlines()[1:]
    except OSError:
        raw = []
    for line in raw:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "deal" and r.get("t", 0) > now - hours * 3600:
            rows.append(r)
    margin = sum((r.get("price") or 0) - (r.get("cost") or 0) for r in rows)
    grades = {}
    for r in rows:
        grades[r.get("grade")] = grades.get(r.get("grade"), 0) + 1
    return {"hours": hours, "deals_h": round(len(rows) / hours, 1), "margin_M_h": round(margin / hours / 1e6, 2), "grades": grades}


def kit_state():
    out = subprocess.run(["pgrep", "-f", f"market_kit.py join --dir {HERE}"], capture_output=True, text=True).stdout.split()
    log = HERE / "logs" / "market-kit.log"
    tail = []
    try:
        tail = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-3000:]
    except OSError:
        pass
    url = next((l.split(" at ")[-1].split()[0] for l in reversed(tail) if "registered" in l and " at " in l), None)
    if not url:
        url = next((l.split("through the kit at ")[-1].strip() for l in reversed(tail) if "through the kit at" in l), None)
    last_task = next((l[:8] for l in reversed(tail) if "[seller]" in l and " task " in l), None)
    rejoins = sum(1 for l in tail[-600:] if "marks this shop dead" in l or "registered again" in l)
    return {"kit_running": bool(out), "public_url": url, "last_seller_task": last_task, "recent_rejoin_lines": rejoins}


def main():
    now = time.time()
    b = get("/api/board?round=3")
    P = [p for p in b["players"] if p.get("student")]
    tot = sorted(P, key=lambda p: -(p.get("student_total") or 0))
    buy = sorted(P, key=lambda p: -(p.get("student_buyer") or 0))
    shp = sorted(P, key=lambda p: -(p.get("student_shop") or 0))
    rank = lambda L: [p["player"] for p in L].index(NAME) + 1
    me = tot[rank(tot) - 1]
    shops = {s["name"]: s for s in b["shops"]}
    buyers = {x["name"]: x for x in b["buyers"]}
    live = [s for s in b["shops"] if s.get("deals")]
    books = sorted(live, key=lambda s: -(s.get("book") or 0))
    rival = next(s for s in books if s["name"] != NAME)
    gs = read(".market/guard_state.json", {}) or {}
    sc = read(".market/buyer_score.json", {}) or {}
    try:
        sys.path.insert(0, str(HERE))
        import model_env
        spend = model_env.spend_summary(now)
    except Exception as e:
        spend = {"error": repr(e)}
    i_b = rank(buy)
    out = {
        "time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), "hours_left": round((END - now) / 3600, 1),
        "total": {"rank": rank(tot), "score": me["student_total"], "second": tot[1]["player"] if tot[0]["player"] == NAME else tot[0]["player"],
                  "gap": round(me["student_total"] - (tot[1] if tot[0]["player"] == NAME else tot[0])["student_total"], 3)},
        "shop": {"rank": rank(shp), "score": me["student_shop"], "alive": shops[NAME].get("alive"),
                 "book_M": round(shops[NAME]["book"] / 1e6), "rival": rival["name"], "rival_book_M": round(rival["book"] / 1e6),
                 "lead_M": round((shops[NAME]["book"] - rival["book"]) / 1e6), "stars_rank": shops[NAME].get("stars_rank"),
                 "market_dead_shops": f"{sum(1 for s in live if not s.get('alive'))}/{len(live)}",
                 "pace_1h": shop_pace(now, 1), "pace_6h": shop_pace(now, 6)},
        "buyer": {"rank": i_b, "score": buy[i_b - 1]["student_buyer"],
                  "above": [(p["player"], p["student_buyer"]) for p in buy[max(0, i_b - 3):i_b - 1]],
                  "saved": buyers[NAME]["saved"], "deals": buyers[NAME]["deals"], "m_star": sc.get("m_star"),
                  "pace_2h": buyer_pace(now, 2), "pace_6h": buyer_pace(now, 6)},
        "guards": {"fires": gs.get("fires"), "frozen": list((gs.get("frozen") or {}).keys()), "total_gap": gs.get("total_gap"),
                   "book_eta_h": gs.get("book_eta_h")},
        "kit": kit_state(), "spend": spend,
    }
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    t, s, bu, k = out["total"], out["shop"], out["buyer"], out["kit"]
    print(f"[{out['time_utc']} UTC, {out['hours_left']} h left]")
    print(f"TOTAL  rank {t['rank']}  {t['score']}  (vs {t['second']}: {t['gap']:+.3f})")
    print(f"SHOP   rank {s['rank']}  {s['score']}  alive {s['alive']}  book {s['book_M']}M vs {s['rival']} {s['rival_book_M']}M (lead {s['lead_M']}M)"
          f"  stars_rank {s['stars_rank']}  market dead {s['market_dead_shops']}")
    print(f"       1h: {s['pace_1h']['deals_h']} deals/h, margin {s['pace_1h']['margin_M_h']}M/h {s['pace_1h']['grades']} | 6h: margin {s['pace_6h']['margin_M_h']}M/h")
    print(f"BUYER  rank {bu['rank']}  {bu['score']}  above {bu['above']}  saved {bu['saved']} (m* {bu['m_star']})  deals {bu['deals']}")
    for p in (bu["pace_2h"], bu["pace_6h"]):
        print(f"       {p['hours']}h: {p['deals_h']} deals/h v {p['v']} | food {p['food_h']}/h {p['food_v']} | goods {p['goods_h']}/h {p['goods_v']} | breaks {p['breaks']}")
    print(f"GUARDS fires {out['guards']['fires']} frozen {out['guards']['frozen']} total_gap {out['guards']['total_gap']} book_eta {out['guards']['book_eta_h']}")
    print(f"KIT    running {k['kit_running']}  url {k['public_url']}  last seller task {k['last_seller_task']}  rejoin lines (recent) {k['recent_rejoin_lines']}")
    sp = out["spend"]
    print(f"API    24h ${sp.get('today')} ({sp.get('calls')} calls) {sp.get('per_role')}  credit ~${sp.get('credit_left')} (expires 11-21 00:00 UTC)")


if __name__ == "__main__":
    main()
