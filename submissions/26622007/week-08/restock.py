"""The kit's restock hook (Round 3, every 60 s): prints {"buy": [{"sku", "qty"}]}, and "bankrupt": true when stuck.

    python restock.py --state .market

The first stock, before the ledger says what sells: every category gets a few units of the SKU nearest its median
price, since an order's acceptable SKUs lie around a reference price. Food only with a long shelf life (no storage
fee and it outlasts the round), goods only cheap ones (a tied-up sofa pays storage). A sold unit is bought again.

Once market_model.py has seen enough deals: SKUs ranked by the profit they should bring me per hour (reachable
deals per hour x my share x margin), stocked for cover_hours of expected sales, less for food that would not last.
A few lines still go to categories that sell where I have nothing, so a change in demand can be noticed.
If the ledger cannot be read, the shop keeps using what it has, or the first-stock rule.
pricing.json "restock" holds the numbers. They start cautious (one unit per new category, no food until the ledger
shows it sells, a cap on the stock held, goods that break often left out) and the meta-harness widens them as Round 3's
own numbers allow. guard() applies them to every line, the agent's too; hard_limits.py keeps the balance clear of the
bankruptcy line whatever the settings say.

That formula is a proposal. With a harness set, a Luna agent decides every agent_every_minutes: it gets the proposal,
looks at what it wants through the tools (agent_loop.py), and answers with what to buy and the calls it relied on. Its
lines wait in .market/restock_queue.json and go out max_lines per run. Without a decision the formula stands.
"""
import argparse
import collections
import json
import math
import os
import random
import sys
import time

import agent_loop
import agent_memory
import decision_memory
import forum
import hard_limits
import market_model
import tool_forge
from harness import HarnessError

WORST_COST = 0.75      # a unit's cost is at most this share of its list price (A 55-75%)
EXPECTED_COST = {"food": 0.50, "goods": 0.59}      # a unit's expected cost share, from the published grade odds
FOOD_SELL_WITHIN = 0.45                            # share of shelf life to sell in: below it a unit is grade C
MAX_LOT = 20                                       # the most one supply line can buy
DEFAULTS = {"keep_cash": 1500000, "target_per_category": 2, "min_shelf_hours": 720, "goods_max_price": 50000,
            "max_lines": 10, "cover_hours": 12, "max_units_per_sku": 20, "max_sku_share": 0.1, "explore_lines": 2,
            "min_expected": 1.0, "agent_every_minutes": 10, "agent_max_lines": 30, "round_ends_at": 1794927540, "no_explore_hours": 48, "end_cover": 0.5}


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


def plan(inv, catalog, demand, cfg, model=None, breaks=None):
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
    if cfg.get("food_explore_qty") == 0:       # cautious start: food is bought only once the ledger shows it sells
        rows = [d for d in rows if d.get("kind") != "food"]
    random.shuffle(rows)
    rows.sort(key=lambda d: have[d["category"]])       # the emptiest categories first
    room = 0 if hours_left < cfg["no_explore_hours"] else cfg["explore_lines"] if active else cfg["max_lines"]
    for d in rows[:room]:
        sku, x = min(skus[d["category"]], key=lambda s: abs(s[1]["list_price"] - d.get("median_price", 0)))
        if sku not in picked:
            qty = cfg["target_per_category"] - have[d["category"]]
            if x.get("kind") == "food" and cfg.get("food_explore_qty") is not None:
                qty = min(qty, cfg["food_explore_qty"])
            if qty > 0:
                picks.append((0, sku, qty))
    return {"buy": guard([{"sku": sku, "qty": qty} for _, sku, qty in picks], inv, catalog, cfg, breaks)}


def guard(buy, inv, catalog, cfg, breaks=None):
    """The lines that keep to the settings, whoever proposed them: food per line, goods that break too often, the cost of
    the stock held, the cash kept, and hard_limits' line above bankruptcy. A line that does not fit is shrunk or dropped."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    now = time.time()
    held = sum(u.get("cost") or 0 for u in units if not (u.get("expires_at") and u["expires_at"] <= now))
    room = cfg.get("max_stock_cost", float("inf")) - held
    spare = min(balance - cfg["keep_cash"], hard_limits.spendable(balance))
    out = []
    for b in buy:
        x = catalog.get(b["sku"]) or {}
        qty = min(b["qty"], MAX_LOT)
        if x.get("kind") == "food" and cfg.get("food_max_qty") is not None:
            qty = min(qty, cfg["food_max_qty"])
        if x.get("kind") == "goods" and breaks and cfg.get("max_break") is not None:
            chance = market_model.break_chance(breaks, b["sku"])
            if chance is not None and chance > cfg["max_break"]:
                print(f"[restock] guard: {b['sku']} breaks {chance:.0%} > {cfg['max_break']:.0%}, left out", file=sys.stderr)
                continue
        unit = (x.get("list_price") or float("inf")) * WORST_COST
        qty = min(qty, int(min(spare, room) // unit) if unit < float("inf") else 0)
        if qty < 1:
            continue
        out.append({"sku": b["sku"], "qty": qty})
        spare, room = spare - unit * qty, room - unit * qty
    return out


def read_breaks(state, catalog):
    try:
        return market_model.break_stats(market_model.load_rows(state), catalog)
    except Exception:   # noqa: BLE001 - without break rates the guard simply does not use them
        return None


def read_model(state, catalog, cfg):
    """The demand model, or None when it cannot be built: a restock must never fail because of the ledger."""
    me = os.environ.get("SHOP_MARKET_NAME", "nocha12")
    shops = market_model.my_shops()
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
    tmp = os.path.join(state, "briefing.json.tmp")     # what the seller reads at each turn of a talk, when it cannot wait for tools
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"at": int(time.time()), "skus": model["skus"]}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(state, "briefing.json"))
    return model


GOAL = """You decide what stock a shop buys now. Profit (balance at the end of the round, so unsold stock is a loss),
revenue and stars are what count. Round ends at unix time {ends}, now is {now}. A unit costs 25-75% of list price by grade;
food past its date is thrown away at cost; goods in stock cost 1% of cost per day. A shop with no stock cannot sell at all,
and other shops' deals show in the ledger whether you stock or not. Up to {lines} lines, qty 1-20 each.
c0 is the formula's proposal for this state; follow it, change it or ignore it, as the evidence says. c1 is how your own earlier
stock decisions turned out: what sold, what is left, what is gone unsold. Learn from it: a judgment that failed should not repeat.
c2 is the shop's own memory: long-term notes, lessons from earlier mistakes that match this state, and recent days. The
code keeps every line inside the shop's current limits (pricing.json), which start cautious and widen as results earn it."""


def keys_for(proposal, inv, catalog):
    """What this decision is about, for picking memories: the SKUs and categories proposed or held, and food/goods."""
    skus = {b["sku"] for b in proposal.get("buy", [])} | {u.get("sku") for u in inv.get("units") or []}
    out = set()
    for sku in skus:
        x = catalog.get(sku) or {}
        out |= {sku, x.get("category") or "", x.get("kind") or ""}
    return sorted(k for k in out if k)

FINAL = {"type": "object", "additionalProperties": False, "required": ["buy", "why"],
         "properties": {"why": {"type": "string"},
                        "buy": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False, "required": ["sku", "qty"],
                            "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}}}}}


def clean_lines(buy, catalog, limit):
    """Local check of the agent's answer: known SKUs, a lot size the market takes, each SKU once."""
    out, seen = [], set()
    for b in buy:
        if b["sku"] in catalog and b["sku"] not in seen and 1 <= b["qty"] <= MAX_LOT:
            out.append({"sku": b["sku"], "qty": b["qty"]})
            seen.add(b["sku"])
    return out[:limit]


def agent_turn(state, inv, catalog, demand, cfg, model, decide=agent_loop.decide):
    """The lines to send now: from the agent's queue, after a new decision when one is due. None: use the formula."""
    path = os.path.join(state, "restock_queue.json")
    q = load(path, {"at": 0, "lines": []})
    if time.time() - q["at"] >= cfg["agent_every_minutes"] * 60:
        proposal = plan(inv, catalog, demand, {**cfg, "max_lines": cfg["agent_max_lines"]}, model)
        results = decision_memory.outcomes(state, inv)
        agent_memory.learn_from_outcomes(state, results, catalog)
        try:
            final = decide(GOAL.format(ends=cfg.get("round_ends_at"), now=int(time.time()), lines=cfg["agent_max_lines"]), FINAL,
                           state, "restock", {"c0": json.dumps(proposal, ensure_ascii=False),
                                       "c1": json.dumps(results, ensure_ascii=False),
                                       "c2": agent_memory.select(state, keys_for(proposal, inv, catalog))})
        except HarnessError as e:
            print(f"[restock] agent failed: {e}", file=sys.stderr, flush=True)
            return None
        if final is None:
            print("[restock] agent gave no evidenced decision", file=sys.stderr, flush=True)
            return None
        q = {"at": time.time(), "lines": clean_lines(final["buy"], catalog, cfg["agent_max_lines"])}
        agent_memory.daily(state, "restock", f"bought {q['lines']} because {final.get('why', '')[:300]}")
    now, q["lines"] = q["lines"][:cfg["max_lines"]], q["lines"][cfg["max_lines"]:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, path)
    return now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    inv = load(os.path.join(args.state, "inventory.json"), {})
    cfg = {**DEFAULTS, **load("pricing.json", {}).get("restock", {})}
    catalog = load(os.path.join(args.state, "catalog.json"), {})
    demand, model = load(os.path.join(args.state, "demand.json"), []), read_model(args.state, catalog, cfg)
    breaks = read_breaks(args.state, catalog)
    out = plan(inv, catalog, demand, cfg, model, breaks)
    if os.environ.get("HARNESS") and not out.get("bankrupt"):
        lines = agent_turn(args.state, inv, catalog, demand, cfg, model)
        if lines is not None:
            out = {"buy": guard(lines, inv, catalog, cfg, breaks)}
    agent_memory.maybe_consolidate(args.state)
    if os.environ.get("HARNESS"):
        forum.maybe_run(args.state)     # the kit runs this hook once a minute in Round 3; forum.py keeps its own pace
        tool_forge.maybe_run(args.state)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
