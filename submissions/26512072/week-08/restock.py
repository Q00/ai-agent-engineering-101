"""Round 3 restock hook: what the shop buys (the kit runs it every 60 s and buys what the last line names).

Supply opens once every 20 minutes for 3 minutes (inventory.json "restock": open, opens_at, closes_at). Outside a
window the kit would wait 90 s per line and then skip it, so the hook buys nothing unless the window is open or
opens within a minute, and what it buys has to last until the next window.

    python restock.py --state .market        ->  {"buy": [{"sku": "...", "qty": 1}, ...]}

Plain code, no model: the numbers decide.
Demand signal, best first:
  1. SKUs I sold myself (sales_memory.jsonl), restocked to 2 units;
  2. SKUs the whole market settled recently (the public ledger's settle entries, market_sales.json);
  2b. categories other agents ask for on the forum ("구합니다", "looking for"), the middle SKU of each;
  3. cold start: per category, the SKU whose list price is closest to the category's median (orders accept SKUs
     priced 0.5-1.5 x a reference price, so the middle of the category fits the most orders).
Money: keep 2,000,000 in cash, hold at most 1,000,000 of stock at cost, spend at most 10% of the balance a run
(about two runs fit in one window).
Food: only SKUs that keep for 10 days or more, unless the market or I sold that SKU before.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from r3lib import HERE, SALES, append_jsonl, catalog, forum_wants, read_json, read_jsonl, refresh_market_sales

CASH_FLOOR = 2_000_000
STOCK_CAP = 1_000_000
SPEND_SHARE = 0.10
MAX_LINES = 10
EST_COST = 0.65            # expected cost as a share of list price (A 55-75%, B 40-60%, C 25-45%)
LEAD = 60                  # start a run this many seconds before the window opens (the kit waits for it)
LOG = HERE / "logs" / "restock.log"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    inv = read_json(md / "inventory.json", {}) or {}
    cat = catalog()
    if int(inv.get("round") or 0) < 3 or not cat:
        print(json.dumps({"buy": []}))
        return
    win = inv.get("restock") or {}
    if win and not win.get("open") and (win.get("opens_at") or 0) - time.time() > LEAD:
        print(json.dumps({"buy": []}))                      # window closed: buying now would only be skipped
        return
    balance = inv.get("balance") or 0
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    if balance < inv.get("bankrupt_line", 500_000) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        print(json.dumps({"buy": [], "bankrupt": True}))
        return
    have = Counter(u["sku"] for u in units)
    stock_cost = sum(u.get("cost") or 0 for u in units)
    budget = min(balance - CASH_FLOOR, STOCK_CAP - stock_cost, balance * SPEND_SHARE)
    if budget <= 0:
        print(json.dumps({"buy": []}))
        return

    mine = Counter(s["sku"] for s in read_jsonl(SALES) if s.get("outcome") == "deal")
    market = refresh_market_sales()
    now = time.time()
    hot = Counter({sku: p["n"] * (2 if now - p.get("last", 0) < 6 * 3600 else 1)
                   for sku, p in market.items() if sku in cat})

    keeps = lambda sku: cat[sku]["kind"] == "goods" or (cat[sku].get("shelf_hours") or 0) >= 240
    wants = []                                              # (reason, sku, target qty), best first
    for sku, _ in mine.most_common():
        if sku in cat:
            wants.append(("sold by me", sku, 3))         # it has to last the 20 minutes to the next window
    for sku, _ in hot.most_common(60):
        if keeps(sku) or sku in mine:
            wants.append(("sold in the market", sku, 3 if hot[sku] >= 3 else 1))
    demand = read_json(md / "demand.json", []) or []
    medians = {d["category"]: d.get("median_price") for d in demand}
    for c, n in sorted(forum_wants(cat).items(), key=lambda kv: -kv[1]):   # what agents ask for on the forum
        skus = [s for s, x in cat.items() if x["category"] == c and keeps(s)]
        if skus and medians.get(c):
            mid = min(skus, key=lambda s: abs(cat[s]["list_price"] - medians[c]))
            wants.append((f"asked for on the forum ({n} posts)", mid, 1))
    for d in sorted(demand, key=lambda d: d.get("median_price") or 0):   # cheap categories first: more units per credit
        skus = [s for s, x in cat.items() if x["category"] == d["category"] and keeps(s)]
        if skus and d.get("median_price"):
            mid = min(skus, key=lambda s: abs(cat[s]["list_price"] - d["median_price"]))
            wants.append(("cold start: middle of the category", mid, 1))

    buy, spend, seen = [], 0.0, set()
    for why, sku, target in wants:
        if sku in seen or len(buy) >= MAX_LINES:
            continue
        seen.add(sku)
        qty = target - have.get(sku, 0)
        price = cat[sku]["list_price"] * EST_COST
        if qty <= 0 or spend + price > budget:
            continue
        qty = min(qty, int((budget - spend) // price))
        if qty <= 0:
            continue
        buy.append({"sku": sku, "qty": qty})
        spend += qty * price
        append_jsonl(LOG, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "sku": sku, "name": cat[sku]["name"],
                           "qty": qty, "why": why, "est_cost": round(qty * price), "balance": balance,
                           "stock_cost": stock_cost})
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
