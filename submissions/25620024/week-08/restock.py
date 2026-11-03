"""Week 08 — restock.py: the kit's restock hook (every 60 s in Round 3). Little stock, fast turnover.

    python restock.py --state .market

Order of buying, each line only while cash stays above the reserve:
  1. reservations: SKUs a buyer asked our shop for that we did not have (the shop wrote them to
     private/reserve.jsonl); one unit each, so the next buyer gets it
  2. demand: target = rate per hour x cover hours (market settles plus unmet asks, decayed); the hero SKUs
     (selling fastest) get a deeper target, they are the ones sold lowest in the market
  3. display: one unit in categories with demand but no stock, so buyers see us in shops.json; long shelf life only
Food that lasts 48 h or less is bought only once its demand is proven.
Supply opens for 3 minutes every 20 (inventory.json "restock": open, opens_at, closes_at; rule added 2026-11-03).
Outside a window the run prints no lines (the kit would wait up to 90 s per line, then skip it); a minute before
a window opens it plans, so the kit's first line goes out as the window opens. What a window buys has to last to
the next one, so reservations wait for it and are kept an hour.
Prints {"buy": [{"sku": ..., "qty": ...}]}; the kit buys at most 10 lines a run.
"""
import argparse
import json
import math
import os
import time
from pathlib import Path

import market_model

THETA = Path(os.environ.get("SHOP_THETA", Path(__file__).parent / "private" / "shop_theta.json"))
RESERVE = Path(__file__).parent / "private" / "reserve.jsonl"
DEFAULTS = {"cash_reserve": 0.35, "max_list": 300000, "cover_h": {"food": 6, "goods": 24}, "hero_depth": 2.0,
            "heroes": 8, "max_qty": 20, "display_cats": 40, "short_shelf_rate": 0.5, "cost_guess": 0.6,
            # first window of Round 3 (2026-11-03 21:05) bought ~1.9M on 3 market settles per SKU: the market's
            # demand counted as ours, and no floor on evidence. Now: our share of the market rate, a minimum of
            # settles before buying for demand, a cap on goods we have not sold yet, and a spend cap per window
            "share_prior": 0.2, "min_settles": 3, "goods_cap_unproven": 3, "proven_sales": 2,
            "window_budget": 0.08}
STATE = Path(__file__).parent / "private" / "restock_state.json"


def theta():
    try:
        return {**DEFAULTS, **json.loads(THETA.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def load(md, name, default):
    try:
        return json.loads((md / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def plan(md, c, t, now=None):
    now = now or time.time()
    inv, cat, demand = load(md, "inventory.json", {}), market_model.catalog(md), load(md, "demand.json", [])
    if isinstance(demand, dict):
        demand = demand.get("categories", [])
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    stock = {}
    for u in units:
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
    start = inv.get("start") or 5_000_000
    cash = (inv.get("balance") or 0) - t["cash_reserve"] * start
    w = inv.get("restock") or {}                              # spend cap per supply window
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    wid = str(w.get("closes_at") or int(now // 1200))
    spent = st.get("spent", 0) if st.get("window") == wid else 0
    cash = min(cash, t["window_budget"] * start - spent)
    n_mkt = dict(c.execute("select sku, count(*) from settles where ts>? group by sku", (now - 24 * 3600,)).fetchall())
    n_ours = dict(c.execute("select sku, count(*) from quotes where outcome='deal' group by sku").fetchall())
    by_sku, by_cat = market_model.rates(c, now=now)
    heroes = [k for k, _ in sorted(by_sku.items(), key=lambda kv: -kv[1])[:t["heroes"]]]
    buy, why = {}, []

    def add(sku, qty, reason):
        nonlocal cash
        r = cat.get(sku)
        if not r or qty <= 0 or r["list_price"] > t["max_list"]:
            return
        qty = min(qty, t["max_qty"] - buy.get(sku, 0))
        cost = r["list_price"] * t["cost_guess"] * qty
        if qty <= 0 or cost > cash or len(buy) >= 10 and sku not in buy:
            return
        buy[sku] = buy.get(sku, 0) + qty
        cash -= cost
        why.append(f"{qty} x {r['name'][:24]} ({reason})")

    if RESERVE.exists():                                      # 1. reservations, the last 30 minutes
        for line in RESERVE.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                rsv = json.loads(line)
            except ValueError:
                continue
            if now - rsv.get("ts", 0) < 3600 and not stock.get(rsv.get("sku")) and rsv.get("sku") not in buy:
                add(rsv["sku"], 1, "reserved")
    for sku, rate in sorted(by_sku.items(), key=lambda kv: -kv[1]):   # 2. demand
        r = cat.get(sku)
        if not r:
            continue
        kind = r.get("kind", "food")
        if kind == "food" and (r.get("shelf_hours") or 999) <= 48 and rate < t["short_shelf_rate"]:
            continue
        if n_mkt.get(sku, 0) < t["min_settles"]:
            continue                                          # too little evidence: the display step covers it
        ours = n_ours.get(sku, 0)
        share = min(1.0, max(0.05, ours / n_mkt[sku])) if ours >= t["proven_sales"] else t["share_prior"]
        target = math.ceil(rate * share * t["cover_h"].get(kind, 12) * (t["hero_depth"] if sku in heroes else 1))
        if kind == "goods" and ours < t["proven_sales"]:
            target = min(target, t["goods_cap_unproven"])
        add(sku, target - stock.get(sku, 0), f"{rate:.2f}/h x share {share:.2f}" + (" hero" if sku in heroes else ""))
    have_cat = {(cat.get(s) or {}).get("category") for s in stock} | {(cat.get(s) or {}).get("category") for s in buy}
    cats = sorted(by_cat, key=lambda k: -by_cat[k]) or [d["category"] for d in demand]
    for category in cats[:t["display_cats"]]:                 # 3. display
        if category in have_cat:
            continue
        med = next((d.get("median_price") for d in demand if d.get("category") == category), None)
        pool = [r for r in cat.values() if r.get("category") == category and r["list_price"] <= t["max_list"]
                and (r.get("kind") == "goods" or (r.get("shelf_hours") or 0) >= 168)]
        if pool:
            r = min(pool, key=lambda r: abs(r["list_price"] - (med or r["list_price"])) - 1e6 * (r["sku"] in by_sku))
            add(r["sku"], 1, "display")
    st = {"window": wid, "spent": spent + sum(cat[k]["list_price"] * t["cost_guess"] * v for k, v in buy.items())}
    return [{"sku": k, "qty": v} for k, v in buy.items()], why, cash, st


def window(md, now=None):
    """(buy now?, why): the supply window from inventory.json; no "restock" field means no windows."""
    now = now or time.time()
    w = load(md, "inventory.json", {}).get("restock")
    if not isinstance(w, dict):
        return True, "no supply windows announced"
    if w.get("open"):
        return True, f"window open until {time.strftime('%H:%M:%S', time.localtime(w.get('closes_at') or now))}"
    opens = w.get("opens_at") or 0
    if opens - now <= 60:
        return True, f"window opens in {max(0, opens - now):.0f} s"
    return False, f"window closed, opens at {time.strftime('%H:%M:%S', time.localtime(opens))} (in {(opens - now) / 60:.0f} min)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    c = market_model.db()
    try:
        market_model.sync(c, md, budget_s=15)
    except Exception as e:
        print(f"[restock] ledger sync failed: {type(e).__name__}: {e}", flush=True)
    buy, why, cash, st = plan(md, c, theta())
    go, when = window(md)
    if not go:
        buy, why = [], [f"waiting: {when}"]
    else:
        why = [when] + why
        try:
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(json.dumps(st), encoding="utf-8")
        except OSError:
            pass
    try:                                   # speak first: the forum runs here too, with no order behind it
        import forum
        said = forum.tick(md, c)
        if said:
            print(f"[forum] {sum('post' in x for x in said)} posts, {sum('vote' in x for x in said)} votes to the outbox",
                  flush=True)
    except Exception as e:
        print(f"[forum] skipped: {type(e).__name__}: {e}", flush=True)
    print(f"[restock] {len(buy)} lines, cash above reserve after: {cash:,.0f}; " + "; ".join(why[:10]), flush=True)
    print(json.dumps({"buy": buy}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
