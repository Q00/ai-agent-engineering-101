"""Restock hook (Round 3): what my shop buys from the market's supply, decided from the public ledger.

    python restock.py --state .market --market https://market.ourolabs.ai --name chaewoonee

The kit runs it every 60 s ("restock" in agent.json) and reads the last line:
    {"buy": [{"sku": "...", "qty": 1-20}, ...]}            (at most 10 lines; "bankrupt": true to file)

Why the ledger: the market does not publish what sells (demand.json has categories only), but every settled
deal is a public ledger line ("settle", with the SKU and the amount). ledger_stats() reads the new lines since
the last run and keeps 48 h of sales per SKU in memory/ledger_stats.json. That file is the shop's market memory.

Rules (risk: keep 30% of the starting money in cash):
  * hot goods : SKUs that sold in the market in the last 24 h at a price above the expected cost (0.6 x list)
                -> keep 2-5 units, more for faster sellers
  * hot food  : only SKUs that sold at least 3 times in the last 6 h (food is thrown away at cost) -> keep 2
  * my sales  : SKUs my own shop sold (memory/shop_sales.jsonl) count double
  * missed    : requests my shop had to turn away (memory/shop_missed.jsonl, written by seller.py): a missed
                SKU is bought like a hot one (food only if missed twice in 6 h); a missed category is explored first
  * explore   : while the ledger shows little, 1-2 units of goods from missed categories, else from demand.json
  * spend     : at most 20% of the spendable cash per run; each unit is budgeted at 0.75 x list (grade A's top cost)
  * caps      : at most 5 units of one SKU, and stock (at cost) worth at most 40% of balance + stock
Patches 3.0-3.6 (Round 3 rules as of Nov 12):
  * Book = balance - start + unsold stock at cost, so buying stock no longer lowers the score (Patch 3.3); the
    cash reserve is now only the bankruptcy guard (keep 1,500,000) and the stock cap follows the balance
  * supply opens 3 min in every 20 (inventory.json "restock"): outside the window buy nothing, no 429 retries
  * surge (Patch 3.2): skip SKUs whose market-wide factor is 1.05 or more (each extra unit costs more)
  * the round ends Nov 17 23:59 KST: no buying in the last hour (nothing would sell)
"""
import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
STATS = HERE / "memory" / "ledger_stats.json"
SALES = HERE / "memory" / "shop_sales.jsonl"
MISSED = HERE / "memory" / "shop_missed.jsonl"
CASH_KEEP = 0.30                                  # of the start: 1,500,000 kept against bankruptcy
ROUND_END = 1794927540                            # 2026-11-17 23:59 KST
SURGE_SKIP = 1.05
RUN_SHARE = 0.20
SKU_CAP = 5
STOCK_CAP = 0.40                                  # of balance + stock at cost
KEEP_HOURS = 48


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def ledger_stats(market, log, pages=20):
    """Read new public ledger lines into memory/ledger_stats.json: {"seq", "sales": {sku: [[ts, amount, shop]]}}."""
    st = read_json(STATS, {"seq": None, "sales": {}})
    after = st["seq"] if st.get("seq") is not None else -5000          # first run: the newest 5000 lines
    n_new = 0
    try:
        with httpx.Client(timeout=15) as c:
            for _ in range(pages):
                r = c.get(f"{market}/api/ledger", params={"after": after})
                rows = r.json() if r.status_code == 200 else []
                if not rows:
                    break
                for e in rows:
                    d = e.get("data") or {}
                    if e.get("kind") == "settle" and d.get("item"):
                        amt = d.get("amount", d.get("price"))
                        if isinstance(amt, (int, float)):
                            st["sales"].setdefault(d["item"], []).append([int(e.get("ts") or time.time()), int(amt),
                                                                          d.get("shop"), e.get("seq")])
                            n_new += 1
                after = st["seq"] = rows[-1]["seq"]
                if len(rows) < 500:
                    break
    except (httpx.HTTPError, ValueError) as e:
        log(f"[ledger] read failed: {type(e).__name__} {str(e)[:100]}")
    cut = time.time() - KEEP_HOURS * 3600
    st["sales"] = {k: v2 for k, v in st["sales"].items() if (v2 := [x for x in v if x[0] >= cut])}
    STATS.parent.mkdir(exist_ok=True)
    STATS.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    log(f"[ledger] {n_new} new settled deals, {len(st['sales'])} SKUs sold in {KEEP_HOURS} h")
    return st


def my_sales(hours=24):
    cut, out = time.time() - hours * 3600, {}
    try:
        for line in SALES.read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            if x.get("ts", 0) >= cut:
                out[x["sku"]] = out.get(x["sku"], 0) + 1
    except (OSError, ValueError):
        pass
    return out


def missed_requests(hours=24):
    cut, out = time.time() - hours * 3600, []
    try:
        for line in MISSED.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if x.get("ts", 0) >= cut:
                out.append(x)
    except OSError:
        pass
    return out


def plan(inv, catalog, demand, st, mine, log, rng=random, missed=()):
    """The buy list. Pure function of its inputs, so it can be tested without the market."""
    balance, start = inv.get("balance") or 0, inv.get("start") or 5_000_000
    spendable = balance - CASH_KEEP * start
    if spendable <= 0:
        log(f"[restock] cash {balance} is at the {int(CASH_KEEP * 100)}% reserve: buying nothing")
        return []
    held, stock_value = {}, 0
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            held[u["sku"]] = held.get(u["sku"], 0) + 1
            stock_value += u.get("cost") or 0
    room = STOCK_CAP * (balance + stock_value) - stock_value
    if room <= 0:
        log(f"[restock] stock worth {stock_value} at cost is at the {int(STOCK_CAP * 100)}% cap: buying nothing")
        return []
    budget = min(spendable * RUN_SHARE, room)
    now = time.time()
    cands = []
    for sku, sales in st.get("sales", {}).items():
        c = catalog.get(sku)
        if not c or not c.get("list_price"):
            continue
        day = [x for x in sales if x[0] >= now - 24 * 3600]
        six = [x for x in day if x[0] >= now - 6 * 3600]
        if not day:
            continue
        med = statistics.median(x[1] for x in day)
        margin = med - 0.6 * c["list_price"]
        if margin <= 0.05 * c["list_price"]:
            continue
        if c.get("kind") == "food":
            if len(six) < 3:
                continue
            want = 2
        else:
            want = min(5, max(2, math.ceil(len(day) / 4)))
        score = (len(day) + 2 * mine.get(sku, 0)) * margin
        cands.append((score, sku, want, len(day), int(med)))
    for sku, n in mine.items():                          # my own sellers that the ledger window missed
        if sku in catalog and not any(x[1] == sku for x in cands) and catalog[sku].get("kind") != "food":
            cands.append((n * 0.1 * catalog[sku]["list_price"], sku, 2, n, None))
    now6 = now - 6 * 3600
    miss_sku, miss_cat = {}, {}
    for x in missed:
        if x.get("sku") in catalog:
            miss_sku.setdefault(x["sku"], []).append(x.get("ts", now))
        elif x.get("category"):
            miss_cat[x["category"]] = miss_cat.get(x["category"], 0) + 1
    for sku, ts in miss_sku.items():
        c = catalog[sku]
        if c.get("kind") == "food" and sum(t >= now6 for t in ts) < 2:
            continue
        want = min(SKU_CAP, len(ts) + 1)
        score = 1.5 * len(ts) * 0.3 * (c.get("list_price") or 0)   # a buyer asked ME for it: weigh it up
        cands = [x for x in cands if x[1] != sku]
        cands.append((score, sku, want, len(ts), "missed"))
    surge = {x["sku"]: x.get("factor", 1) for x in inv.get("surge") or [] if x.get("sku")}
    hot = [x for x in cands if surge.get(x[1], 1) < SURGE_SKIP]
    for x in cands:
        if x not in hot:
            log(f"[restock] skip {x[1]}: surge x{surge[x[1]]} (the whole market is buying it)")
    cands = hot
    cands.sort(key=lambda x: x[0], reverse=True)
    buy = []
    for score, sku, want, n, med in cands:
        qty = min(want, SKU_CAP) - held.get(sku, 0)
        unit_cost = 0.75 * catalog[sku]["list_price"]
        qty = min(qty, int(budget // unit_cost), 20)
        if qty < 1:
            continue
        buy.append({"sku": sku, "qty": qty})
        budget -= qty * unit_cost
        if med == "missed":
            log(f"[restock] missed {sku} {catalog[sku].get('name', '')[:30]}: buyers asked me {n} time(s) in 24 h, "
                f"have {held.get(sku, 0)} -> buy {qty}")
        else:
            log(f"[restock] hot {sku} {catalog[sku].get('name', '')[:30]}: {n} sold/24h, median {med}, "
                f"have {held.get(sku, 0)} -> buy {qty}")
        if len(buy) >= 8:
            break
    units = sum(held.values())
    if len(buy) < 2 and units < 30 and (demand or miss_cat):   # explore: little signal yet
        goods = [d for d in demand if d.get("kind") != "food"] or list(demand)
        first = [{"category": c} for c, _ in sorted(miss_cat.items(), key=lambda x: -x[1])]
        rest = [d for d in goods if d.get("category") not in miss_cat]
        picks = (first + rng.sample(rest, k=min(2, len(rest))))[:2]
        for d in picks:
            skus = [s for s, c in catalog.items() if c.get("category") == d.get("category") and s not in held
                    and s not in {b["sku"] for b in buy}
                    and c.get("list_price") and 0.75 * c["list_price"] <= budget]
            if not skus:
                continue
            sku = rng.choice(skus)
            budget -= 0.75 * catalog[sku]["list_price"]
            buy.append({"sku": sku, "qty": 1})
            log(f"[restock] explore {sku} {catalog[sku].get('name', '')[:30]} ({d.get('category')}"
                + (", a category buyers asked me for)" if d.get("category") in miss_cat else ")"))
    return buy[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(HERE / ".market"))
    ap.add_argument("--market", default="https://market.ourolabs.ai")
    ap.add_argument("--name", default="chaewoonee")
    a = ap.parse_args()
    md = Path(a.state)
    log = lambda s: print(s, flush=True)
    inv = read_json(md / "inventory.json", {})
    if inv.get("round", 0) < 3:
        print(json.dumps({"buy": []}))
        return
    stuck = (inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0) and (inv.get("bankruptcies_left") or 0) > 0
    if stuck and not inv.get("units"):
        log("[restock] below the bankruptcy line with nothing left to sell: filing")
        print(json.dumps({"buy": [], "bankrupt": True}))
        return
    st = ledger_stats(a.market.rstrip("/"), log)
    catalog = read_json(md / "catalog.json", {})
    window = inv.get("restock") or {}
    if time.time() > ROUND_END - 3600:
        log("[restock] the round ends within the hour: buying nothing")
        buy = []
    elif window and not window.get("open"):
        buy = []                                          # supply is closed: the kit would only collect 429s
    else:
        buy = plan(inv, catalog, read_json(md / "demand.json", []), st, my_sales(), log, missed=missed_requests())
    try:                                                  # the board: [시세] every 10 min, [재고] every 60 min, votes
        import forum
        forum.market_trend(st, catalog, md)
        forum.shop_stock(inv, catalog, md)
        forum.moderate(read_json(md / "forum.json", []), inv, a.name, md)
    except Exception as e:                                # a post must never stop the restock
        log(f"[forum] skipped: {type(e).__name__} {e}")
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
