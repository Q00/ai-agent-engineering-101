"""restock hook (agent.json "restock"): what the shop buys this run. The kit runs it every 60 s in Round 3.

    python restock.py --state .market

Ranks SKUs by the revenue they settled in the market over policy lookback_h (the ledger, through market_tool),
skips the ones the policy rules out (too few sales, list price too high, supply cost surging, food that keeps too
little), and buys enough of each to cover cover_h hours of my expected sales. Buys only while a supply window is
open (or about to open, on the market's clock), and spends at most window_spend_frac of (balance - cash_reserve)
per window. Every buying run is logged to logs/restock.jsonl. Also starts the strategist when policy.json is stale.
The last line is {"buy": [{"sku": ..., "qty": ...}]} for the kit.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import market_tool as mt

HERE = Path(__file__).resolve().parent
WINDOW = mt.STATE / "restock_window.json"
LOG = HERE / "logs" / "restock.jsonl"
EST_COST = {"goods": 0.62, "food": 0.55}    # expected unit cost / list (grade mix x cost bands), a little high


def maybe_start_strategist(pol):
    """The strategist runs in its own process so a slow model call never holds up buying."""
    try:
        age = time.time() - json.loads(mt.POLICY.read_text(encoding="utf-8")).get("written_at", 0)
    except (OSError, ValueError):
        age = float("inf")
    lock = mt.STATE / "strategist.pid"
    if not (HERE / "strategist.py").exists() or age < pol.get("strategist_every_min", 20) * 60:
        return
    if lock.exists() and time.time() - lock.stat().st_mtime < 600:      # one already running (or crashed < 10 min ago)
        return
    lock.write_text("starting")
    subprocess.Popen([sys.executable, str(HERE / "strategist.py")], cwd=HERE, start_new_session=True,
                     stdout=open(HERE / "logs" / "strategist.out", "a"), stderr=subprocess.STDOUT)


def plan(inv, pol, cat, now):
    st = mt.sku_stats(pol["lookback_h"], cat)
    surge = {s["sku"]: s["factor"] for s in inv.get("surge") or []}
    have = {}
    for u in inv.get("units", []):
        have[u["sku"]] = have.get(u["sku"], 0) + 1
    mine = sum(s["mine"] for s in st.values())
    share = pol["share_guess"]
    sold_skus = [k for k, s in st.items() if s["mine"]]
    if mine >= 10:                                  # enough of my own sales to measure my share where I sell
        share = min(0.25, max(0.01, mine / max(1, sum(st[k]["n"] for k in sold_skus))))

    try:
        w = json.loads(WINDOW.read_text())
    except (OSError, ValueError):
        w = {}
    opens = inv["restock"]["opens_at"]
    if w.get("opens_at") != opens:
        w = {"opens_at": opens, "spent": 0}
    spendable = max(0, inv["balance"] - pol["cash_reserve"])
    budget = spendable * pol["window_spend_frac"] - w["spent"]

    ranked = []
    for sku, s in sorted(st.items(), key=lambda kv: -kv[1]["revenue"]):
        c = cat.get(sku) or {}
        why = None
        if s["n"] < pol["min_sales"]:
            why = "few sales"
        elif c.get("list_price", 0) > pol["max_unit_list"]:
            why = "list price above max_unit_list"
        elif surge.get(sku, 1) > pol["max_surge"]:
            why = f"surge {surge[sku]}"
        elif c.get("kind") == "food" and (c.get("shelf_hours") or 0) < pol["min_shelf_h"]:
            why = "short shelf life"
        ranked.append((sku, s, c, why))

    buy, considered = [], []
    for sku, s, c, why in ranked:
        if len(buy) >= pol["max_lines"] or len(considered) >= 25:
            break
        if why:
            if len(considered) < 25:
                considered.append({"sku": sku, "revenue": s["revenue"], "skip": why})
            continue
        rate = s["n"] / pol["lookback_h"]                           # market deals per hour for this SKU
        want = max(1, round(rate * share * pol["cover_h"]))
        qty = min(20, want - have.get(sku, 0))
        unit = c["list_price"] * EST_COST.get(c.get("kind"), 0.62) * surge.get(sku, 1)
        qty = min(qty, int(budget // unit)) if unit > 0 else 0
        considered.append({"sku": sku, "revenue": s["revenue"], "rate_h": round(rate, 1), "want": want,
                           "have": have.get(sku, 0), "qty": max(0, qty)})
        if qty >= 1:
            buy.append({"sku": sku, "qty": qty})
            budget -= qty * unit
            w["spent"] += qty * unit
    return buy, considered, w, {"share": round(share, 4), "spendable": spendable}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    a = ap.parse_args()
    mt.sync()
    pol = mt.policy()
    try:
        maybe_start_strategist(pol)
    except OSError as e:
        print(f"[restock] strategist not started: {e!r}", file=sys.stderr)
    try:
        inv = json.loads((Path(a.state) / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"buy": []}))
        return
    out = {"buy": []}
    if inv.get("round", 0) >= 3:
        if inv["balance"] < inv.get("bankrupt_line", 0) and inv.get("bankruptcies_left", 0) > 0 and not inv["units"]:
            out["bankrupt"] = True
        now = mt.market_now()
        r = inv.get("restock") or {}
        open_now = r.get("open") or (r.get("opens_at", 0) - now < 70 and r.get("closes_at", 0) > now)
        if open_now:
            buy, considered, w, info = plan(inv, pol, mt.catalog(), now)
            out["buy"] = buy
            WINDOW.write_text(json.dumps(w))
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                                    "balance": inv["balance"], "units": len(inv["units"]), **info,
                                    "buy": buy, "considered": considered[:15]}, ensure_ascii=False) + "\n")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
