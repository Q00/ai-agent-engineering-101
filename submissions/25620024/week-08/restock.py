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
            "window_budget": 0.08,
            # regulars: buyers with 2+ settled buys from us in 48 h; keep their categories in stock first
            "regular_min_buys": 2, "regular_depth": 2,
            # the marketing plan's picks (high-ticket entries, exclusives) get their own budget per window, above
            # the general cap: leaders sell 88k-210k per deal in bedding and mattresses, we sold 10k (2026-11-03)
            "pick_window_budget": 0.08}
STATE = Path(__file__).parent / "private" / "restock_state.json"


def theta():
    try:
        t = {**DEFAULTS, **json.loads(THETA.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        t = dict(DEFAULTS)
    try:                                                      # the finance team's limits bind every run
        import finance_agent
        fl = finance_agent.limits()
    except Exception:
        fl = {}
    if fl:
        t["cash_reserve"] = max(t["cash_reserve"], fl.get("cash_floor", 0))
        t["window_budget"] = min(t["window_budget"], fl.get("window_spend", t["window_budget"]))
        t["pick_window_budget"] = min(t["pick_window_budget"], fl.get("window_spend", t["pick_window_budget"]))
    return t


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

    def add(sku, qty, reason, any_price=False):
        nonlocal cash
        r = cat.get(sku)
        if not r or qty <= 0 or (r["list_price"] > t["max_list"] and not any_price):
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
    regular, wants = market_model.regulars(c, t["regular_min_buys"])          # 1b. what our regulars buy
    have_cat = {(cat.get(s) or {}).get("category") for s in stock}
    for category, skus in sorted(wants.items(), key=lambda kv: -sum(kv[1].values())):
        if category in have_cat:
            continue                                          # a regular coming back finds it in stock already
        sku = max(skus, key=skus.get)
        add(sku, min(t["regular_depth"], sum(skus.values())), f"regulars bought {sum(skus.values())} here")
    if regular:
        why.append(f"{len(regular)} regulars ({sum(regular.values())} buys)")
    try:                                                      # 1c. the marketing team's picks
        import promo
        mk = dict(promo.plan().get("restock") or {}, _veto=promo.plan().get("scm_veto"))   # sales team is on top
    except Exception:
        mk = {}
    general = cash
    cash = min((inv.get("balance") or 0) - t["cash_reserve"] * start, t["pick_window_budget"] * start - spent)
    for x in promo.hq_orders():                               # 1b. headquarters' direct orders: first, never vetoed
        add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0), "HQ order", any_price=True)
    for x in mk.get("skus") or []:
        add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0) - buy.get(x.get("sku"), 0),
            "marketing pick", any_price=True)
    try:                                                      # the SCM team's orders (Codex, hourly)
        import scm_agent
        veto = set((mk.get("_veto") or {}).get("cancel_orders") or [])
        for x in [x for x in scm_agent.scm_plan().get("orders") or [] if x.get("sku") not in veto]:
            add(x.get("sku"), int(x.get("qty") or 0) - stock.get(x.get("sku"), 0) - buy.get(x.get("sku"), 0),
                "scm order", any_price=True)
    except Exception as e:
        why.append(f"scm plan not read: {type(e).__name__}")
    cash = min(cash, general)                                 # the rest of the run keeps the general cap
    if mk.get("mode") == "focus":                             # marketing: only regulars and its picks for now
        return [{"sku": k, "qty": v} for k, v in buy.items()], why + ["marketing focus mode"], cash, \
            {"window": wid, "spent": spent + sum(cat[k]["list_price"] * t["cost_guess"] * v for k, v in buy.items())}
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


HARD_FLOOR = 700_000         # HQ 11-04: never go bankrupt (the line is 500,000): even at the worst cost a supply
WORST_COST = 0.75            # draw (grade A: 75% of list) leaves at least 700,000


def never_bankrupt(md, buy):
    """Drop lines from the end until the run, at the worst cost the market can draw, leaves HARD_FLOOR."""
    try:
        inv = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], len(buy)
    cat = market_model.catalog(md)
    bal, kept = inv.get("balance") or 0, []
    for x in buy:
        worst = (cat.get(x["sku"]) or {}).get("list_price", 0) * WORST_COST * x["qty"]
        if bal - worst >= HARD_FLOOR:
            kept.append(x)
            bal -= worst
    return kept, len(buy) - len(kept)


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
    buy, cut = never_bankrupt(md, buy)
    if cut:
        why = [f"bankruptcy guard dropped {cut}"] + why
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
    try:                                   # the Enuri team's book for the buyer: passive, rebuilt every tick_s
        import enuri
        card = enuri.tick(md, c)
        if card is not None:
            acc = enuri.book().get("enuri_accountability") or {}
            print(f"[enuri] book rebuilt: {card.get('deals', 0)} deals scored, mae {card.get('mae')}, "
                  f"missed {acc.get('missed', 0)}, wrong {acc.get('wrong', 0)}, lost {acc.get('lost', 0)} (24 h)", flush=True)
    except Exception as e:
        print(f"[enuri] skipped: {type(e).__name__}: {e}", flush=True)
    print(f"[restock] {len(buy)} lines, cash above reserve after: {cash:,.0f}; " + "; ".join(why[:10]), flush=True)
    print(json.dumps({"buy": buy}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
