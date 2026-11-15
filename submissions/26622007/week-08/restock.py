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
import contextlib
import io
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
import loss_watch
import measure
import market_model
import opening
import profit_price
import restock_breadth
import restock_first_stock
import restock_first_slot
import restock_demand_catchup
import restock_food_stockout
import restock_fresh_slots
import restock_sellable_slots
import restock_turnover_targets
import restock_window_fast
import rival_vote
import shop_forum
import shop_memory
import tool_forge
from harness import HarnessError

WORST_COST = 0.75      # a unit's cost is at most this share of its list price (A 55-75%)
EXPECTED_COST = {"food": 0.50, "goods": 0.59}      # a unit's expected cost share, from the published grade odds
FOOD_SELL_WITHIN = 0.45                            # share of shelf life to sell in: below it a unit is grade C
MAX_LOT = 20                                       # the most one supply line can buy
# Patch 3.2 surge: past 50 units of a product bought by the whole market in an hour, each unit costs 0.2% more, at most
# +50%. inventory.json's surge lists the products under it with the factor the next unit pays. On 11/05 49 of our units
# cost more than 0.75 x list (the most 0.926), so the worst cost the caps reserve follows the factor (GPT Pro 11/05 §2).
SURGE_STEP, SURGE_MAX = 0.002, 1.5
SURGE_SLACK = 0.05        # other shops' units bought between this run and the supply: 25 units at 0.2%
SURGE_HALF, SURGE_SKIP = 1.20, 1.30   # from 1.20 a SKU off the proven / priority lists buys half; from 1.30 none
DEFAULTS = {"keep_cash": 1500000, "target_per_category": 2, "min_shelf_hours": 720, "goods_max_price": 50000,
            "max_lines": 10, "cover_hours": 12, "max_units_per_sku": 20, "max_sku_share": 0.1, "explore_lines": 2,
            "min_expected": 1.0, "agent_every_minutes": 10, "agent_max_lines": 30, "round_ends_at": 1794927540, "no_explore_hours": 48, "end_cover": 0.5,
            "band_low": 0, "band_high": float("inf"), "food_max_price": float("inf")}


def surge(inv):
    """{sku: factor the next unit pays} from inventory.json's surge; a SKU not listed pays the plain cost."""
    rows = inv.get("surge") if isinstance(inv, dict) else None
    out = {}
    for r in rows if isinstance(rows, list) else ():
        if isinstance(r, dict) and isinstance(r.get("sku"), str) and isinstance(r.get("factor"), (int, float)) \
                and not isinstance(r.get("factor"), bool):
            out[r["sku"]] = min(max(float(r["factor"]), 1.0), SURGE_MAX)
    return out


def worst(sku, qty, catalog, factors=None):
    """The most one unit of a qty-unit line can cost: grade A's 0.75 x list, times the surge factor its last unit pays
    with the slack for other shops' buys, at most 1.5."""
    price = (catalog.get(sku) or {}).get("list_price") or 0
    f = (factors or {}).get(sku)
    return price * WORST_COST * (1.0 if f is None else min(SURGE_MAX, f + SURGE_STEP * max(qty - 1, 0) + SURGE_SLACK))


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def wanted(model, catalog, have_sku, balance, cfg, hours_left=float("inf"), factors=None):
    """[(profit per hour per won of stock, sku, qty)] the demand model asks for, best first. The stock-cost caps in guard()
    are the binding limit, so lines are ranked by what each won tied up in stock earns: ranked by profit per hour alone,
    three mattresses filled the whole cap and the shop held 6 units in 5 categories while buyers see only shops that
    stock the ordered category (shops.json offers). Near the end of the round it stocks only what should sell in time:
    profit counts the balance, so a unit left on the last day is a loss."""
    out = []
    for sku, m in model["skus"].items():
        x = catalog.get(sku) or {}
        price = x.get("list_price")
        if not price:
            continue
        rate = m["rate"] * m["my_share"]
        cover = min(cfg["cover_hours"], hours_left * cfg["end_cover"])
        # expected net per unit sold (GPT Pro §1.5): price x (1 - refund chance under my 6 h warranty) - cost - storage
        cost = price * EXPECTED_COST.get(x.get("kind"), EXPECTED_COST["goods"]) * (factors or {}).get(sku, 1.0)
        refund = (cfg.get("prior_break") or 0) if x.get("kind") == "goods" else 0
        storage = cost * 0.01 * cover / 24 if x.get("kind") == "goods" else 0
        margin = price * m["price_ratio"] * (1 - refund) - cost - storage
        if x.get("kind") == "food" and x.get("shelf_hours"):
            cover = min(cover, x["shelf_hours"] * FOOD_SELL_WITHIN)
        if margin <= 0 or rate * cover < cfg["min_expected"]:
            continue
        target = min(cfg["max_units_per_sku"], math.ceil(rate * cover),
                     int(balance * cfg["max_sku_share"] / (worst(sku, 1, catalog, factors) or price * WORST_COST)))
        if target > have_sku[sku]:
            out.append((rate * margin / cost, sku, min(MAX_LOT, target - have_sku[sku])))
    return sorted(out, reverse=True)


def listed(cfg):
    """high_price_proven_skus then priority_skus, in order, strings only (a malformed setting lists nothing, never fails)."""
    out = []
    for key in ("high_price_proven_skus", "priority_skus"):
        v = cfg.get(key)
        for s in v if isinstance(v, (list, tuple)) else ():
            if isinstance(s, str) and s not in out:
                out.append(s)
    return out


def slot_count(live, cfg, catalog, factors, now):
    """(Counter, changed): the units of each SKU as its sku_target_units counts them. restock_sellable_slots when its
    switch is on (cfg["sellable_slots"]), else restock_fresh_slots (default off too: every live unit)."""
    if restock_sellable_slots.on(cfg) is not None:
        return restock_sellable_slots.counted(live, cfg, catalog, factors, now)
    return restock_fresh_slots.counted(live, cfg, catalog, factors, now)


def fresh_extra(cfg, inv, catalog, waiting=(), now=None):
    """restock_fresh_slots (default off: empty): the SKUs that hold their target, units on their way included, and are
    asked again only because units of theirs waited. Their lines go after every other line of a run, listed or not,
    the agent's too (plan, urgent_listed, main), so today's lines keep their places in the window's ~14-18 calls
    (independent reviews 8023a19 I1, 798955a I4). Never raises: empty on any trouble."""
    try:
        if restock_sellable_slots.on(cfg) is not None:      # that study orders its refills by net per call instead
            return set()
        now = now or time.time()
        live = [u for u in inv.get("units") or [] if isinstance(u, dict) and not gone(u, now)]
        have = collections.Counter(u.get("sku") for u in live)
        for w in waiting or ():
            have[w.get("sku")] += w.get("qty") or 0
        targets = cfg.get("sku_target_units") if isinstance(cfg.get("sku_target_units"), dict) else {}
        return {s for s in restock_fresh_slots.counted(live, cfg, catalog, surge(inv), now)[1]
                if type(targets.get(s)) is int and have[s] >= targets[s]}
    except Exception:   # noqa: BLE001 - the order of today
        return set()


def urgent_listed(cfg, inv, catalog, waiting=(), model=None):
    """listed(cfg) with the SKUs that have no live unit held or on its way first (food before goods), then the refills
    (food before goods), each in list order. With the demand model the refills go by the hours their stock lasts at
    rate x my_share (or our own last price_hours' sales when faster), lowest first, and those it has no rate for after them in list order: in the 06:23 window (11/07) run 2
    spent two of its ~10 s calls on 3 apples and 1 kimchi (16 and 15 held) and held back G021aae64 (1 held, 7 sold/h)
    and G03d0199f when the window closed; the window ends 2-4 lines short every time, never short of cash.
    A window serves ~14 lines (one about every 10 s): at 04:44 (11/07) the runs refilled SKUs that still held 2-12 units
    while G75f11c19 (8.5 sold/h), G0667d440 and G19a70f63 sat at 0, held back in every window, with ~39M over the cash
    buffer (main 01:30: sold-out food, then the new high-price SKUs, then the fast sellers)."""
    now = time.time()
    live = [u for u in inv.get("units") or [] if isinstance(u, dict) and not gone(u, now)]
    have = collections.Counter(u.get("sku") for u in live)
    for w in waiting or ():
        have[w.get("sku")] += w.get("qty") or 0
    extra = fresh_extra(cfg, inv, catalog, waiting, now)     # default off: none; last among the listed SKUs
    food = lambda s: (catalog.get(s) or {}).get("kind") == "food"
    skus = (model or {}).get("skus") if isinstance(model, dict) else None

    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    def lasts(s):       # hours the held units last; None when the model has no usable rate for the SKU
        m = skus.get(s) if isinstance(skus, dict) else None
        r = m.get("rate") if isinstance(m, dict) else None
        sh = m.get("my_share") if isinstance(m, dict) else None
        if not (num(r) and num(sh) and math.isfinite(r * sh) and r * sh > 0):
            return None
        # our own sales over the model's price_hours when faster: the weighted share lags a day behind a SKU we only
        # began to stock (07:46 window held back G429bd69e, 4 held: 4.3 h at the model, 0.9 h at our 4.3 sold/h)
        mine = m.get("mine_rate")
        return have[s] / max(r * sh, mine if num(mine) and mine > 0 else 0)
    if not isinstance(skus, dict):
        return sorted(listed(cfg), key=lambda s: (s in extra, have[s] > 0, not food(s)))     # stable: list order within each group
    if restock_sellable_slots.on(cfg) is not None:
        # restock_sellable_slots: the empty SKUs first as today, then the refills by what one call nets (need x unit
        # net, highest first), then those the model gives no net for in today's order (Astra 11/15 원인 ②)
        counts = slot_count(live, cfg, catalog, surge(inv), now)[0]
        for w in waiting or ():
            counts[w.get("sku")] += w.get("qty") or 0
        value = {s: restock_sellable_slots.call_value(s, cfg, catalog, model, have, counts) for s in listed(cfg)}
        return sorted(listed(cfg), key=lambda s: (0, not food(s), 0) if have[s] <= 0
                      else (1, 0, -value[s]) if value[s] is not None
                      else (2, 0, lasts(s)) if lasts(s) is not None else (3, not food(s), 0))
    # empty (food first), then refills with a rate by hours left, then refills without one as before (food first)
    return sorted(listed(cfg), key=lambda s: (3, 0, 0) if s in extra else (0, not food(s), 0) if have[s] <= 0
                  else (1, 0, lasts(s)) if lasts(s) is not None else (2, not food(s), 0))


def fill_listed(ranked, model, catalog, have_sku, cfg, factors=None, hours_left=float("inf"), breaks=None):
    """restock.fill_listed: each listed SKU with a sku_target_units target is asked up to it, not to the demand model's
    rate x my_share x cover. The model's share is our own past share, so a shop that lost quotes on price asked for
    1-5 units of goods the market sells 25-56 an hour (G8ec51935: target 10, the model 5, 7 held, 11/06 19:01) and
    held 53M of stock under a 120M cap with 60M cash idle. Only SKUs whose expected margin at the market's price is
    positive (refunds at the SKU's ledger break rate when there is one, else prior_break: at 0.15 G8ec51935, 9% in the
    ledger, came out at -0.4% of list); guard() still applies every cap."""
    if cfg.get("fill_listed") is not True or not isinstance(cfg.get("sku_target_units"), dict) \
            or hours_left * cfg.get("end_cover", 0.5) < cfg.get("cover_hours", 6):    # the last day: the model's own targets
        return ranked
    want = {sku: q for _, sku, q in ranked}
    for sku in listed(cfg):
        t, x, m = cfg["sku_target_units"].get(sku), catalog.get(sku) or {}, (model.get("skus") or {}).get(sku)
        if not isinstance(t, int) or isinstance(t, bool) or not x.get("list_price") or not m:
            continue
        cost = x["list_price"] * EXPECTED_COST.get(x.get("kind"), EXPECTED_COST["goods"]) * (factors or {}).get(sku, 1.0)
        refund = (cfg.get("prior_break") or 0) if x.get("kind") == "goods" else 0
        measured = market_model.break_chance(breaks, sku) if breaks and x.get("kind") == "goods" else None
        refund = measured if measured is not None else refund
        if x["list_price"] * m["price_ratio"] * (1 - refund) - cost <= 0:
            continue
        need = min(MAX_LOT, t - have_sku[sku])
        if need > want.get(sku, 0):
            want[sku] = need
    order = [sku for _, sku, _ in ranked] + [s for s in want if s not in {sku for _, sku, _ in ranked}]
    return [(0, sku, want[sku]) for sku in order if want[sku] > 0]


def plan(inv, catalog, demand, cfg, model=None, breaks=None, waiting=()):
    """The hook's answer for this state."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        return {"bankrupt": True, "buy": []}
    now = time.time()
    live = [u for u in units if u.get("grade") != "expired" and not (u.get("expires_at") and u["expires_at"] <= now)]
    have = collections.Counter(u.get("category") for u in live)
    # the units of each SKU as its target counts them (restock_fresh_slots, default off: every live unit)
    have_sku = slot_count(live, cfg, catalog, surge(inv), now)[0]
    active = bool(model and model.get("active"))
    hours_left = (cfg["round_ends_at"] - now) / 3600 if cfg.get("round_ends_at") else float("inf")
    picks = []
    if active:      # the caps first, over the whole ranked list, then the line limit: a line the food cap empties is
        ranked = wanted(model, catalog, have_sku, balance, cfg, hours_left, surge(inv))   # refilled by the next one that fits
        ranked = restock_demand_catchup.adjust(ranked, model, catalog, have_sku, balance, cfg, hours_left, surge(inv), worst)
        ranked = fill_listed(ranked, model, catalog, have_sku, cfg, surge(inv), hours_left, breaks)
        # proven / priority SKUs first, so the guard gives them the stock-cost room and the line limit cannot cut them:
        # ranked per won a mattress came below the cut (04:59 run left out G1515d951 that the guard allowed 3 of, and
        # G75f11c19 got 0 room in every run 04:44-05:02), and proven_first() only sees the lines plan() returns
        first = urgent_listed(cfg, inv, catalog, waiting, model)
        extra = fresh_extra(cfg, inv, catalog, waiting, now)      # default off: none; after the unlisted lines too
        ranked.sort(key=lambda r: len(first) + 1 if r[1] in extra else first.index(r[1]) if r[1] in first
                    else len(first))                                                      # stable: the rest keep their order
        fit = guard([{"sku": sku, "qty": qty} for _, sku, qty in ranked], inv, catalog, cfg, breaks, waiting)
        picks = [(0, b["sku"], b["qty"]) for b in fit[:cfg["max_lines"] - cfg["explore_lines"]]]
    picked = {sku for _, sku, _ in picks}
    skus = collections.defaultdict(list)
    medians = {d["category"]: d.get("median_price") for d in demand}
    for sku, x in catalog.items():          # GPT Pro §5.1: caps by kind, and near the category's median price
        if opening.fits(sku, x, {**opening.DEFAULTS, **{k: cfg[k] for k in ("food_max_price", "goods_max_price", "band_low",
                                                                               "band_high", "min_shelf_hours") if k in cfg}},
                        medians.get(x.get("category"))):
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
    return {"buy": guard([{"sku": sku, "qty": qty} for _, sku, qty in picks], inv, catalog, cfg, breaks, waiting)}


PENDING_SECONDS = 240      # a line sent this recently whose units have not shown up yet still counts as bought
SENT = "restock_sent.json"


def pending(md, inv, now=None):
    """Recent requests: qty is undelivered stock, cash_qty reserves the full request.
    Our compact supply/settle ledger retains unit IDs after sale/breakage. Until a request
    expires, reserve its full worst cash cost even if the wallet snapshot is stale.
    Missing historical supply rows conservatively fall back to current inventory.
    Unreadable request state raises so main() buys nothing."""
    now = now or time.time()
    out = []
    try:
        with open(os.path.join(md, SENT), encoding="utf-8") as f:
            sent = json.load(f)
    except (FileNotFoundError, TypeError):       # nothing sent yet, or no state folder (md None)
        sent = []
    if not isinstance(sent, list):
        raise ValueError(f"{SENT} is not a list")
    # A supply is immutable delivery evidence, but not proof of sale: if the
    # snapshot is older than the supply, an absent UNSOLD unit still needs a stock
    # reservation. Clear it only when on-hand already counts it or a settle proves
    # it left. Never match supply before request time (even a five-second slack
    # could reassign an expired request\'s unit to its later neighbour).
    # Union by unit ID, and consume each ID once across overlapping requests.
    delivered, sold, present = {}, {}, set()
    me = os.environ.get("SHOP_MARKET_NAME", "nocha12")
    for r in rows_of(md):
        # Settlement shop is a URL or display name, not the supply owner ID.
        # A sale may clear stock only by joining SKU + unit to OUR supply below
        # (or an on-hand unit already counted by the snapshot), never by shop text.
        if r.get("k") == "settle" and isinstance(r.get("unit"), str) and isinstance(r.get("sku"), str) \
                and hard_limits.number(r.get("ts")) and r["ts"] <= now:
            key = (r["unit"], r["sku"])
            sold[key] = max(sold.get(key, -1), r["ts"])
        if r.get("k") == "supply" and r.get("shop") == me and hard_limits.number(r.get("ts")):
            for uid in r.get("units") if isinstance(r.get("units"), list) else ():
                if isinstance(uid, str):
                    delivered.setdefault(uid, (r.get("sku"), r["ts"]))
    for i, u in enumerate(inv.get("units") or []):
        if isinstance(u, dict) and hard_limits.number(u.get("supplied_at")):
            uid = u.get("unit") or ("snapshot", i)
            delivered.setdefault(uid, (u.get("sku"), u["supplied_at"]))
            present.add(uid)
    used = set()
    for s in sorted(sent, key=lambda x: x.get("at", 0) if isinstance(x, dict)
                    and hard_limits.number(x.get("at")) else 0):
        if not (isinstance(s, dict) and s.get("sku") and isinstance(s.get("qty"), int)
                and isinstance(s.get("at"), (int, float)) and now - s["at"] < PENDING_SECONDS):
            continue
        if not hard_limits.number(s.get("worst", 0)) or s.get("worst", 0) < 0:
            raise ValueError(f"{SENT}: {s.get('sku')} has no usable worst cost")
        candidates = [uid for uid, (sku, at) in delivered.items() if uid not in used
                      and sku == s["sku"] and s["at"] <= at <= min(now, s["at"] + PENDING_SECONDS)
                      and (uid in present or sold.get((uid, sku), -1) >= at)]
        came = min(s["qty"], len(candidates))
        used.update(candidates[:came])
        # A ledger confirmation says nothing about the age of this wallet snapshot.
        # Retain the full worst-case cash reservation until the request's usual expiry.
        out.append({"sku": s["sku"], "qty": s["qty"] - came,
                    "cash_qty": s["qty"], "worst": s.get("worst", 0)})
    return out


def sent_waiting(md, now=None):
    """pending() without the ledger read, for restock_window_fast's run: every line sent in the last PENDING_SECONDS
    counts in full as still on its way, whether its units have come or not. qty is never under pending()'s (units that
    arrived are in inventory.json as well, so they count twice), cash_qty and worst are the same: every unit, stock-cost
    and cash cap in guard() is at least as tight. Unreadable request state raises, as in pending()."""
    now = now or time.time()
    try:
        with open(os.path.join(md, SENT), encoding="utf-8") as f:
            sent = json.load(f)
    except (FileNotFoundError, TypeError):
        sent = []
    if not isinstance(sent, list):
        raise ValueError(f"{SENT} is not a list")
    out = []
    for s in sent:
        if not (isinstance(s, dict) and s.get("sku") and isinstance(s.get("qty"), int)
                and isinstance(s.get("at"), (int, float)) and now - s["at"] < PENDING_SECONDS):
            continue
        if not hard_limits.number(s.get("worst", 0)) or s.get("worst", 0) < 0:
            raise ValueError(f"{SENT}: {s.get('sku')} has no usable worst cost")
        out.append({"sku": s["sku"], "qty": s["qty"], "cash_qty": s["qty"], "worst": s.get("worst", 0)})
    return out


def cash_note(buy, inv, catalog, buffer):
    """{"floor", "unit": {sku: the most one unit of the line can cost, storage included}} for the kit, which reads the
    balance again before every supply call (scripts/market_kit.py cash_ok) and sends no line that would take it under
    floor: this run's check can be stale by its last line (GPT Pro 14, 대표님 11/10 23:48). floor is the run's
    cash buffer (hard_limits.cash_buffer); unit is guard()'s own cost of a unit (worst() and BUY_STORAGE). None with no
    line or no finite floor (the lines are then none anyway)."""
    if not buy or not hard_limits.number(buffer) or buffer >= hard_limits.OPEN:
        return None
    factors = surge(inv)
    unit = {}
    for b in buy:       # a SKU on two lines: the dearer unit of the two (independent review 1e2c08b m4)
        unit[b["sku"]] = max(unit.get(b["sku"], 0), worst(b["sku"], b["qty"], catalog, factors) * (1 + hard_limits.BUY_STORAGE))
    return {"floor": buffer, "unit": unit}


def note_sent(md, buy, catalog, now=None, factors=None):
    now = now or time.time()
    old = load(os.path.join(md, SENT), [])
    keep = [s for s in old if isinstance(s, dict) and isinstance(s.get("at"), (int, float))
            and now - s["at"] < PENDING_SECONDS] if isinstance(old, list) else []
    keep += [{"at": now, "sku": b["sku"], "qty": b["qty"], "worst": worst(b["sku"], b["qty"], catalog, factors)} for b in buy]
    tmp = os.path.join(md, SENT + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f)
    os.replace(tmp, os.path.join(md, SENT))


def guard(buy, inv, catalog, cfg, breaks=None, waiting=()):
    """The lines that keep to the settings, whoever proposed them (formula, opening, next_window, the agent): food per line,
    goods that break too often, and totals over the stock held + lines still on their way (waiting, see pending()) + these
    new lines: units per SKU (max_units_per_sku; high_price_max_units for goods listed above high_price_goods, high_price_proven_max_units for the
    high_price_proven_skus whose extra units sold out between supply windows), the cost of all stock
    (max_stock_cost) and of food (food_max_stock_cost), the cash kept, and hard_limits' line above bankruptcy. New and
    waiting lines count at worst cost (0.75 x list x the surge factor, worst()). A line that does not fit is shrunk or
    dropped; a SKU under surge from SURGE_HALF buys half unless it is proven / priority, from SURGE_SKIP not at all."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    now = time.time()
    live = [u for u in units if not (u.get("expires_at") and u["expires_at"] <= now)]
    kind = lambda sku: (catalog.get(sku) or {}).get("kind")
    count = collections.Counter(u.get("sku") for u in live)
    # units per SKU as its sku_target_units counts them: the same, or with restock_fresh_slots on (cfg["fresh_slots"],
    # set by main() from its switch) a listed goods SKU's units that waited past its hours leave room, a step at a
    # time. Only the target reads it: max_units_per_sku, the high-price and surge caps count every unit held
    # (independent review 8023a19 I2: a target of 12 could have held 24 past the 20 a SKU may hold)
    slots = slot_count(live, cfg, catalog, surge(inv), now)[0]
    sellable = restock_sellable_slots.on(cfg)      # its extra units keep its cash floor (None: off)
    spent = sum(w["worst"] * w.get("cash_qty", w["qty"]) for w in waiting)
    held = sum(u.get("cost") or 0 for u in live)
    food = sum(u.get("cost") or 0 for u in live if u.get("kind") == "food")
    for w in waiting:
        count[w["sku"]] += w["qty"]
        slots[w["sku"]] += w["qty"]
        held += w["worst"] * w["qty"]
        food += w["worst"] * w["qty"] if kind(w["sku"]) == "food" else 0
    room = cfg.get("max_stock_cost", float("inf")) - held
    food_room = cfg.get("food_max_stock_cost", float("inf")) - food
    spare = min(balance - cfg["keep_cash"], hard_limits.spendable(balance, cfg.get("cash_buffer", hard_limits.STOP_BUYING_BELOW))) \
        - sum(w["worst"] * w.get("cash_qty", w["qty"]) for w in waiting)
    out, factors = [], surge(inv)
    for b in buy:
        x = catalog.get(b["sku"]) or {}
        qty = min(b["qty"], MAX_LOT)
        qty = restock_first_stock.quantity(cfg.get("first_stock"), b["sku"], qty, count[b["sku"]])
        f, proven_or_priority = factors.get(b["sku"], 1.0), b["sku"] in listed(cfg)
        if f >= SURGE_SKIP and not proven_or_priority:
            print(f"[restock] guard: {b['sku']} surge {f} >= {SURGE_SKIP}, left out until it falls", file=sys.stderr)
            continue
        if b["sku"] in (cfg.get("skip_skus") or ()):
            print(f"[restock] guard: {b['sku']} is in skip_skus, left out", file=sys.stderr)
            continue
        if x.get("kind") == "food" and cfg.get("food_max_qty") is not None:
            qty = min(qty, cfg["food_max_qty"])
        if x.get("kind") == "goods" and breaks and cfg.get("max_break") is not None:
            chance = market_model.break_chance(breaks, b["sku"])
            if chance is not None and chance > cfg["max_break"]:
                print(f"[restock] guard: {b['sku']} breaks {chance:.0%} > {cfg['max_break']:.0%}, left out", file=sys.stderr)
                continue
        cap = other = cfg.get("max_units_per_sku", MAX_LOT)     # other: the caps but the target, on every unit held
        high = x.get("kind") == "goods" and (x.get("list_price") or 0) > cfg.get("high_price_goods", float("inf"))
        if high and cfg.get("high_price_listed_only") is True and b["sku"] not in listed(cfg):
            # 04:43-04:45 the agent bought 2 jacquard mattresses, 2 bed frames, 4 toppers no list had: unsold at 05:00
            print(f"[restock] guard: {b['sku']} high price, not in high_price_proven_skus / priority_skus, left out", file=sys.stderr)
            continue
        target = (cfg.get("sku_target_units") or {}).get(b["sku"]) if isinstance(cfg.get("sku_target_units"), dict) else None
        if isinstance(target, int) and not isinstance(target, bool):
            cap = min(cap, target)          # units held + waiting + new <= target (G6b9db161 3 while proven ones get 4)
        if high:
            proven = b["sku"] in listed({"high_price_proven_skus": cfg.get("high_price_proven_skus")})
            top = cfg.get("high_price_proven_max_units", cfg.get("high_price_max_units", 1)) if proven \
                else cfg.get("high_price_max_units", 1)
            cap, other = min(cap, top), min(other, top)
        if f >= SURGE_SKIP:         # a proven / priority SKU keeps one unit on the shelf, no more, deep in a surge
            cap, other = min(cap, 1), min(other, 1)
        elif f >= SURGE_HALF and not proven_or_priority and cap > 1:
            cap //= 2               # half the units held + waiting + new, not half of each run's line
        # off, slots is count and cap <= other: cap - count, as before
        asked, capped = qty, min(qty, cap - slots[b["sku"]], other - count[b["sku"]])
        plain = max(0, min(qty, cap - count[b["sku"]], other - count[b["sku"]]))      # today's room: every unit counts
        if sellable is not None and capped > plain and x.get("list_price"):
            # restock_sellable_slots: the units over today's room only from cash over its floor after the lines before
            each = worst(b["sku"], capped, catalog, factors) * (1 + hard_limits.BUY_STORAGE)
            free = balance - spent - sellable["cash_floor"]
            extra = min(capped - plain, max(0, int(free // each)) if each > 0 else 0)
            if extra < capped - plain:
                print(f"[restock] guard: {b['sku']} sellable_slots {capped - plain} -> {extra} over today's room "
                      f"(cash floor {sellable['cash_floor']:.0f})", file=sys.stderr)
            capped = plain + extra
        # priced on the capped line, not the line the cash leaves: a few won high at most, never low
        unit = worst(b["sku"], capped, catalog, factors) if x.get("list_price") else float("inf")
        paid = unit * (1 + hard_limits.BUY_STORAGE)      # the cash a unit takes: its cost and the storage it adds
        stock_limit = min(room, food_room if x.get("kind") == "food" else float("inf"))
        by_cash = int(spare // paid) if unit < float("inf") else 0
        by_stock = capped if stock_limit == float("inf") else int(stock_limit // unit) if unit < float("inf") else 0
        qty = min(capped, by_cash, by_stock)
        if qty < asked:     # which limits cut the line, so a stock-out can be traced to a cap or the cash
            why = (["units per SKU"] if capped < asked else []) + ([
                "no list price" if unit == float("inf") else "cash kept" if qty == by_cash else "stock cost" if stock_limit == room
                else "food stock cost"] if qty < capped else [])
            print(f"[restock] guard: {b['sku']} {asked} -> {max(qty, 0)} ({', '.join(why)})", file=sys.stderr)
            if qty < 1 and "stock cost" in why and x.get("kind") == "food" and proven_or_priority and count[b["sku"]] <= 0 \
                    and unit < float("inf"):
                # measured only, nothing reserved: whether keeping 1 unit for an empty priority food would change a real
                # window's lines (GPT Pro 11/05 13:30 §2; a person decides the guard after the windows are compared)
                print(f"[restock] food-reserve sim: {b['sku']} none held or waiting, cut by stock cost; 1 unit would take "
                      f"{unit:.0f} of {room:.0f} room", file=sys.stderr)
        if qty < 1:
            continue
        out.append({"sku": b["sku"], "qty": qty})
        count[b["sku"]] += qty
        slots[b["sku"]] += qty
        spare, room = spare - paid * qty, room - unit * qty
        spent += paid * qty
        if x.get("kind") == "food":
            food_room -= unit * qty
    return out


_ROWS = {}      # state -> ((size, mtime_ns), rows)


def rows_of(state):
    """The ledger rows, read once a run while the file is unchanged (read_model, read_breaks and opening each read the
    143 MB file, ~0.8 s each and more beside the buyer: in the 08:23 window of 11/07 the run inside it printed its lines
    ~12 s after the last supply and G2607d1f6 was held back at the close). A sync that adds rows changes the key.
    Only the rows its readers use (market_model.load_stock_rows; the whole ledger held 2.7-2.8 GB each run on 11/15), and
    the old copy goes before the next is read. decision_memory, the forums and measure get these too."""
    try:
        st = os.stat(os.path.join(state, market_model.ROWS))
        key = (st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key is not None and state in _ROWS and _ROWS[state][0] == key:
        return _ROWS[state][1]
    _ROWS.clear()
    rows = market_model.load_stock_rows(state)
    if key is not None:
        _ROWS[state] = (key, rows)
    return rows


def read_breaks(state, catalog):
    try:
        return market_model.break_stats(rows_of(state), catalog, mature_only=True)
    except Exception:   # noqa: BLE001 - without break rates the guard simply does not use them
        return None


SALES_TAIL_BYTES = 15000000      # the ledger's last ~9 h (11/06: 30 MB held 17 h); refunds come within BREAK_HOURS
# read before an unreadable exposure stops the buys (11/07 05:4x: 15 MB held 8.2 h, 60 MB 34 h)
SALES_TAIL_MAX_BYTES = 60000000


def warranted_sales(state, catalog, now=None):
    """[(time, price)] of this shop's warranted goods sales in the last hard_limits.BREAK_HOURS, from the tail of the
    ledger rows (a result row names the seller, the settle row the price and the warranty); a longer tail, up to
    SALES_TAIL_MAX_BYTES, when the first does not reach back BREAK_HOURS; None when unreadable, so cash_buffer() buys
    nothing."""
    size = SALES_TAIL_BYTES
    while True:
        out = _warranted_sales(state, catalog, now, size)
        if out is not SHORT:
            return out
        if size >= SALES_TAIL_MAX_BYTES:
            print(f"[restock] ledger tail of {size} bytes does not reach back {hard_limits.BREAK_HOURS} h: "
                  f"exposure unknown, buying nothing", file=sys.stderr, flush=True)
            return None
        size = min(size * 4, SALES_TAIL_MAX_BYTES)


SHORT = object()        # the tail read does not reach back BREAK_HOURS


def _warranted_sales(state, catalog, now, tail):
    now, mine = now or time.time(), market_model.my_shops()
    since = now - hard_limits.BREAK_HOURS * 3600
    try:
        with open(os.path.join(state, market_model.ROWS), "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - tail)
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()[1 if start else 0:]   # a cut first line only
    except FileNotFoundError:       # no ledger yet (a new or reset shop): no sales of ours to refund
        return []
    except OSError:
        return None
    ours, sold, first = set(), [], None
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if first is None and isinstance(r.get("ts"), (int, float)):
            first = r["ts"]
        if r.get("k") == "result" and r.get("seller") in mine:
            ours.add(r.get("order"))
        elif r.get("k") == "settle" and isinstance(r.get("ts"), (int, float)) and r["ts"] >= since and r.get("warranty") \
                and (catalog.get(r.get("sku")) or {}).get("kind") == "goods" and isinstance(r.get("amount"), (int, float)):
            sold.append(r)
    if start and (first is None or first > since):
        return SHORT        # the tail no longer covers BREAK_HOURS: a longer one, or no buys, never too little kept
    return [(r["ts"], r["amount"]) for r in sold if r.get("order") in ours or str(r.get("shop") or "").rstrip("/") in mine]


def read_model(state, catalog, cfg):
    """The demand model, or None when it cannot be built: a restock must never fail because of the ledger."""
    me = os.environ.get("SHOP_MARKET_NAME", "nocha12")
    shops = market_model.my_shops()
    try:
        added = market_model.sync(state, max_pages=cfg.get("max_pages", market_model.DEFAULTS["max_pages"]))
        model = market_model.estimate(rows_of(state), catalog, {me}, shops, cfg=cfg)
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


NEXT_WINDOW_STATE = "next_window_state.json"


def gone(u, now):
    return u.get("grade") == "expired" or bool(u.get("expires_at") and u["expires_at"] <= now)


def arrived(sku, at, inv, memory=os.path.join("memory", "seller_memory_r3.json")):
    """True when a line for this SKU sent at `at` was supplied: a unit of it came in since, or the shop has sold or agreed
    one since (a unit can sell within a minute of arriving)."""
    if any(u.get("sku") == sku and (u.get("supplied_at") or 0) >= at - 5 for u in inv.get("units") or []):
        return True
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at - 5))
    m = load(memory, {})
    rows = m.get("negotiations") if isinstance(m, dict) else None
    return any(isinstance(n, dict) and n.get("sku") == sku and str(n.get("time", "")) >= since
               and n.get("result") in ("agreed", "deal") for n in rows or [])


def next_window(md, inv, catalog, cfg, waiting=(), keep=None, now=None):
    """pricing.json "next_window": a one-off buy for the first supply window it meets (GPT Pro review §2-a). Each target is a
    ceiling: what is in stock or still on its way is subtracted. (lines, why) while it runs, (None, why) once done, off or
    expired; [] means buy nothing now (no window yet, or the plan's window is done). It ends when its window has closed."""
    now = now or time.time()
    targets = cfg.get("targets") or {}
    if not targets or not cfg.get("id"):
        return None, "no next_window plan"
    path = os.path.join(md, NEXT_WINDOW_STATE)
    st = load(path, {})
    if st.get("id") != cfg["id"]:
        st = {"id": cfg["id"], "created": now, "sent": [], "done": False, "notes": []}
    if st["done"]:
        return None, f"next_window {cfg['id']} done"

    def finish(why):
        st["done"], st["notes"] = True, st["notes"] + [why]
        opening.save(path, st)
        return None, f"next_window {cfg['id']}: {why}"

    if now - st["created"] > cfg.get("expires_hours", 2) * 3600:
        return finish("expired before a window")
    if st["sent"] and opening.window_closed(inv, now):
        at = st["sent"][-1]["at"]
        missed = [b["sku"] for b in st["sent"][-1]["lines"] if not arrived(b["sku"], at, inv)]
        if missed and st.get("retries", 0) < 1:      # the market refused them (e.g. 429 at the window's edge): once more
            st.update(retries=st.get("retries", 0) + 1, only=missed, sent=[], notes=st["notes"] + [f"missed {missed}"])
            opening.save(path, st)
            return [], f"next_window {cfg['id']}: {missed} never arrived, retried in the next window"
        return finish(f"bought in the window of {time.strftime('%H:%M', time.localtime(at))}"
                      + (f", never arrived: {missed}" if missed else ""))
    if st["sent"]:                           # sent once; the rest of this window is the plan's: nothing else is bought
        return [], f"next_window {cfg['id']}: sent, nothing else in this window"
    if opening.window_closed(inv, now, lead=0, need=opening.SECONDS_PER_LINE * len(targets) + 15):
        opening.save(path, st)               # lead 0: a line sent before the window opens is refused (429) and lost
        return [], f"next_window {cfg['id']} waits for the supply window"
    have = collections.Counter(u.get("sku") for u in inv.get("units") or [] if not gone(u, now))
    for w in waiting:
        have[w["sku"]] += w["qty"]
    only = set(st.get("only") or targets)
    want = [{"sku": sku, "qty": q - have[sku]} for sku, q in targets.items()
            if sku in catalog and sku in only and q - have[sku] > 0]
    out = keep(want) if keep and want else want
    if not out:
        return finish(f"nothing to buy (wanted {want}; stock and limits leave none)")
    st["sent"].append({"at": now, "lines": out})
    opening.save(path, st)
    cut = {b["sku"]: b["qty"] for b in out}
    notes = [f"{b['sku']} {b['qty']}->{cut.get(b['sku'], 0)}" for b in want if cut.get(b["sku"], 0) != b["qty"]]
    return out, (f"next_window {cfg['id']}: {len(out)} lines, {sum(cut.values())} units"
                 + (f" (guard: {', '.join(notes)})" if notes else ""))


PRICE_LOG_STATE = "price_log_state.json"


def log_prices(md, inv, breaks, pricing):
    """A [price] line for each unit not logged before: its floor and first ask from its real cost, as the seller computes
    them at the first message of a talk (profit_price)."""
    path = os.path.join(md, PRICE_LOG_STATE)
    seen = set(load(path, {}).get("seen", []))
    units = inv.get("units") or []
    briefing = load(os.path.join(md, "briefing.json"), {}).get("skus") or {}
    memory = os.path.join("memory", "seller_memory_r3.json")
    for u in units:
        if u["unit"] in seen or not u.get("cost"):
            continue
        ratio = (briefing.get(u["sku"]) or {}).get("price_ratio")
        median = ratio * u["list_price"] if ratio and u.get("list_price") else None
        measured = market_model.unit_break_chance(breaks, u["sku"], u.get("grade")) if breaks and u.get("kind") == "goods" else None
        q = profit_price.quote(u, median, measured, shop_memory.paid_prices(u["sku"], memory=memory), pricing)
        print(f"[price] {profit_price.line(u, q)}", file=sys.stderr, flush=True)
    opening.save(path, {"seen": [u["unit"] for u in units]})


GOAL = """You decide what stock a shop buys now. Book (balance plus what the unsold stock cost, Patch 3.3: a restock
does not lower it; storage, refunds and spoiled food do), revenue and stars are what count. Round ends at unix time {ends}, now is {now}. A unit costs 25-75% of list price by grade;
food past its date is thrown away at cost; goods in stock cost 1% of cost per day. A shop with no stock cannot sell at all,
and other shops' deals show in the ledger whether you stock or not. Up to {lines} lines, qty 1-20 each.
c0 is the formula's proposal for this state; follow it, change it or ignore it, as the evidence says. Your lines go
first; the code then fills the room the limits leave with the formula's lines, because buyers only see shops that stock
the ordered category and a shop's visits follow how much it stocks. c1 is how your own earlier
stock decisions turned out: what sold, what is left, what is gone unsold. Learn from it: a judgment that failed should not repeat.
c2 is the shop's own memory: long-term notes, lessons from earlier mistakes that match this state, and recent days.
Do not narrow the overall proposal merely because stock cost or carrying exposure is high: stock at cost remains in book.
Storage and expiry matter per SKU; prefer evidence of that SKU's paid sales and losses to a blanket avoidance of food or furniture.
When citing inventory exposure to exclude or reduce a proposed SKU, identify that SKU and cite its own matured paid-sale net loss or the same extra units remaining across two supply windows; total inventory or category exposure alone is not SKU-specific evidence. If that evidence is unavailable, say so rather than treating exposure as a proven loss.
The code keeps every line inside the shop's current limits (pricing.json), which start cautious and widen as results earn it."""


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


AHEAD_SECONDS = 150      # a due agent decision is made on a closed run this close to the window: ~2 runs, the LLM's ~30 s done


def ahead_due(inv, now=None):
    """True when supply is closed and opens within AHEAD_SECONDS. Earlier, the decision would land just after a window
    closed (its lines still pending) and a failing agent would be asked again every minute (independent review 11/07)."""
    r, now = inv.get("restock"), now or time.time()
    if not isinstance(r, dict) or r.get("open") is not False or not isinstance(r.get("opens_at"), (int, float)):
        return False
    return 0 < r["opens_at"] - now <= AHEAD_SECONDS


def agent_turn(state, inv, catalog, demand, cfg, model, decide=agent_loop.decide, send=True, due_minutes=None):
    """The lines to send now: from the agent's queue, after a new decision when one is due. None: use the formula.
    send=False (outside a supply window) only makes a due decision and queues all its lines. due_minutes (inside one)
    puts a new decision off: at 07:44 (11/07) the 20-min decision fell on the window's first run and its ~30 s of LLM
    came before the first supply call (first line 16-18 s after the window opened in 3 of the 6 windows 06:21-08:05)."""
    path = os.path.join(state, "restock_queue.json")
    q = load(path, {"at": 0, "lines": []})
    if time.time() - q["at"] >= (cfg["agent_every_minutes"] if due_minutes is None else due_minutes) * 60:
        proposal = plan(inv, catalog, demand, {**cfg, "max_lines": cfg["agent_max_lines"], "demand_catchup": None}, model)
        results = decision_memory.outcomes(state, inv, rows=rows_of(state))
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
    elif not send:
        return None
    now, q["lines"] = (q["lines"][:cfg["max_lines"]], q["lines"][cfg["max_lines"]:]) if send else ([], q["lines"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, path)
    return now if send else None


def proven_first(agent_lines, formula_lines, cfg, order=None):
    """The high_price_proven_skus, then priority_skus, that either the agent or the formula proposes go first, at the
    larger of their two quantities (guard still caps them). At 03:40 the agent's 'G1515d951 1' replaced the formula's 3
    and max_lines cut G9c02e68e; at 04:00 they came behind five food lines and the 180 s window closed before G1515d951
    was bought (kit log 04:03). order: the listed SKUs in the order to send them (urgent_listed), else the lists' own."""
    if order is None:
        order = []
        for s in list(cfg.get("high_price_proven_skus") or ()) + list(cfg.get("priority_skus") or ()):
            if isinstance(s, str) and s not in order:
                order.append(s)
    want = {}
    for b in list(agent_lines) + list(formula_lines):
        q = b.get("qty")
        if b.get("sku") in order and isinstance(q, int) and not isinstance(q, bool) and q > 0:
            want[b["sku"]] = max(want.get(b["sku"], 0), q)
    return [{"sku": s, "qty": want[s]} for s in order if s in want] + [b for b in agent_lines if b.get("sku") not in want]


SMALL_FOOD_LINE = 3     # a food line of at most this many units is a top-up: it goes after the goods lines


def small_food(catalog, inv, waiting=()):
    """batch()'s last: a food line of SMALL_FOOD_LINE units or fewer for a SKU with a live unit held or on its way. Food
    nets ~4-6k a unit, goods 15-300k (24 h ledger, sku_net), and a window serves ~14 lines: 04:4x-08:4x (11/07) 16 such
    lines went out while 42 goods lines were held back when the window closed (08:23: 3 apples and 1 kimchi a minute
    after 14 and 12, then G2607d1f6 held back). An empty food SKU keeps its place (urgent_listed puts it first): the
    food cost cap or the cash guard can trim its line to a few units (independent review 11/07)."""
    now = time.time()
    have = collections.Counter(u.get("sku") for u in inv.get("units") or [] if isinstance(u, dict) and not gone(u, now))
    for w in waiting or ():
        have[w.get("sku")] += w.get("qty") or 0

    def last(b):
        q, s = b.get("qty"), b.get("sku")
        return (catalog.get(s) or {}).get("kind") == "food" and isinstance(q, int) and not isinstance(q, bool) \
            and q <= SMALL_FOOD_LINE and have[s] > 0
    return last


def batch(agent_lines, fill_lines, formula_lines, keep, cfg, order=None, last=None):
    """This run's lines and the agent lines to queue again: proven / priority SKUs first (proven_first, from the
    formula's plan even while the agent's queue waits), then the agent's, then fill_lines (the formula's, once the
    queue is empty). Lines the guard drops are gone, as before; agent lines that only the cut at max_lines leaves out
    go back to the queue, so the proven lines never push an agent line out for good. last(line) True: the line moves
    behind the others (small_food), keeping the order within each part."""
    agent_skus = {b.get("sku") for b in agent_lines}
    first = set(cfg.get("high_price_proven_skus") or ()) | set(cfg.get("priority_skus") or ())
    lines = proven_first(agent_lines, formula_lines, cfg, order)
    mine = {b["sku"] for b in lines}
    lines = list(lines) + [b for b in fill_lines if b["sku"] not in mine]        # as with_formula()
    if last is not None:
        # class 2 (main: restock_fresh_slots' extra lines) goes behind the others before the guard too, so the cash and
        # the stock-cost room are the other lines' first (independent review ecef27b M1: with 2.18M over the buffer an
        # extra sofa took the room of two agent units). No class 2 (off, bool classes): the order is untouched
        lines = [b for b in lines if int(last(b)) < 2] + [b for b in lines if int(last(b)) >= 2]
    kept = keep(lines)
    if last is not None:        # False/0 first, then True/1 (small_food), then 2, each in its order
        kept = sorted(kept, key=lambda b: int(last(b)))
    n = cfg["max_lines"]
    return kept[:n], [b for b in kept[n:] if b["sku"] in agent_skus and b["sku"] not in first]


def line_class(extra, small):
    """batch()'s last for main(): 2 for a restock_fresh_slots extra line (behind every other line, before the guard
    as well), 1 for a small food top-up (small_food: behind the goods lines), 0 for the rest."""
    return lambda b: 2 if b.get("sku") in extra else int(bool(small(b)))


def requeue(state, lines):
    """Agent lines that did not fit this run's max_lines go back to the front of the queue, so none is lost."""
    if not lines:
        return
    path = os.path.join(state, "restock_queue.json")
    q = load(path, {})
    q["lines"] = list(lines) + (q.get("lines") or [])
    q.setdefault("at", time.time())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, path)


def with_formula(agent_lines, formula_lines, keep, limit):
    """The agent's lines first, then the formula's for the room the limits leave. Buyers see only shops that stock the
    ordered category (shops.json offers), and over Round 3's first three hours units stocked and visits ranked together
    (Spearman 0.80, 42 shops): the agent alone bought 2-3 lines a window and nothing between its decisions."""
    mine = {b["sku"] for b in agent_lines}
    return keep(list(agent_lines) + [b for b in formula_lines if b["sku"] not in mine])[:limit]


def with_trial(trial, buy, inv, catalog, cfg, breaks=None, waiting=(), hold=()):
    """next_window's one-off lines first, then the run's usual lines in the room the limits leave (the trial's units count
    as on their way), never more than max_lines. The trial used to replace the whole window: the 01:40 and 09:40 windows
    of 11/05 each bought the single trial unit while the windows around them bought 28-53 units (ledger supply rows).
    The usual lines leave out the trial's SKUs (hold: all its targets while it runs), so a unit of them that arrives is
    the trial's and next_window's arrived() check and retry stay true. A target the guard cut from the trial stays held too."""
    skip = set(hold) | {b["sku"] for b in trial or ()}
    rest = [b for b in buy if b["sku"] not in skip]
    if not trial:
        return rest
    sent = [{"sku": b["sku"], "qty": b["qty"], "worst": worst(b["sku"], b["qty"], catalog, surge(inv))} for b in trial]
    return (list(trial) + guard(rest, inv, catalog, cfg, breaks, list(waiting) + sent))[:max(cfg["max_lines"], len(trial))]


LATE_MARGIN = 5     # seconds the kit needs to read this run's output and make its first supply call
KIT_LINES = 20      # the kit sends the first 20 lines of a run (scripts/market_kit.py MAX_SUPPLY_LINES; 10 until 11/11)


def late_lines(state, inv, buy, agent_skus=(), now=None):
    """The lines the kit can still start before an open window closes. A run reads inventory.json when it starts, and
    the agent turn and the hooks after it can take tens of seconds: on 11/05 a run that began at 16:25:13 handed four
    lines over at 16:25:24, nine seconds after the 16:25:15 close, and each was refused (429) and retried. Lines that
    cannot start go back to the queue when they are the agent's; the formula plans its own lines again next run."""
    r, now = inv.get("restock"), now or time.time()
    if not buy or not isinstance(r, dict) or r.get("open") is not True or not isinstance(r.get("closes_at"), (int, float)):
        return buy
    left = r["closes_at"] - now - LATE_MARGIN
    n = 0 if left < 0 else int(left // opening.SECONDS_PER_LINE) + 1
    if n >= len(buy):
        return buy
    send, late = list(buy[:n]), list(buy[n:])
    requeue(state, [b for b in late if b.get("sku") in agent_skus])
    print(f"[restock] window closes in {r['closes_at'] - now:.0f} s: {len(late)} line(s) held back "
          f"({', '.join(str(b.get('sku')) for b in late)})", file=sys.stderr, flush=True)
    return send


def without_held(cfg, hold):
    """cfg with the next_window SKUs it holds among skip_skus: the guard leaves them out before the cut at max_lines.
    Planned and dropped after the cut (main, "if hold and not trial") a held SKU took one of a run's max_lines slots: every
    first run of a window printed 9 (11/10 14:44-16:24, G1465c678). restock_window_fast's arm only."""
    skip = cfg.get("skip_skus")
    skip = list(skip) if isinstance(skip, (list, tuple)) else []
    return {**cfg, "skip_skus": skip + sorted(s for s in hold if s not in skip)}


def carry_lines(inv, catalog, demand, cfg, model, breaks, waiting, sent, hold):
    """restock_window_fast: the plan's next lines after the ones this run sends, for the window's next run. The same
    plan() and guard() over the same state, cut later: the cash, stock-cost and unit room the earlier lines take is
    counted before these. The SKUs sent and the held next_window SKUs are left out. [] without an active demand
    model or on any trouble (the next run is then the usual one). Prints nothing: the guard's lines are this run's."""
    try:
        if not (model and model.get("active")):
            return []
        more = without_held({**cfg, "max_lines": cfg["max_lines"] + restock_window_fast.CARRY_LINES + len(sent)}, hold)
        with contextlib.redirect_stderr(io.StringIO()):
            buy = plan(inv, catalog, demand, more, model, breaks, waiting).get("buy") or []
        gone_ = {b.get("sku") for b in sent} | set(hold)
        return [b for b in buy if b["sku"] not in gone_][:restock_window_fast.CARRY_LINES]
    except Exception:   # noqa: BLE001 - no carry: the usual run
        return []


def window_fast_arm(state, cfg, inv, now=None):
    """(arm, label) for the supply window inventory.json names: ("B", "default") when pricing.json restock.window_fast
    is true (false as shipped: 13.8 SKU lines a window against 12.2 over 32 windows was measured, but the economic
    verdict is open), else the study's arm and name while its switch is on, else (None, None)."""
    if str(os.environ.get("RESTOCK_WINDOW_FAST") or "").strip().lower() in ("0", "false", "no", "off"):
        return None, None       # the stop in the environment holds for the rule as for the study (review 9d5c7b1 M4)
    if cfg.get("window_fast") is True:
        return "B", "default"
    r = inv.get("restock") if isinstance(inv, dict) else None
    wfc = restock_window_fast.config(state, now=now)
    a = restock_window_fast.arm(wfc, r.get("opens_at") if isinstance(r, dict) else None)
    return (a, wfc["study"]) if a else (None, None)


def window_fast(state, inv, pricing, cfg, catalog, now=None, note=None):
    """restock_window_fast, arm B: {"buy": lines} from the carry the window's last run left, or None for the usual run.
    The ledger sync and the cash buffer are main()'s own (hard_limits.cash_buffer over the stock held and on its way and
    the warranted sales of the ledger's tail); the lines sent in the last PENDING_SECONDS count in full (sent_waiting);
    guard(), late_lines() and hard_limits.final_buy() have the last word as in every run. None whenever a step cannot
    be made exactly: no carry for this open window, a next_window plan that is not done, an opening phase, sent lines or
    sales that cannot be read, a failed sync, or no line left."""
    try:
        now = now or time.time()
        arm_, study = window_fast_arm(state, cfg, inv, now)
        if arm_ != "B":
            return None
        if (inv.get("round") or 0) < market_model.ECON_ROUND or (pricing.get("opening") or {}).get("phases"):
            return None
        trial = pricing.get("next_window") or {}
        hold = set(trial.get("targets") or {})
        if hold and trial.get("id"):         # a plan that is waiting for its window or running: its run is the usual one
            st = load(os.path.join(state, NEXT_WINDOW_STATE), {})
            if not isinstance(st, dict) or st.get("id") != trial["id"] or st.get("done") is not True:
                return None
        if restock_window_fast.take_carry(state, inv, now, mark=False) is None:
            return None
        waiting = sent_waiting(state, now)
        t0 = time.time()
        added = market_model.sync(state, max_pages=cfg.get("max_pages", market_model.DEFAULTS["max_pages"]))
        took = time.time() - t0
        sales = warranted_sales(state, catalog)
        if sales is None:
            return None
        stock = sum(u["cost"] for u in inv.get("units") or [] if isinstance(u, dict) and hard_limits.number(u.get("cost"))) \
            + sum(w["worst"] * w["qty"] for w in waiting)
        buffer = hard_limits.cash_buffer(stock, sales)
        if not hard_limits.number(buffer):
            return None
        if isinstance(note, dict):
            note["buffer"] = buffer         # main() hands the kit this floor with the lines (cash_note)
        carried = restock_window_fast.take_carry(state, inv, now)      # marked used: these lines are never carried twice
        if carried is None:
            return None
        lines, at = carried
        print(f"[restock] cash buffer {buffer} (balance {inv.get('balance')}, stock {stock:.0f}, "
              f"{len(sales)} warranted goods sales in {hard_limits.BREAK_HOURS} h)", file=sys.stderr, flush=True)
        buy = guard([b for b in lines if b["sku"] not in hold], inv, catalog, {**cfg, "cash_buffer": buffer}, None, waiting)
        buy = late_lines(state, inv, buy[:cfg["max_lines"]])
        owed = sum(w["worst"] * w["cash_qty"] for w in waiting)
        buy = hard_limits.final_buy(buy, inv.get("balance"), catalog, buffer=buffer + owed)
        print(f"[restock] window_fast {study}: {len(buy)} of the {len(lines)} lines the run of "
              f"{time.strftime('%H:%M:%S', time.localtime(at))} left for this window, ledger sync +{added} rows in "
              f"{took:.1f} s, no demand model this run", file=sys.stderr, flush=True)
        if not buy:
            return None
        try:
            note_sent(state, buy[:KIT_LINES], catalog, factors=surge(inv))
        except OSError as e:
            print(f"[restock] could not note the lines sent: {e}", file=sys.stderr, flush=True)
        return {"buy": buy}
    except Exception as e:   # noqa: BLE001 - the usual run decides
        print(f"[restock] window_fast: the usual run ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        return None


def forum_off_window(state, inv, now=None):
    """Run the unchanged due policy only outside the supply protection interval.
    The kit waits for process exit (market_kit.run_buyer), so printing early cannot
    remove this wait. Leave due state untouched when deferred; the next closed
    hook run retries normally. No worker, new call cap or external sender.
    """
    now = time.time() if now is None else now
    r = inv.get("restock")
    if isinstance(r, dict) and r.get("open") is False and hard_limits.number(r.get("opens_at")) \
            and r["opens_at"] - now > 150:
        return forum.maybe_run(state, rows=rows_of(state))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    inv = load(os.path.join(args.state, "inventory.json"), {})
    pricing = load("pricing.json", {})
    cfg = hard_limits.clamp_cfg("restock", {**DEFAULTS, **pricing.get("restock", {})})
    catalog = load(os.path.join(args.state, "catalog.json"), {})
    cfg["demand_catchup"] = restock_demand_catchup.config(args.state, inv)
    cfg["fresh_slots"] = restock_fresh_slots.config(args.state)      # its own switch file, None when off: today's count
    cfg["fresh_now"] = time.time()                                   # the run's one clock for that count
    cfg["sellable_slots"] = restock_sellable_slots.config(args.state)  # its own switch, None when off; over fresh_slots
    cfg["turnover_targets"] = restock_turnover_targets.config(args.state)
    if cfg["turnover_targets"]:
        cfg["sku_target_units"], raised = restock_turnover_targets.targets(cfg, cfg.get("sku_target_units"))
        print(f"[restock] turnover_targets {cfg['turnover_targets']['study']}: {len(raised)} listed SKUs with a larger target: "
              + ", ".join(f"{sku} {item['target']}->{item['boosted']}" for sku, item in sorted(raised.items())),
              file=sys.stderr, flush=True)
    cfg["breadth"] = restock_breadth.configs(args.state) or None
    if cfg["breadth"]:
        cfg["priority_skus"], cfg["sku_target_units"], added = restock_breadth.listing(cfg)
        studies = "+".join(on["study"] for on in cfg["breadth"])
        print(f"[restock] breadth {studies}: {len(added)} SKUs listed for the window: "
              + ", ".join(f"{sku} x{units}" for sku, units in added.items()), file=sys.stderr, flush=True)
    cfg["food_stockout"] = restock_food_stockout.config(args.state)
    if cfg["food_stockout"]:
        cfg["priority_skus"], cfg["sku_target_units"], added = restock_food_stockout.listing(cfg, catalog)
        print(f"[restock] food_stockout {cfg['food_stockout']['study']}: {len(added)} food SKUs listed for the window: "
              + ", ".join(f"{sku} x{units}" for sku, units in added.items()), file=sys.stderr, flush=True)
    if restock_sellable_slots.on(cfg):
        _, moved = restock_sellable_slots.counted([u for u in inv.get("units") or [] if isinstance(u, dict)
                                                   and not gone(u, time.time())], cfg, catalog, surge(inv))
        print(f"[restock] sellable_slots {cfg['sellable_slots']['study']}: {len(moved)} listed goods SKUs count fewer "
              f"units than held ({sum(m['held'] - m['counted'] for m in moved.values())} stuck units left out): "
              + ", ".join(f"{k} {m['held']}->{m['counted']}" for k, m in sorted(moved.items())), file=sys.stderr, flush=True)
    elif cfg["fresh_slots"]:
        _, moved = restock_fresh_slots.counted([u for u in inv.get("units") or [] if isinstance(u, dict)
                                                and not gone(u, time.time())], cfg, catalog, surge(inv))
        print(f"[restock] fresh_slots {cfg['fresh_slots']['study']}: {len(moved)} listed goods SKUs count fewer units "
              f"than held ({sum(m['held'] - m['counted'] for m in moved.values())} units waited past "
              f"{cfg['fresh_slots']['hours']:g} h): "
              + ", ".join(f"{k} {m['held']}->{m['counted']}" for k, m in sorted(moved.items())), file=sys.stderr, flush=True)
    cfg["first_slot"] = None
    cfg["first_stock"] = None  # only the finite reader below may enable this trial
    arm_, label = window_fast_arm(args.state, cfg, inv)       # (None, None) when off
    window_arm = "default" if label == "default" else arm_     # measure's tag: the rule is told from a study's arm B
    fast_b = arm_ == "B"
    fast_note = {}
    fast = window_fast(args.state, inv, pricing, cfg, catalog, note=fast_note)      # restock_window_fast, default off: None
    if fast is not None:
        cash = cash_note(fast["buy"][:KIT_LINES], inv, catalog, fast_note.get("buffer"))
        if cash:
            fast = {**fast, "cash": cash}
        print(json.dumps(fast, ensure_ascii=False), flush=True)
        measure.tick(args.state, inv, fast["buy"][:KIT_LINES], pricing, skip_sales=True,     # the 6 h sales wait for the usual run
                     window_arm=f"{window_arm}-fast")
        return 0
    demand, model = load(os.path.join(args.state, "demand.json"), []), read_model(args.state, catalog, cfg)
    cfg["first_stock"] = restock_first_stock.config(args.state, inv, model, catalog, listed(cfg))
    cfg["first_slot"] = restock_first_slot.config(args.state, inv, model, catalog, listed(cfg)) or \
        restock_first_slot.default_config(args.state, inv, model, catalog, listed(cfg))
    loss_watch.check(args.state, rows=rows_of(args.state))     # after read_model: the ledger rows are fresh
    breaks = read_breaks(args.state, catalog)
    blind = False
    try:
        waiting = pending(args.state, inv)     # sent lines not yet in inventory.json count as stock in every guard below
    except Exception as e:   # noqa: BLE001 - lines sent but not known: the cash they took is not known, so no buys
        print(f"[restock] pending lines unreadable, buying nothing: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        waiting, blind = [], True
    try:
        sales = warranted_sales(args.state, catalog)
    except Exception as e:   # noqa: BLE001 - the buffer then buys nothing (hard_limits.cash_buffer)
        print(f"[restock] own sales unreadable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        sales = None
    # the storage the buffer keeps is on the stock held and on the lines on their way (GPT Pro 11/07 04:30 S1)
    stock = sum(u["cost"] for u in inv.get("units") or [] if isinstance(u, dict) and hard_limits.number(u.get("cost"))) \
        + sum(w["worst"] * w["qty"] for w in waiting)
    try:
        cfg["cash_buffer"] = hard_limits.OPEN if blind else hard_limits.cash_buffer(stock, sales)   # a person, 11/06 19:03: no bankruptcy
    except Exception as e:   # noqa: BLE001 - never a crash: no buys instead
        print(f"[restock] cash buffer failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        cfg["cash_buffer"] = hard_limits.OPEN
    print(f"[restock] cash buffer {cfg['cash_buffer']} (balance {inv.get('balance')}, stock {stock:.0f}, "
          f"{'unknown' if sales is None else len(sales)} warranted goods sales in {hard_limits.BREAK_HOURS} h)",
          file=sys.stderr, flush=True)
    keep = lambda b: guard(b, inv, catalog, cfg, breaks, waiting)
    out = plan(inv, catalog, demand, cfg, model, breaks, waiting)
    ocfg = hard_limits.clamp_cfg("opening", {**opening.DEFAULTS, **pricing.get("opening", {})})
    first, why, trial = None, "not Round 3", None
    if inv.get("round", 0) >= market_model.ECON_ROUND and not out.get("bankrupt"):
        try:
            first, why = opening.lines(args.state, inv, catalog, demand, rows_of(args.state), ocfg, keep=keep)
        except Exception as e:   # noqa: BLE001 - a broken opening leaves the usual plan, not an idle shop
            first, why = None, f"opening failed: {type(e).__name__}: {e}"
        if first is None:       # next_window's lines join this run's (with_trial); once sent they no longer stop the window
            try:
                trial, more = next_window(args.state, inv, catalog, pricing.get("next_window") or {}, waiting, keep)
            except Exception as e:   # noqa: BLE001 - the same: the usual plan goes on
                trial, more = None, f"next_window failed: {type(e).__name__}: {e}"
            why = f"{why}; {more}"
        try:
            log_prices(args.state, inv, breaks, pricing)
        except Exception as e:   # noqa: BLE001 - a price log never stops a restock
            print(f"[restock] price log failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    print(f"[restock] {why}", file=sys.stderr, flush=True)
    trial_cfg = pricing.get("next_window") or {}
    hold = set(trial_cfg.get("targets") or {}) if trial is not None or trial_cfg.get("hold_after_done") is True else set()
    agent_skus = set()
    slot_plan_closed = False
    if fast_b and hold and first is None and not out.get("bankrupt"):
        with contextlib.redirect_stderr(io.StringIO()):     # the guard's lines were printed by the plan above
            out = plan(inv, catalog, demand, without_held(cfg, hold), model, breaks, waiting)
    if first is not None:                 # the opening decides this run (already guarded); the agent waits
        out = {"buy": first}
    elif (opening.window_closed(inv) or trial == [] and opening.window_closed(inv, lead=0)) and not out.get("bankrupt"):
        slot_plan_closed = True
        out = {"buy": []}                  # nothing can be bought now; the agent's queue keeps its lines for the window.
        # A trial waiting for its window keeps the lead time empty, as before: a line sent before it opens is refused (429)
        # but would count as pending and make next_window find its target already bought
        if os.environ.get("HARNESS") and ahead_due(inv):     # a due decision is made now, not on the window's first run
            try:
                agent_turn(args.state, inv, catalog, demand, cfg, model, send=False)
            except Exception as e:   # noqa: BLE001 - the window run decides then, as before
                print(f"[restock] agent ahead failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    elif trial:                            # this run sends the trial; the formula fills the room, the agent waits a run
        out = {"buy": with_trial(trial, out["buy"], inv, catalog, cfg, breaks, waiting, hold)}
    elif os.environ.get("HARNESS") and not out.get("bankrupt"):
        lines = agent_turn(args.state, inv, catalog, demand, cfg, model, due_minutes=2 * cfg["agent_every_minutes"])
        if lines is not None:
            queued = load(os.path.join(args.state, "restock_queue.json"), {}).get("lines") or []
            # the formula fills only once the agent's queue is sent, except the proven / priority SKUs it proposes:
            # those go first in either case (proven_first); agent lines they push past max_lines are queued again
            extra, small = fresh_extra(cfg, inv, catalog, waiting), small_food(catalog, inv, waiting)
            send, back = batch(lines, [] if queued else out["buy"], out["buy"], keep, cfg,
                               urgent_listed(cfg, inv, catalog, waiting, model if model and model.get("active") else None),
                               line_class(extra, small))                                   # extra: after the agent's too
            agent_skus = {x.get("sku") for x in lines}
            if hold and not trial:         # agent lines for the trial's SKUs wait in the queue until the trial ends
                back += [x for x in send if x["sku"] in hold and x["sku"] in agent_skus]
            requeue(args.state, back)
            out = {"buy": send}
    if hold and not trial:                 # the trial was sent earlier in this window: its SKUs stay the trial's
        out["buy"] = with_trial(None, out["buy"], inv, catalog, cfg, hold=hold)
    agent_memory.maybe_consolidate(args.state)
    if os.environ.get("HARNESS"):
        forum_off_window(args.state, inv)   # closed hooks keep the same due policy; no model wait during supply
        tool_forge.maybe_run(args.state)
        rival_vote.maybe_run(args.state)    # a free dislike stance on a tied Players rival's buyer, never a paid vote
    shop_forum.maybe_run(args.state, rows=rows_of(args.state))    # the shop's own posts, from its ledger rows and stock
    # the last word, whatever came before. It keeps its own 0.75 x list (test_hard_limits pins it); the surge cost is
    # reserved by guard() and the waiting lines, and with the balance far above STOP_BUYING_BELOW the line does not bind
    slot = cfg["first_slot"]
    late_agent_skus = agent_skus  # retain baseline provenance unless this slot introduces a new SKU
    # Never override an opening, selected next_window plan, fast path or old trial.
    if slot and not slot_plan_closed and first is None and not trial and restock_first_slot.SKU not in hold and not fast_b and not cfg["first_stock"] and not blind and not out.get("bankrupt"):
        previous = out.get("buy") or []
        chosen = restock_first_slot.select(slot, previous, inv, waiting, keep)[:min(cfg["max_lines"], KIT_LINES)]
        if slot.get("guard") == "passed":
            if not any(b["sku"] == restock_first_slot.SKU and b["qty"] > 0 for b in previous):
                late_agent_skus = agent_skus - {restock_first_slot.SKU}
            # Only displaced agent quantities return to their own queue; never the new target.
            remaining = collections.Counter()
            for b in chosen:
                remaining[b["sku"]] += b["qty"]
            back = []
            for b in previous:
                used = min(b["qty"], remaining[b["sku"]])
                remaining[b["sku"]] -= used
                if b["sku"] in agent_skus and b["sku"] != restock_first_slot.SKU and b["qty"] > used:
                    back.append({"sku": b["sku"], "qty": b["qty"] - used})
            requeue(args.state, back)
            out["buy"] = chosen
    if first is None:
        out["buy"] = late_lines(args.state, inv, out.get("buy"), late_agent_skus)
    owed = sum(w["worst"] * w.get("cash_qty", w["qty"]) for w in waiting)      # lines already sent are bought out of the same cash
    out["buy"] = hard_limits.final_buy(out.get("buy"), inv.get("balance"), catalog, buffer=cfg["cash_buffer"] + owed)
    if out["buy"]:
        try:
            note_sent(args.state, out["buy"][:KIT_LINES], catalog, factors=surge(inv))    # the kit sends no more
        except OSError as e:
            print(f"[restock] could not note the lines sent: {e}", file=sys.stderr, flush=True)
    if fast_b:      # restock_window_fast: the plan's next lines wait for this window's next run; none: the old carry goes
        carry = []
        if first is None and not trial and out["buy"] and not out.get("bankrupt") \
                and not (load(os.path.join(args.state, "restock_queue.json"), {}).get("lines") or []):
            carry = carry_lines(inv, catalog, demand, cfg, model, breaks, waiting, out["buy"][:KIT_LINES], hold)
        restock_window_fast.save_carry(args.state, inv, carry)
    restock_demand_catchup.record(args.state, cfg["demand_catchup"], out["buy"][:KIT_LINES])
    cash = cash_note(out["buy"][:KIT_LINES], inv, catalog, cfg["cash_buffer"])     # the kit checks each line against it again
    if cash:
        out["cash"] = cash
    print(json.dumps(out, ensure_ascii=False), flush=True)
    restock_first_slot.record(args.state, slot, out["buy"][:KIT_LINES])
    restock_first_stock.record(args.state, cfg["first_stock"], out["buy"][:KIT_LINES], inv, waiting)
    measure.tick(args.state, inv, out["buy"][:KIT_LINES], pricing,     # after the line the kit reads; local files only, never raises
                 window_arm=window_arm, rows=rows_of(args.state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
