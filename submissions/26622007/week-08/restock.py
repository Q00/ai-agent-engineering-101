"""The kit's restock hook (Round 3, every 60 s): prints {"buy": [{"sku", "qty"}]}, and "bankrupt": true when stuck.

    python restock.py --state .market

The first stock, before the ledger says what sells: every category gets a few units of the SKU nearest its median
price, since an order's acceptable SKUs lie around a reference price. Food only with a long shelf life (no storage
fee and it outlasts the round), goods only cheap ones (a tied-up sofa pays storage). A sold unit is bought again.

Once market_model.py has seen enough deals: SKUs ranked by the profit they should bring me per hour (reachable
deals per hour x my share x margin), stocked for cover_hours of expected sales, less for food that would not last.
A few lines still go to categories that sell where I have nothing, so a change in demand can be noticed.
If the ledger cannot be read, the shop keeps using what it has, or the first-stock rule.
pricing.json "restock" holds the numbers. No harness: this is arithmetic.
"""
import argparse
import collections
import json
import math
import os
import random
import sys
import time

import market_model

WORST_COST = 0.75      # a unit's cost is at most this share of its list price (A 55-75%)
EXPECTED_COST = {"food": 0.50, "goods": 0.59}      # a unit's expected cost share, from the published grade odds
FOOD_SELL_WITHIN = 0.45                            # share of shelf life to sell in: below it a unit is grade C
MAX_LOT = 20                                       # the most one supply line can buy
DEFAULTS = {"keep_cash": 1500000, "target_per_category": 2, "min_shelf_hours": 720, "goods_max_price": 50000,
            "max_lines": 10, "cover_hours": 12, "max_units_per_sku": 20, "max_sku_share": 0.1, "explore_lines": 2,
            "min_expected": 1.0, "round_ends_at": 1794927540, "no_explore_hours": 48, "end_cover": 0.5}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def wanted(model, catalog, have_sku, balance, cfg, hours_left=float("inf")):
    """[(profit per hour, sku, qty)] the demand model asks for, best first. Near the end of the round it stocks only
    what should sell in time: profit counts the balance, so a unit left on the last day is a loss."""
    out = []
    for sku, m in model["skus"].items():
        x = catalog.get(sku) or {}
        price = x.get("list_price")
        if not price:
            continue
        margin = price * (m["price_ratio"] - EXPECTED_COST.get(x.get("kind"), EXPECTED_COST["goods"]))
        rate = m["rate"] * m["my_share"]
        cover = min(cfg["cover_hours"], hours_left * cfg["end_cover"])
        if x.get("kind") == "food" and x.get("shelf_hours"):
            cover = min(cover, x["shelf_hours"] * FOOD_SELL_WITHIN)
        if margin <= 0 or rate * cover < cfg["min_expected"]:
            continue
        target = min(cfg["max_units_per_sku"], math.ceil(rate * cover),
                     int(balance * cfg["max_sku_share"] / (price * WORST_COST)))
        if target > have_sku[sku]:
            out.append((rate * margin, sku, min(MAX_LOT, target - have_sku[sku])))
    return sorted(out, reverse=True)


def plan(inv, catalog, demand, cfg, model=None):
    """The hook's answer for this state."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        return {"bankrupt": True, "buy": []}
    now = time.time()
    live = [u for u in units if u.get("grade") != "expired" and not (u.get("expires_at") and u["expires_at"] <= now)]
    have = collections.Counter(u.get("category") for u in live)
    have_sku = collections.Counter(u.get("sku") for u in live)
    active = bool(model and model.get("active"))
    hours_left = (cfg["round_ends_at"] - now) / 3600 if cfg.get("round_ends_at") else float("inf")
    picks = wanted(model, catalog, have_sku, balance, cfg, hours_left)[:cfg["max_lines"] - cfg["explore_lines"]] if active else []
    picked = {sku for _, sku, _ in picks}
    skus = collections.defaultdict(list)
    for sku, x in catalog.items():
        if x.get("kind") == "food":
            if (x.get("shelf_hours") or 0) >= cfg["min_shelf_hours"]:
                skus[x["category"]].append((sku, x))
        elif x.get("list_price", float("inf")) <= cfg["goods_max_price"]:
            skus[x["category"]].append((sku, x))
    rows = [d for d in demand if skus.get(d["category"]) and have[d["category"]] < cfg["target_per_category"]]
    if active:      # demand is known: only categories that really sell and where I have nothing at all
        rows = [d for d in rows if d["category"] in model["cats"] and not have[d["category"]]]
    random.shuffle(rows)
    rows.sort(key=lambda d: have[d["category"]])       # the emptiest categories first
    room = 0 if hours_left < cfg["no_explore_hours"] else cfg["explore_lines"] if active else cfg["max_lines"]
    for d in rows[:room]:
        sku, x = min(skus[d["category"]], key=lambda s: abs(s[1]["list_price"] - d.get("median_price", 0)))
        if sku not in picked:
            picks.append((0, sku, cfg["target_per_category"] - have[d["category"]]))
    spare, buy = balance - cfg["keep_cash"], []
    for _, sku, qty in picks:
        cost = catalog[sku]["list_price"] * WORST_COST * qty
        if cost <= spare:
            buy.append({"sku": sku, "qty": qty})
            spare -= cost
    return {"buy": buy}


def read_model(state, catalog, cfg):
    """The demand model, or None when it cannot be built: a restock must never fail because of the ledger."""
    me = os.environ.get("SHOP_MARKET_NAME", "nocha12")
    shops = {os.environ[k].rstrip("/") for k in ("RENDER_EXTERNAL_URL", "SHOP_PUBLIC_URL") if os.environ.get(k)}
    try:
        added = market_model.sync(state, max_pages=cfg.get("max_pages", market_model.DEFAULTS["max_pages"]))
        model = market_model.estimate(market_model.load_rows(state), catalog, {me}, shops, cfg=cfg)
    except Exception as e:      # noqa: BLE001 - any ledger trouble means the same thing here
        print(f"[restock] demand model unavailable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None
    top = sorted(model["skus"].items(), key=lambda kv: -kv[1]["rate"])[:5]
    print(f"[restock] model {'on' if model['active'] else 'warming up'}: {model['n']} reachable deals (+{added} rows), "
          f"{len(model['skus'])} SKUs, {len(model['captive'])} captive buyers, top "
          + ", ".join(f"{k} {v['rate']:.2f}/h" for k, v in top), file=sys.stderr, flush=True)
    with open(os.path.join(state, "demand_model.json"), "w", encoding="utf-8") as f:
        json.dump({**model, "skus": dict(top)}, f, ensure_ascii=False)
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    inv = load(os.path.join(args.state, "inventory.json"), {})
    cfg = {**DEFAULTS, **load("pricing.json", {}).get("restock", {})}
    catalog = load(os.path.join(args.state, "catalog.json"), {})
    out = plan(inv, catalog, load(os.path.join(args.state, "demand.json"), []), cfg, read_model(args.state, catalog, cfg))
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
