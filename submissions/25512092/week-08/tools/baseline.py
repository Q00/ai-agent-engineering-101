"""Regression check for the restructure: same data in, same decisions out.

    python tools/baseline.py snap NAME          copy inventory, catalog, market_stats.json and market.db (backup API)
                                                 to reports/cache/baseline/NAME/ with the time of the copy
    python tools/baseline.py run NAME LABEL     run the current code on that copy, clock frozen at the copy's time,
                                                 -> reports/cache/baseline/NAME/out_LABEL.json
    python tools/baseline.py diff NAME A B      differences between two runs (exit 1 if any)

What a run computes (the decisions the restructure must not change):
  case       raw_shop_case for prev None / normal / overstock, decide() with the stored case state
  buy        make_plan at the real budget and at the budget without STOCK_CAP, with its log lines
  floor      pricing.floor per held unit for normal, cash_low, overstock x 1.00 / 0.95, endgame (40 h before end)
  first ask  seller_r3.first_ask per unit for normal x 1.00 / 1.05 / 1.10 and for the other cases
  warranty   strategy.warranty_hours per unit in normal
"""
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(HERE, "reports", "cache", "baseline")
sys.path.insert(0, HERE)


def snap(name):
    d = os.path.join(BASE, name)
    os.makedirs(d, exist_ok=True)
    for f in ("inventory.json", "catalog.json"):
        shutil.copy(os.path.join(HERE, ".market", f), os.path.join(d, f))
    shutil.copy(os.path.join(HERE, "market_stats.json"), os.path.join(d, "market_stats.json"))
    src, dst = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30), sqlite3.connect(os.path.join(d, "market.db"))
    src.backup(dst)
    src.close()
    dst.close()
    json.dump({"ts": time.time()}, open(os.path.join(d, "meta.json"), "w"))
    print(f"snapshot -> {d}")


def run(name, label):
    d = os.path.join(BASE, name)
    T = json.load(open(os.path.join(d, "meta.json")))["ts"]
    time.time = lambda: T                                   # every module reads the frozen clock
    import market_db
    market_db.DB_PATH = os.path.join(d, "market.db")
    market_db.STATS_PATH = os.path.join(d, "market_stats.json")
    import forum_post
    forum_post.DB_PATH = market_db.DB_PATH
    import pricing
    import restock
    import strategy
    import seller_r3
    inv = json.load(open(os.path.join(d, "inventory.json"), encoding="utf-8"))
    cat = json.load(open(os.path.join(d, "catalog.json"), encoding="utf-8"))
    stats = market_db.load_stats()
    out = {"ts": T}

    db = market_db.connect()
    state = strategy.load_state(db)
    db.close()
    out["case"] = {str(p): strategy.raw_shop_case(inv, T, p) for p in (None, "normal", "overstock")}
    out["decide"] = strategy.decide(inv, T, state)

    cash = inv.get("balance", 0)
    real = min((cash - restock.CUSHION) * restock.spend_share(), restock.MAX_STOCK_COST - pricing.held_cost(inv))
    free = (cash - restock.CUSHION) * restock.spend_share()
    out["buy"] = {}
    for tag, b in (("real", real), ("free", free), ("1M", 1_000_000)):
        lines = []
        plan = restock.make_plan(inv, inv["units"], cat, b, lines.append, record=False) if b > 0 else []
        out["buy"][tag] = {"budget": int(b), "plan": plan, "log": lines}

    endgame_t = pricing.ROUND_END - 40 * 3600
    out["units"] = {}
    for u in inv["units"]:
        if u.get("grade") == "expired":
            continue
        avg, _ = market_db.price_of(u["sku"], stats)
        r = {}
        for case, mult in (("normal", 1.0), ("cash_low", 1.0), ("overstock", 1.0), ("overstock", 0.95)):
            f, rule = pricing.floor(u, avg, case, T, overstock_mult=mult)
            r[f"floor {case} {mult}"] = [f, rule]
            if case == "normal":
                for v in ("1.00", "1.05", "1.10"):
                    r[f"ask normal {v}"] = seller_r3.first_ask(u, avg, f, rule, {"key": "shop_normal_ask", "variant": v})
            else:
                r[f"ask {case} {mult}"] = seller_r3.first_ask(u, avg, f, rule, {"key": "x", "variant": str(mult)})
        r["floor endgame"] = list(pricing.floor(u, avg, "endgame", endgame_t))
        r["warranty normal"] = strategy.warranty_hours(u, "normal", stats)
        out["units"][u["unit"]] = r
    path = os.path.join(d, f"out_{label}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True, default=str)
    print(f"{label}: case {out['decide']}, buy real {len(out['buy']['real']['plan'])} / free "
          f"{len(out['buy']['free']['plan'])} / 1M {len(out['buy']['1M']['plan'])} lines, {len(out['units'])} units -> {path}")


def diff(name, a, b):
    d = os.path.join(BASE, name)
    A = json.load(open(os.path.join(d, f"out_{a}.json"), encoding="utf-8"))
    B = json.load(open(os.path.join(d, f"out_{b}.json"), encoding="utf-8"))
    bad = []

    def walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                walk(x.get(k), y.get(k), f"{path}/{k}")
        elif x != y:
            bad.append(f"{path}: {json.dumps(x, ensure_ascii=False)[:150]} != {json.dumps(y, ensure_ascii=False)[:150]}")
    walk(A, B, "")
    n = len(A["units"])
    print(f"{a} vs {b}: {len(bad)} differences ({n} units, buy lists, case)")
    for line in bad[:40]:
        print("  " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "snap":
        snap(sys.argv[2])
    elif cmd == "run":
        run(sys.argv[2], sys.argv[3])
    else:
        sys.exit(diff(*sys.argv[2:5]))
