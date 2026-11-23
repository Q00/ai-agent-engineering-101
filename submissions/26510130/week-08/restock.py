"""Week 08 -- the restock hook: what the shop buys, from what the market has bought.

    python restock.py --state .market        # the kit runs it every 60 s

Prints one line, {"buy": [{"sku": ..., "qty": n}, ...]} (or "bankrupt": true).

Stock that never sells is the whole capital lost, and the market hides what
sells. So the restock does not guess: it reads `market_memory` (built from the
ledger's settled deals) and stocks the categories that are selling now, in the
SKUs that sold. Before there is enough data (the first hours of the round) it
seeds one unit in many cheap, slow-to-spoil categories, because a shop with
nothing in an order's category is not even shown to that buyer.

Limits live here, in code, not in a prompt:
  - a cash floor the shop never spends below,
  - a cap on stock held at cost,
  - no short-shelf food unless that category is clearly selling,
  - no buying at all in the last hours of the round, when stock can only be lost.
Every decision is appended to logs/restock.jsonl with its reason.
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import asks
import market_memory as mm

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "restock.jsonl"
ROUND_END = float(os.environ.get("ROUND_END", datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc).timestamp()))
CASH_FLOOR = 1_500_000         # never spend below this
STOCK_CAP = 2_500_000          # stock held, at cost: the floor of the cap below
STOCK_SHARE = 0.70             # ... which grows with the balance (11-10: 20 M cash sat idle under a 2.5 M cap).
                               # 11-10 21:00, volume strategy: was 0.35. Board, 52 shops: log deals vs log units
                               # in stock r = 0.71, vs stars r = 0.12; YAHO1212 had 164 units and 8.8k visits
                               # against 400-1,400 units and 30-60k visits for the top shops. Stock counts in
                               # Book at cost, so holding it costs only storage (1 %/day), spoilage and breakage.
STOCK_CAP_MAX = 30_000_000
CAT_TARGET_MAX = 12            # units per selling category, long-shelf (was 6); short-shelf below
CAT_TARGET_SHORT = 4           # (was 2)
LINE_SPEND_SHARE = 0.08        # one asked-for line spends at most this share of the cap (a dry run wanted
                               # 11 x a 546k sofa, asked 26/h but rarely bought at a floor above the median)
LINE_QTY_MAX = 6              # units per line in the demand loop (was 3)
BREADTH_MIN_SHELF_H = 168      # breadth: one unit in every long-shelf category not held, so the shop is shown
BREADTH_MAX_LIST = 150_000     # to that category's orders at all
ASK_LINE_SHARE = 0.5           # 11-11 12:00: the asks loop took all 10 lines of every run topping up the same
BREADTH_LINE_SHARE = 0.3       # ~12 SKUs by 1-2 units, so the demand and breadth loops never ran: 18 of 140
TOPUP_MIN = 3                  # demand categories held, 24 SKUs, 21.6M cash idle under a 22.6M cap. Students'
                               # median shop: 15,280 visits to our 11,871; deals vs stock units r = 0.71.
ASK_MIN_RATE = 0.5            # asks/h before a SKU asked of this shop is stocked
ASK_SELL_THROUGH = 0.3         # deals per ask: 753 deals from 2,490 negotiations, 11-08..11-10
ASK_HORIZON_H = 2.5            # stock for this long: a supply window opens every 20 min, but not always on time
                               # (11-10 21:00: was 1.5; food is still held to 25 % of its shelf life)
ASK_MAX_QTY = 20               # the market's per-line limit
SURGE_MAX = 1.06               # skip a SKU whose market-wide surge has pushed cost up more than this
                               # (11-10: was 1.15; a grade-A unit costs ~0.66 of list, so +10 % surge puts
                               # its floor above the 0.70 opening ask buyers take)
SUPPLY_LINE_S = 11             # one supply line takes ~10 s; lines past the window's close wait 90 s and drop
SEED_SPEND = 1_200_000         # cold start: at most this much, spread wide
COLD_SETTLES = 40              # market settles seen before demand is trusted
MAX_LIST = 400_000             # dearer SKUs only when their category is selling
STOP_BEFORE_END = 2 * 3600     # 11-12 13:51: was 36 h. Unsold units keep their cost in Book at the end (agent.md),
                               # so stock that does not sell costs only storage (1 %/day); keep buying what sells.
                               # Food that would expire before the end is still skipped (late_food below).
LINES = 10                     # the kit buys at most 10 lines a run
EST_COST = 0.6                 # expected cost / list price before the unit arrives


def say(msg):
    print(msg, file=sys.stderr, flush=True)


def read(state, name, default):
    try:
        return json.loads((Path(state) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def leaf(category):
    return (category or "").split(">")[-1]


def nearest_median(skus, median, cat):
    """The SKU whose list price is closest to the category median: an order's acceptable
    SKUs are priced 0.5-1.5x a reference, so the middle of the category fits most orders."""
    return sorted(skus, key=lambda s: abs(math.log(max(cat[s]["list_price"], 1) / max(median, 1))))


def plan(inv, demand, cat, mem, now):
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    balance = inv.get("balance") or 0
    held = sum(u.get("cost") or 0 for u in units)
    have = Counter(u.get("category") for u in units)
    have_sku = Counter(u.get("sku") for u in units)
    by_cat = defaultdict(list)
    for s, x in cat.items():
        by_cat[x["category"]].append(s)
    med = {d["category"]: d["median_price"] for d in demand}
    shelf = {d["category"]: min((cat[s].get("shelf_hours") or 1e9) for s in by_cat[d["category"]])
             for d in demand if by_cat.get(d["category"])}

    if ROUND_END - now < STOP_BEFORE_END:
        return [], "last hours of the round: selling down, buying nothing"
    win = inv.get("restock") or {}
    if win and not win.get("open") and (win.get("opens_at") or 0) - now > 20:
        # Outside a window the kit waits 90 s per line and then drops it: a plan sent now is lost.
        return [], f"supply window closed, opens in {win['opens_at'] - now:.0f} s"
    lines = LINES
    if win.get("open") and win.get("closes_at"):
        lines = min(LINES, int((win["closes_at"] - now) / SUPPLY_LINE_S))
        if lines < 1:
            return [], "supply window closing: no time for a line"
    cap = min(STOCK_CAP_MAX, max(STOCK_CAP, STOCK_SHARE * (balance + held)))
    room = min(balance - CASH_FLOOR, cap - held)
    if room <= 0:
        return [], f"no room: balance {balance}, stock at cost {held}, cap {cap:.0f}"

    settles = mem.get("seen", {}).get("settle", 0)
    buy, why = [], []
    surge = {s["sku"]: s.get("factor", 1.0) for s in inv.get("surge") or []}
    paid_cost = defaultdict(list)
    for u in units:
        paid_cost[u.get("sku")].append(u.get("cost") or 0)

    to_end_h = (ROUND_END - now) / 3600

    def late_food(sku):
        """Food whose shelf life ends before the round does: unsold, its cost leaves Book."""
        x = cat[sku]
        return x.get("kind") == "food" and (x.get("shelf_hours") or 1e9) < to_end_h and to_end_h < 48

    def underwater(sku):
        """11-11 23:51: the 2.32M-list sofa G8ec51935 was asked for ~20/h, so the asks loop bought it every
        run (have 6-7), but its supply cost 1.55-1.68M x 1.04 sat above the 1.56M buyers pay for grade A
        (market median of 15): 9 units over 1M, ~14.5M of stock, about 1 sale an hour. Skip a SKU whose
        cost (held units, else the estimate) cannot be sold at the market's median with our margin."""
        med = mm.sku_median(mem, sku, "A")
        if not med:
            return False
        c = paid_cost.get(sku)
        # The cheapest held unit, not the mean: the shelf keeps the dear survivors (water G3434758a: held A
        # units 12.4-16.3k, recent deals on units costing 8.3-11.4k), so a mean skipped a SKU still selling.
        cost = min(c) if c else cat[sku]["list_price"] * EST_COST * surge.get(sku, 1.0)
        return cost * 1.04 >= med

    # 1. What buyers asked this shop for (asks.py): demand seen directly, SKU by SKU.
    ask_lines = max(1, int(lines * ASK_LINE_SHARE))
    demand_lines = max(ask_lines, lines - max(1, int(lines * BREADTH_LINE_SHARE)))
    spent = 0
    for sku, (rate, miss) in sorted(asks.rates(now).items(), key=lambda kv: -kv[1][0]):
        if rate < ASK_MIN_RATE or len(buy) >= ask_lines or sku not in cat:
            continue
        x = cat[sku]
        if surge.get(sku, 1.0) > SURGE_MAX:
            why.append(f"skip {sku} surge {surge[sku]}")
            continue
        horizon = ASK_HORIZON_H
        if x.get("kind") == "food" and x.get("shelf_hours"):
            horizon = min(horizon, x["shelf_hours"] * 0.25)      # sell well inside the shelf life
        target = min(ASK_MAX_QTY, math.ceil(rate * ASK_SELL_THROUGH * horizon))
        qty = target - have_sku[sku]
        lp = x["list_price"]
        if qty < 1 or (lp > MAX_LIST and rate < 3):
            continue
        if have_sku[sku] and qty < TOPUP_MIN:
            continue                                         # a 1-2 unit top-up is not worth a line
        if underwater(sku):
            why.append(f"skip {sku} cost above what buyers pay")
            continue
        if late_food(sku):
            continue
        unit_cost = lp * EST_COST * surge.get(sku, 1.0)
        qty = min(qty, int((room - spent) / unit_cost), max(1, int(LINE_SPEND_SHARE * cap / unit_cost)))
        if qty < 1:
            continue
        buy.append({"sku": sku, "qty": qty})
        why.append(f"asked {sku} x{qty} ({rate:.1f}/h, {miss:.1f} unserved/h, have {have_sku[sku]})")
        spent += lp * EST_COST * surge.get(sku, 1.0) * qty
        have[x["category"]] += qty
    room -= spent
    if len(buy) >= lines or room <= 0:
        return buy, "asks: " + "; ".join(why)
    if settles < COLD_SETTLES:
        # Seed: one unit in many categories, cheapest-to-hold first (goods, then long-shelf food).
        spent = 0
        seedable = [d for d in demand if d["median_price"] <= 120_000 and shelf.get(d["category"], 1e9) >= 168]
        seedable.sort(key=lambda d: (-d["skus"], d["median_price"]))   # many SKUs: a big category
        for d in seedable:
            c = d["category"]
            if have[c] or not by_cat.get(c):
                continue
            sku = nearest_median(by_cat[c], d["median_price"], cat)[0]
            est = cat[sku]["list_price"] * EST_COST
            if spent + est > min(room, SEED_SPEND - held):
                continue
            buy.append({"sku": sku, "qty": 1})
            why.append(f"seed {leaf(c)}")
            spent += est
            if len(buy) >= lines:
                break
        return buy, f"cold start ({settles} settles seen): " + ", ".join(why)

    # Demand-led: categories by decayed market sales, SKUs that actually sold first.
    hot = [(c, s) for c, s in mm.hot_categories(mem, 40) if c in by_cat and s >= 1]
    hot_sku = dict(mm.hot_skus(mem, 400))
    spent = 0
    for c, score in hot:
        short = shelf.get(c, 1e9) < 96
        if short and score < 30:
            continue                                         # spoils before it sells
        target = max(1, min(CAT_TARGET_MAX if not short else CAT_TARGET_SHORT, math.ceil(score / 6)))
        need = target - have[c]
        if need <= 0:
            continue
        skus = sorted(by_cat[c], key=lambda s: -hot_sku.get(s, 0))
        if not hot_sku.get(skus[0]):
            skus = nearest_median(by_cat[c], med.get(c, 20_000), cat)
        for sku in skus[:2]:
            lp = cat[sku]["list_price"]
            if lp > MAX_LIST and score < 5:
                continue
            if surge.get(sku, 1.0) > SURGE_MAX or any(b["sku"] == sku for b in buy) or underwater(sku) \
                    or late_food(sku):
                continue
            qty = min(need, LINE_QTY_MAX, max(1, int((room - spent) / (lp * EST_COST))),
                      max(1, int(0.5 * LINE_SPEND_SHARE * cap / (lp * EST_COST))))   # dry run: 6 sofas, 2M
            est = lp * EST_COST * qty
            if qty < 1 or spent + est > room:
                continue
            buy.append({"sku": sku, "qty": qty})
            why.append(f"{leaf(c)} x{qty} (score {score:.1f}, have {have[c]}, sku sold {hot_sku.get(sku, 0):.1f})")
            have[c] += qty
            spent += est
            need -= qty
            if need <= 0 or len(buy) >= demand_lines:
                break
        if len(buy) >= demand_lines:
            break
    # Breadth: a shop with nothing in an order's category is not shown to that buyer at all.
    if len(buy) < lines and spent < room:
        wide = [d for d in demand if not have[d["category"]] and by_cat.get(d["category"])
                and shelf.get(d["category"], 1e9) >= BREADTH_MIN_SHELF_H and d["median_price"] <= BREADTH_MAX_LIST]
        wide.sort(key=lambda d: (-d["skus"], d["median_price"]))
        for d in wide:
            sku = next((s for s in nearest_median(by_cat[d["category"]], d["median_price"], cat)
                        if surge.get(s, 1.0) <= SURGE_MAX and not any(b["sku"] == s for b in buy)), None)
            if sku is None:
                continue
            est = cat[sku]["list_price"] * EST_COST
            if spent + est > room:
                continue
            buy.append({"sku": sku, "qty": 1})
            why.append(f"breadth {leaf(d['category'])}")
            spent += est
            if len(buy) >= lines:
                break
    return buy, "demand: " + "; ".join(why) if why else "demand: every hot category is stocked"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    a = ap.parse_args()
    now = time.time()
    inv = read(a.state, "inventory.json", {})
    demand = read(a.state, "demand.json", [])
    out = {"buy": []}
    try:
        mem = mm.update(a.state, log=say)
    except Exception as e:                                   # noqa: BLE001 - stale memory beats none
        say(f"[restock] memory update failed: {type(e).__name__}: {e}")
        mem = mm.load()
    if inv.get("round", 0) < 3 or not demand:
        reason = "not Round 3 yet"
    elif (inv.get("balance") or 0) < inv.get("bankrupt_line", 0) and inv.get("bankruptcies_left"):
        sellable = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
        if len(sellable) < 3:
            out["bankrupt"] = True
        reason = f"below the bankruptcy line with {len(sellable)} units left"
    else:
        cat = mm.catalog(a.state)
        out["buy"], reason = plan(inv, demand, cat, mem, now)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "balance": inv.get("balance"),
                            "units": len(inv.get("units", [])), "buy": out["buy"], "why": reason},
                           ensure_ascii=False) + "\n")
    say(f"[restock] {reason}")
    try:
        import forum
        forum.tick(a.state, mem)
    except Exception as e:                                   # noqa: BLE001 - the forum never blocks a restock
        say(f"[forum] {type(e).__name__}: {e}")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
