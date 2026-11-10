"""Our restock, the kit's hook (Round 3): "restock": {"cmd": "py restock.py --state {state}"}.

The kit runs this every 60 s and reads the last JSON line {"buy": [{"sku", "qty"}, ...]}. Supply opens once every
20 minutes for 3 minutes (agent.md, 11/3; inventory.json "restock": open, opens_at, closes_at), and stock bought in
a window has to last until the next one. So the model is asked once per window: when the window is open or opens
within LEAD seconds (the kit waits up to 90 s per line for it). At other times nothing is bought and no model is
called. Without window information (an older market) it falls back to asking when something changed. The model
looks things up and ends with act.buy, which runs check.buy (cash floor, stock cap, units per SKU, food share).

Bankruptcy (HARNESS.md 6, user 2026-11-03): with a restock hook the kit files only when the hook asks, so
this files ("bankrupt": true) when the shop is stuck: below the kit's bankrupt line, filings left, and no
unit left to sell. Code decides it, not the model, and an urgent alert tells the user.
"""
import argparse
import collections
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents import loop  # noqa: E402
from core import card  # noqa: E402
from core import atomic, bus, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402
from tools import check  # noqa: E402
from tools import market_files as mf  # noqa: E402
from tools.registry import Ctx, call  # noqa: E402

ROUND_END = time.mktime(time.strptime("2026-11-17 23:59", "%Y-%m-%d %H:%M"))   # Round 3 ends (local time, KST)
TAPER_H = 48            # own_floor ends this long before the end: stock left at the end is lost, so we buy only what sells
STOCKUP_PLANS = 2       # while stocking up, runs per supply window (a window is 3 min, a full run ~100 s)
VALUE_LINES = 5         # while stocking up, this many of the kit's 10 lines go to expected sales value, the rest to demand
REASK_MINUTES = 20
LEAD = 75                  # plan this many seconds before a window opens at the latest (a model turn takes ~40 s)
WINDOW_MINUTES = 3
PLANS_PER_WINDOW = 4       # a plan of 10 lines takes the kit ~100 s (one supply call per 10 s); windows run 10 min (seen 11/3)
CANDIDATES = 40            # goods SKUs offered in the situation, one per demand category, so a plan needs no search
DEADLINE = 150             # the kit starts the next restock only after this one ended (and stops it at 300 s)


WINDOWS_PER_6H = 18        # supply opens about every 20 minutes
BUY_LIST_FRESH = 1200      # seconds a buy list the outer loop wrote stays good


def playbook(ctx):
    """User 11/4 02:0x (HARNESS 14): restock buys a list fixed in advance; the strategy's playbook sets its numbers
    and the strategy agent keeps changing them (delegated)."""
    pb = ((ctx.strategy.get("playbook") or {}).get("restock") or {})
    return {"mode": pb.get("mode", "rules"), "cover_windows": pb.get("cover_windows", 2),
            "market_top": pb.get("market_top", 5), "market_units": pb.get("market_units", 2),
            "max_line_share": pb.get("max_line_share", 1.0),
            # user 11/4 02:4x: sell food too - the market's best sellers, only food that keeps (shelf hours)
            "food_top": pb.get("food_top", 8), "food_units": pb.get("food_units", 3),
            "food_min_shelf_hours": pb.get("food_min_shelf_hours", 72),
            # user 11/4 10:3x: the best sellers deeper - the top_n goods lines by value may hold top_units and take
            # top_line_share of the money; other lines stay at units_cap
            "top_n": pb.get("top_n", 0), "top_units": pb.get("top_units", 20), "top_line_share": pb.get("top_line_share", 0.5),
            "units_cap": pb.get("units_cap", 12),
            "own_sales": pb.get("own_sales", False), "own_cover": pb.get("own_cover", 1.5),
            "own_floor": pb.get("own_floor", False), "react": pb.get("react", False),
            "probe_units": pb.get("probe_units", 2), "food_cap": pb.get("food_cap", 3),
            # 11/5 09:0x: food sized by its own sales per window (None = the fixed baseline above)
            "food_windows": pb.get("food_windows"), "food_fresh_share": pb.get("food_fresh_share", 0.5),
            "food_old_windows": pb.get("food_old_windows", 2), "food_min": pb.get("food_min", 2),
            "cost_share": check.cost_share(ctx)[0]}


def build_buy_list(ctx, now=None):
    """The buy list: our missed requests and our sales (last 6 h) turned into units per supply window, times
    cover_windows, then the market's best-selling goods we do not hold (market_units each). Most wanted first, so
    act.buy's trimming drops the least wanted. Written to memory/facts/buy_list.json for the outer side to read."""
    now = now or time.time()
    pb, lim = playbook(ctx), check._limits(ctx)
    cat = mf.catalog(ctx)
    have, fresh = {}, collections.Counter()
    for u in mf.inventory(ctx).get("units", []):
        if u.get("grade") != "expired":
            have[u["sku"]] = have.get(u["sku"], 0) + 1
            sh = (cat.get(u["sku"]) or {}).get("shelf_hours")
            h = mf.hours_left(u.get("expires_at"), now)
            if u.get("kind") == "food" and sh and h is not None and h / sh >= pb["food_fresh_share"]:
                fresh[u["sku"]] += 1
    missed = call(ctx, "me.missed", {"hours": 6})
    sell = call(ctx, "market.selling", {"hours": 6, "limit": 30})
    sell_food = call(ctx, "market.selling", {"hours": 6, "kind": "food", "limit": 30})
    want = {}
    def keeps(sku):         # goods always; food only when it keeps long enough to sell (playbook); nothing on hold
        c = cat.get(sku) or {}
        if mf.on_hold(ctx, sku, cat):
            return False
        left_h = (ROUND_END - ctx.now()) / 3600
        # user 11/6 10:4x (meta answer to Q2): food that spoils before the end is lost at cost, food still in date
        # counts at cost (patch 3.3) - on the last day buy only food that outlasts the round
        outlasts = left_h > 24 or (c.get("shelf_hours") or 0) > left_h
        return c.get("kind") == "goods" or (lim.get("food_share_max", 0) > 0 and c.get("kind") == "food"
                                            and (c.get("shelf_hours") or 0) >= pb["food_min_shelf_hours"]
                                            and outlasts)
    for x in (missed["result"].get("skus") or []) if missed["ok"] else []:
        if keeps(x["sku"]):
            want[x["sku"]] = want.get(x["sku"], 0) + x.get("asked", 0)
    for x in (sell["result"].get("ours") or []) if sell["ok"] else []:
        if keeps(x["sku"]):
            want[x["sku"]] = want.get(x["sku"], 0) + x.get("sold", 0)
    lines = []
    # 11/4 03:0x benchmark (winun1127, dae-yangkim): profit comes from deep stock of the best sellers with a high
    # list price, so the list is ordered by expected sales value (asked/sold x list price), not by count
    for sku, n in sorted(want.items(), key=lambda kv: -kv[1] * ((cat.get(kv[0]) or {}).get("list_price") or 0)):
        if sku not in cat:
            continue
        per = pb["food_units"] if cat[sku].get("kind") == "food" else lim["max_units_per_sku"]
        target = max(1, min(per, lim["max_units_per_sku"], math.ceil(pb["cover_windows"] * n / WINDOWS_PER_6H)))
        lines.append({"sku": sku, "target": target, "have": have.get(sku, 0), "why": f"wanted {n} in 6 h",
                      "value": n * (cat[sku].get("list_price") or 0)})
    market = sorted((x for x in ((sell["result"].get("market") or []) if sell["ok"] else [])
                     if x.get("kind") == "goods" and x["sku"] in cat),
                    key=lambda x: -x.get("sold", 0) * (cat[x["sku"]].get("list_price") or 0))
    by_sku = {x["sku"]: x for x in lines}

    def add(sku, target, why, value):
        # 11/4 03:3x: one line per SKU with the larger target and value - G1515d951 (the market's best seller) came
        # in as "wanted 1" after one sale of ours and its market line was dropped, so it ranked last
        x = by_sku.get(sku)
        if x is None:
            x = by_sku[sku] = {"sku": sku, "target": target, "have": have.get(sku, 0), "why": why, "value": value}
            lines.append(x)
        elif value > x["value"] or target > x["target"]:
            x.update(target=max(target, x["target"]), value=max(value, x["value"]), why=why if value > x["value"] else x["why"])
    for x in market[:pb["market_top"]]:
        add(x["sku"], min(lim["max_units_per_sku"], pb["market_units"]), f"market sold {x.get('sold')} in 6 h",
            x.get("sold", 0) * (cat[x["sku"]].get("list_price") or 0))
    food = [x for x in ((sell_food["result"].get("market") or []) if sell_food["ok"] else [])
            if x.get("kind") == "food" and x["sku"] in cat and keeps(x["sku"])]
    for x in food[:pb["food_top"]]:
        add(x["sku"], min(lim["max_units_per_sku"], pb["food_units"]), f"market sold {x.get('sold')} food in 6 h",
            x.get("sold", 0) * (cat[x["sku"]].get("list_price") or 0))
    # 11/4 03:3x: what the top shops sell that we lack (outer/benchmark.py) - the strongest sign which SKUs orders take
    bench = atomic.read_json(paths.memory() / "facts" / "benchmark.json", default=None) or {}
    for sku, sold, lp in (bench.get("skus") or {}).get("their_best_we_lack", []):
        if sku in cat and keeps(sku):
            per = pb["food_units"] if cat[sku].get("kind") == "food" else pb["market_units"]
            add(sku, min(lim["max_units_per_sku"], per), f"top shops sold {sold} in 6 h", sold * (lp or 0))
    # user 11/4 10:3x: goods orders our buyer saw that no shop could serve (memory/facts/unserved.jsonl): stock the
    # SKU of each that sells most in the market and fits its budget at our usual price
    uf = paths.memory() / "facts" / "unserved.jsonl"
    if uf.exists():
        sold = {x["sku"]: x.get("sold", 0) for x in ((sell["result"].get("market") or []) if sell["ok"] else [])}
        for line in uf.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                u = json.loads(line)
            except ValueError:
                continue
            if u.get("ts", 0) < now - 6 * 3600:
                continue
            fit = [k for k in u.get("skus") or [] if k in cat and cat[k].get("kind") == "goods"
                   and cat[k].get("list_price") and 0.75 * cat[k]["list_price"] <= (u.get("budget") or 0)]
            if fit:
                k = max(fit, key=lambda k: (sold.get(k, 0), -cat[k]["list_price"]))
                add(k, 2, f"an order no shop could serve ({u.get('category')})", cat[k]["list_price"])
    lines.sort(key=lambda x: -x["value"])               # one order for all: expected sales value
    top = [x for x in lines if cat[x["sku"]].get("kind") == "goods"][:pb["top_n"]]
    for x in lines:
        if x in top:
            x["top"] = True
            # 11/4 14:0x: deep as the demand shows - a sixth of what sold or was asked in 6 h, at most top_units
            # (a flat 20 planned 10 sofas of 568,080 the market had sold 21 of)
            n = x["value"] / max(1, cat[x["sku"]].get("list_price") or 1)
            x["target"] = min(lim["max_units_per_sku"], pb["top_units"], max(x["target"], math.ceil(n / 6)))
        else:
            x["target"] = min(x["target"], pb["units_cap"])
    # user 11/4 15:0x: depth follows OUR sell-through. 14:00-15:00 G1515d951 sold all 32 we bought while 10 sofas
    # (G5e3784e6) chosen by the market's sales sold none. Goods: up to 1.5x what we sold in the last 2 h (fast sellers
    # are filled even outside the top 3), nothing more of a SKU we hold that does not sell, 2 to try a new one.
    if pb["own_sales"]:
        o2 = call(ctx, "market.selling", {"hours": 2, "limit": 30})
        own = {x["sku"]: x.get("sold", 0) for x in ((o2["result"].get("ours") or []) if o2["ok"] else [])}
        # 11/4 15:4x: demand = what we sold + what visitors asked for and found missing, both in 2 h - counting sales
        # alone under-bought what kept selling out (G6b9db161: 5 sold, 17 asked and missed; water G7fa48eaa: 20 asked)
        missed = collections.Counter()
        for f in (paths.memory() / "raw" / "seller").glob("*.jsonl"):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if '"seller.missed"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("ts", 0) >= now - 7200:
                        for k in (e.get("data") or {}).get("skus") or []:
                            missed[k] += 1
        for x in lines:
            kind = cat[x["sku"]].get("kind")
            n = own.get(x["sku"], 0) + missed.get(x["sku"], 0)
            want_units = math.ceil(n * pb["own_cover"])
            if kind == "goods":
                probe = pb["probe_units"] if (x["have"] == 0 and n == 0) else 0
                if pb["own_floor"] and (ROUND_END - now) / 3600 > TAPER_H:
                    # user 11/10 15h ("stock up fast"): our 2 h sales only raise a goods line - cutting the market's
                    # and top lines to them bought 1-6M a window while 266M cash sat idle (32 sales/h vs 90-220)
                    x["target"] = min(lim["max_units_per_sku"], max(x["target"], want_units, probe))
                else:
                    x["target"] = min(lim["max_units_per_sku"], max(want_units, probe))
            elif kind == "food" and pb["food_windows"]:
                # 11/5 09:0x (user: food fresh, goods by their own cover; old food must not pile up): food is bought
                # for food_windows windows of its own sales (2 h = 6 windows), and only units with food_fresh_share
                # of the shelf left count as held - so fresh units come in while old ones sell to short stated needs.
                # A fixed 8-10 held 12-16 windows of a slow SKU (kimchi on hold). When the old units already cover
                # food_old_windows windows, nothing more is bought; the per-SKU limit still counts every unit.
                per = n / 6
                x["target"] = min(lim["max_units_per_sku"], pb["food_cap"],
                                  max(pb["food_min"], math.ceil(per * pb["food_windows"])))
                old = x["have"] - fresh.get(x["sku"], 0)
                x["have"], x["old"] = fresh.get(x["sku"], 0), old
                if old > math.ceil(per * pb["food_old_windows"]):
                    x["target"] = x["have"]          # the old units go first
            elif kind == "food":                    # food keeps its baseline and grows with demand, up to food_cap
                x["target"] = min(lim["max_units_per_sku"], pb["food_cap"], max(x["target"], want_units))
            x["own_sold_2h"], x["missed_2h"] = own.get(x["sku"], 0), missed.get(x["sku"], 0)
        # the money goes first to what sells and is asked for most (then value): water asked 20 times came last
        # behind a 2.2M sofa that sells a few, and the cash ran out before it
        lines.sort(key=lambda x: (-(x.get("own_sold_2h", 0) + x.get("missed_2h", 0)), -x["value"]))
        if pb["own_floor"] and (ROUND_END - now) / 3600 > TAPER_H:
            # 11/10 17:2x: with cash to spare the kit's 10 lines went to water, kimchi and cushions sold this hour and
            # the high-list best sellers never got a line - while stocking up, expected sales value comes first
            # 18:5x: value alone left the fast food out (48 visits an hour asked for food we lacked, sales 37 -> 16 an
            # hour): VALUE_LINES lines by value, the rest by what sells and is asked for, as before
            deep = sorted(lines, key=lambda x: -x["value"])[:VALUE_LINES]
            lines[:] = deep + [x for x in lines if x not in deep]
    # User 11/4 02:1x: plan within the money we may spend - each line gets what the cash and the stock cap allow
    from tools import act
    room = check.room(ctx)
    money = min(max(0, room["cash"] or 0), max(0, room["stock"]))
    cap = pb["max_line_share"] * money                   # one line may take at most this much of the window's money
    def line_units(x):
        lack, w = x["target"] - x["have"], room["worst"](x["sku"])
        if pb["max_line_share"] >= 1 or not w:
            return max(0, lack)
        # 11/4 05:0x: the cap kept G1515d951 (asked 33 times in 30 min, 1 held) at 0 units because one unit costs
        # more than the cap; a line that fits the money gets at least one unit
        c = pb["top_line_share"] * money if x.get("top") else cap
        return max(0, min(lack, max(int(c // w), 1 if w <= money else 0)))
    wants = [(x["sku"], line_units(x)) for x in lines]
    got = act.allocate(room, wants)
    for x, (_, q, why) in zip(lines, got):
        x["buy"], x["short"] = q, (why if q < x["target"] - x["have"] else None)
    doc = {"ts": now, "playbook": pb, "lines": lines,
           "budget": {"cash": room["cash"], "stock": round(room["stock"]), "balance": room["balance"],
                      "floor": room["floor"], "reserve": room["reserve"],
                      "planned": sum(q * room["worst"](sku) for sku, q, _ in got),
                      "planned_worst": sum(q * room["unit_worst"](sku) for sku, q, _ in got)}}
    atomic.write_json(paths.memory() / "facts" / "buy_list.json", doc)
    return doc


def rules_plan(ctx, now=None):
    """(plan, why, list): the buy list planned now (fresh stock and money), within the room. why: None when there
    is something to buy, 'full' when every line has its target, 'no_budget' when the money or the cap stops all."""
    doc = build_buy_list(ctx, now)
    plan = [{"sku": x["sku"], "qty": min(20, x["buy"])} for x in doc["lines"] if x["buy"] > 0][:10]
    if plan:
        # C10 (design 16-1): the rules buy through the same call() as the model, so the plan is on the record;
        # act.buy fits the plan to the room again (a no-op for a list built within it) and runs check.buy
        r = call(ctx, "act.buy", {"plan": plan, "why": "rules"})
        if not r["ok"]:
            raise check.Refused(r["reason"], r.get("hint", ""))
        return r["result"]["plan"], None, doc
    lacking = [x for x in doc["lines"] if x["target"] > x["have"]]
    return [], ("no_budget" if lacking else "full"), doc


REACT_S = 55               # the ReAct plan's budget inside a 3-minute window (the kit needs ~100 s for 10 lines)
REACT_STEPS = 3


def react_situation(ctx, doc, draft, now):
    """The ReAct planner's view: the rules' candidate lines and draft, the money, the score and the time left."""
    cat, b = mf.catalog(ctx), doc["budget"]
    left_h = (ROUND_END - now) / 3600
    out = ["# the decision at hand: this supply window's restock plan (the window is open now, about 3 minutes)",
           "Score: shop ALL = 0.5 book (cash + unsold stock at cost) + 0.3 revenue + 0.2 stars, each a percentile. "
           "Stock still unsold when the round ends (11/17 23:59) is lost. The top shops hold 130-720M of stock in "
           "high-list mattresses and sofas and sell 90-220 deals an hour; we want more sales, of both the fast cheap "
           "lines buyers keep asking for and the high-list best sellers.",
           f"Hours left in the round: {left_h:.0f}. Stocking up: {'yes' if left_h > TAPER_H else 'no - buy only what sells'}.",
           f"Money: cash {b['cash']}, room under the stock cap {b['stock']}, cash floor {b['floor']}. Costs are drawn "
           "on arrival (about 62% of list, at most 75%). The next window opens in about 20 minutes.",
           "", "Candidate lines (sku | kind | list price | have | rules' target | our sales 2 h | asked & missed 2 h | "
               "why | name):"]
    for x in doc["lines"][:30]:
        c = cat.get(x["sku"]) or {}
        out.append(f"- {x['sku']} | {c.get('kind')} | {c.get('list_price')} | {x['have']} | {x['target']} | "
                   f"{x.get('own_sold_2h', '-')} | {x.get('missed_2h', '-')} | {x['why']} | {(c.get('name') or '')[:30]}")
    out += ["", f"The rules' draft plan: {json.dumps(draft)}",
            "", "Decide the plan: at most 10 lines, 1-20 units each, within the per-SKU limit and the money. Look up "
                "only what would change your plan (market.selling, me.missed, market.sku, check.buy), then end with "
                "act.buy and a one-line why. The draft is a fine answer when you find nothing better."]
    return "\n".join(out)


def react_plan(ctx, now, w):
    """(plan, record): the model plans the window with the rules' list as its draft; the draft when it fails."""
    draft, why, doc = rules_plan(ctx, now)
    d, trace = loop.decide("restock", ctx, react_situation(ctx, doc, draft, now), deadline=now + REACT_S,
                           steps=REACT_STEPS)
    plan = d["plan"] if d else draft
    rec = {"lines": len(plan), "plan": plan, "refused": next((t["observation"][:200] for t in trace
                                                              if "refused" in str(t["observation"])), None),
           "used_model": d is not None, "react": True, "draft": draft, "why": (d or {}).get("why"),
           "steps": len(trace), "budget": doc["budget"], "guard": None if d else "no plan from the model: the draft"}
    return plan, rec


def stuck(inv):
    live = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    return ((inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0) and (inv.get("bankruptcies_left") or 0) > 0
            and not live)


def window(inv_raw, now):
    """(state, minute the window opens) from inventory.json's restock: ('open'|'soon'|'closed'|None, minute|None)."""
    r = inv_raw.get("restock") if isinstance(inv_raw.get("restock"), dict) else None
    if not r:
        return None, None
    if r.get("open"):
        start = (r.get("closes_at") or now) - WINDOW_MINUTES * 60
        return "open", int(start // 60)
    opens = r.get("opens_at")
    if opens and 0 <= opens - now <= LEAD:
        return "soon", int(opens // 60)
    return "closed", int(opens // 60) if opens else None


def signature(ctx):
    inv = call(ctx, "me.inventory", {})["result"]
    sold = call(ctx, "market.selling", {"hours": 2})["result"]["our_deals"]
    return {"units": inv["units"], "expiring": len(inv["expiring_12h"]), "sold_2h": sold,
            "balance_band": (inv["balance"] or 0) // 200_000, "strategy": ctx.strategy.get("version")}, inv


def candidates(ctx, held_skus, room, first=(), asked=()):
    """One goods SKU per goods demand category (nearest the category's median list price, and at most a tenth of the
    room so one line cannot eat it): categories named in `first` first, then the ones with the most SKUs."""
    cat = mf.catalog(ctx)
    by = {}
    for s, c in cat.items():
        if c.get("kind") == "goods" and s not in held_skus:
            by.setdefault(c.get("category"), []).append(c)
    rows = []
    for d in mf.read(ctx, "demand.json", default=[]) or []:
        if d.get("kind") != "goods" or d.get("category") not in by:
            continue
        cap = max(room / 10, 1) / 0.75                    # worst-case cost is 75% of list
        fit = [c for c in by[d["category"]] if c.get("list_price") and c["list_price"] <= cap]
        if not fit:
            continue
        med = d.get("median_price") or 0
        c = min(fit, key=lambda c: abs(c["list_price"] - med))
        rows.append({"sku": c["sku"], "name": (c.get("name") or "")[:40], "category": d.get("item"),
                     "list_price": c["list_price"], "worst_cost": round(c["list_price"] * 0.75),
                     "first": any(f and f in (d.get("category") or "") for f in first), "skus": d.get("skus") or 0})
    rows.sort(key=lambda r: (not r["first"], -r["skus"]))     # asked-for first, then the categories with most SKUs
    per = (ctx.strategy.get("restock") or {}).get("per_asked_category")
    if per:
        # User 11/3 22:06: buy what buyers need, for more deals. An order takes only certain SKUs of its category, so
        # the SKUs buyers asked us for come first (most asked first, held or not), then more SKUs of the asked-for
        # categories near the category's median price (cheapest-first picked sprays and gum from noisy categories);
        # categories nobody asked for are left out.
        demand = {x.get("item"): x for x in mf.read(ctx, "demand.json", default=[]) or []}
        top = [dict(sku=s, name=(cat[s].get("name") or "")[:40], category=(cat[s].get("category") or "").split(">")[-1],
                    list_price=cat[s].get("list_price"), worst_cost=round((cat[s].get("list_price") or 0) * 0.75),
                    first=True, skus=0)
               for s in asked if s in cat and cat[s].get("kind") == "goods"]
        extra = []
        for r in rows:
            if not r["first"]:
                continue
            d = demand.get(r["category"]) or {}
            med = d.get("median_price") or r["list_price"]
            more = sorted((c for c in by.get(d.get("category"), []) if c.get("list_price")
                           and c["sku"] not in asked), key=lambda c: abs(c["list_price"] - med))[:per]
            extra += [dict(r, sku=c["sku"], name=(c.get("name") or "")[:40], list_price=c["list_price"],
                           worst_cost=round(c["list_price"] * 0.75)) for c in more]
        rows = top + extra
    return rows[:CANDIDATES]


def situation(ctx, inv):
    lim = ctx.strategy.get("limits") or {}
    start = inv.get("start") or 5_000_000
    sell = call(ctx, "market.selling", {"hours": 6})["result"]
    held, held_skus = {}, set()
    for u in mf.inventory(ctx).get("units", []):
        if u.get("grade") != "expired":
            c = mf.catalog(ctx).get(u["sku"]) or {}
            held[c.get("category") or u["sku"]] = held.get(c.get("category") or u["sku"], 0) + 1
            held_skus.add(u["sku"])
    lines = ["# the decision at hand",
             f"Phase {ctx.strategy.get('phase')}. Balance {inv['balance']} (floor {lim.get('cash_floor')}); stock "
             f"{inv['units']} units worth {inv['stock_value']} (cap {round((lim.get('max_stock_share') or 0) * start)}); "
             f"food share {inv['food_share']} (max {lim.get('food_share_max')}); at most {lim.get('max_units_per_sku')} "
             f"per SKU. Expiring within 12 h: {inv['expiring_12h'][:8]}.",
             f"Selling in the last 6 h: ours {sell['ours'] or 'none'}; market {sell['market'] or sell['note']}.",
             "Categories we hold now (units): " + (", ".join(f"{k.split('>')[-1]} {n}" for k, n in held.items()) or "none"),
             f"Asked at our shop but not in stock (last 6 h): {call(ctx, 'me.missed', {'hours': 6})['result']}",
             "Wanted posts on the forum (marked excerpts): " + (" | ".join(
                 p["excerpt"] for q in ("구합니다", "wanted", "looking for")
                 for p in call(ctx, "market.forum", {"query": q, "limit": 4})["result"]["posts"]) or "none"),
             "", "Demand categories (categories orders may come from, not what sells; item, kind, median list price, "
             "SKUs in the catalog):"]
    for d in mf.read(ctx, "demand.json", default=[]) or []:
        lines.append(f"- {d.get('item')} ({d.get('kind')}, {d.get('median_price')}, {d.get('skus')})")
    r = mf.inventory(ctx).get("restock") or {}
    if r:
        nxt = r.get("opens_at")
        lines.append(f"\nSupply window: {'open until ' + time.strftime('%H:%M:%S', time.localtime(r['closes_at'])) if r.get('open') and r.get('closes_at') else 'opens ' + (time.strftime('%H:%M:%S', time.localtime(nxt)) if nxt else 'soon')}. "
                     "Supply opens about every 20 minutes; what you buy now has to last until the next window.")
    wf, bc = ctx.strategy.get("welcome_food") or {}, lim.get("below_cost") or {}
    if wf:
        lines.append(f"Welcome Food (strategy): each window brings food for first-time buyers (list price up to "
                     f"{bc.get('max_list_price')}, discounted only while grade {bc.get('grades')} now). What arrives at "
                     "which grade is the market's draw; market.buyer_prefs shows which food categories buyers want.")
    start_cap = round((lim.get("max_stock_share") or 0) * start)
    reserve, open_total, open_n = check.warranty_reserve(ctx)
    room = max(0, min(start_cap - (inv["stock_value"] or 0),
                      (inv["balance"] or 0) - (lim.get("cash_floor") or 0) - reserve))
    missed = call(ctx, "me.missed", {"hours": 6})["result"]
    first = [c["category"] for c in missed.get("categories") or []]
    # what buyers asked us for, then (user 11/3 22:3x: hold the market's best sellers deep) what sells: ours, then the
    # market's goods
    cat = mf.catalog(ctx)
    asked = [s["sku"] for s in missed.get("skus") or [] if s.get("sku")]
    asked += [s["sku"] for s in (sell.get("ours") or []) + (sell.get("market") or [])
              if isinstance(s, dict) and (cat.get(s.get("sku")) or {}).get("kind") == "goods" and s["sku"] not in asked]
    rows = candidates(ctx, held_skus, room, first, asked)
    lines.append(f"\nRoom to stock now: about {room} (the stock cap {start_cap} minus stock {inv['stock_value']}, and "
                 f"the balance above the floor"
                 + (f" and the warranty reserve {reserve} for {open_n} sales ({open_total}) still in warranty"
                    if reserve else "")
                 + "; costs are drawn on arrival, at most 75% of list).")
    if rows:
        lines.append("Goods candidates, one per demand category (ones buyers asked us for first; sku, list price, "
                     "worst-case cost, category, name):")
        lines += [f"- {r['sku']} {r['list_price']} {r['worst_cost']} {r['category']}: {r['name']}" for r in rows]
    lines.append("\nPick from the candidates or find others with market.sku, then act.buy a plan of up to 10 lines "
                 "(or an empty plan). This window allows a few plans in a row. The kit buys one line about every "
                 "10 s, so a line of several units (up to the per-SKU limit) stocks more in the same window.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state")
    a = ap.parse_args()
    if a.state:
        import os
        os.environ.setdefault("MARKET_DIR", a.state)
    t0 = time.time()
    tool_w, w = EventWriter("restock", source="tools"), EventWriter("restock", source="restock")
    ctx = Ctx(caller="restock", _writer=tool_w)
    try:
        w.context(strategy_version=ctx.strategy.get("version"), epoch=ctx.strategy.get("epoch"),
                  card_version=card.load()[0])
        inv_raw = mf.inventory(ctx)
        if stuck(inv_raw):
            bus.alert("restock", "urgent", "bankrupt", "below the bankrupt line with nothing to sell: filed for "
                      "bankruptcy (the money given counts against profit)",
                      {"balance": inv_raw.get("balance"), "left": inv_raw.get("bankruptcies_left")})
            w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False, "bankrupt": True})
            print(json.dumps({"buy": [], "bankrupt": True}))
            return
        try:                                    # design 17-4: a pre-approved emergency stop (harness.ops)
            from harness import ops
            halt = ops.emergency().get("restock_stop")
        except Exception:
            halt = None
        if halt:
            w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False, "emergency": halt})
            print(json.dumps({"buy": []}))
            return
        stop = (ctx.strategy.get("restock") or {}).get("stop_after")
        if stop and t0 >= stop:
            # User 11/3: the board counts unsold stock as zero profit, so stock is sold down before the round ends
            w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False, "stopped": stop})
            print(json.dumps({"buy": []}))
            return
        sig, inv = signature(ctx)
        state_path = paths.memory() / "facts" / "restock_state.json"
        last = atomic.read_json(state_path, default=None) or {}
        win, minute = window(inv_raw, t0)
        if win is not None:                              # the market's supply windows decide when
            same = last.get("window") is not None and minute is not None and abs(minute - last["window"]) <= 2
            done = same and last.get("plans_in_window", 1) >= (STOCKUP_PLANS if playbook(ctx)["own_floor"] else PLANS_PER_WINDOW)
            if win == "closed" or done:
                w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False,
                                        "window": win, "window_done": done})
                print(json.dumps({"buy": []}))
                return
            sig["window"] = minute
        elif last.get("sig") == sig and t0 - last.get("ts", 0) < REASK_MINUTES * 60:
            w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False})
            print(json.dumps({"buy": []}))
            return
        if playbook(ctx)["mode"] == "rules" and win == "soon":
            # 11/4 02:02: a plan made in 'soon' sent its first line 4 s before the window opened (429); the rules
            # need no lead time, so they wait for 'open' without using up the window
            w.emit("restock.plan", {"lines": 0, "refused": None, "used_model": False, "rules": True, "window": win})
            print(json.dumps({"buy": []}))
            return
        if win == "open":
            # user 11/7 10:4x: method 'alternate' - this window's arm per running experiment; a B window buys under
            # the B playbook (harness.experiment.assign_window), and the plan records the arms
            try:
                from harness import experiment
                arms, patch = experiment.assign_window(minute, t0)
                if arms:
                    ctx._strategy = experiment._merge(ctx.strategy, patch)
                    w.emit("restock.arm", {"experiment": arms, "window": minute})
            except Exception as e:                   # the ledger must never stop a restock: buy as A
                w.emit("exec.anomaly", {"role": "restock", "kind": "experiment_arm", "then": "arm A",
                                        "situation": {"error": f"{type(e).__name__}: {e}"[:200]}})
        first_in_window = not (last.get("window") is not None and minute is not None
                               and abs(minute - last["window"]) <= 2)
        if playbook(ctx)["mode"] == "rules" and playbook(ctx)["react"] and win == "open" and first_in_window:
            # user 11/10 20h: restock as a ReAct agent - the model plans the window's first run with tools, the rules'
            # list is its draft and its fallback; a second run in the window stays with the rules
            plan, rec = react_plan(ctx, t0, w)
            atomic.write_json(state_path, {"sig": sig, "ts": t0, "window": minute, "plans_in_window": 1})
            w.emit("restock.plan", rec)
            print(json.dumps({"buy": plan}))
            return
        if playbook(ctx)["mode"] == "rules":
            # HARNESS 14: buy the list at once, once per window; the agent only when the list gives nothing
            plan, why, doc = rules_plan(ctx, t0)
            # user 11/10 19h: the kit takes 10 lines a run, one call per 10 s, and runs us again 60 s later - while
            # stocking up a second run goes in the same 3-minute window with the lines the time left allows
            again = playbook(ctx)["own_floor"] and (ROUND_END - t0) / 3600 > TAPER_H
            n_win = (last.get("plans_in_window", 0) + 1) if (again and last.get("window") is not None
                                                             and minute is not None and abs(minute - last["window"]) <= 2) else 1
            closes = (inv_raw.get("restock") or {}).get("closes_at") if isinstance(inv_raw.get("restock"), dict) else None
            if n_win > 1 and closes:
                plan = plan[:max(0, int((closes - t0) // 10) - 1)]
            atomic.write_json(state_path, {"sig": sig, "ts": t0, "window": minute,
                                           "plans_in_window": n_win if again else PLANS_PER_WINDOW})
            w.emit("restock.plan", {"lines": len(plan), "plan": plan, "refused": None, "used_model": False,
                                    "rules": True, "why": why, "budget": doc["budget"]})
            if why == "no_budget":                       # the strategy has to know: the money is tied up
                w.emit("exec.anomaly", {"role": "restock", "kind": "no_budget", "then": "nothing bought",
                                        "situation": {"budget": doc["budget"],
                                                      "lacking": [(x["sku"], x["target"] - x["have"]) for x in doc["lines"]
                                                                  if x["target"] > x["have"]][:8]}})
            if plan or doc["lines"]:                     # the agent only when there is no list at all
                print(json.dumps({"buy": plan}))
                return
            w.emit("exec.anomaly", {"role": "restock", "kind": "no_buy_list", "then": "agent", "situation": {}})
        d, trace = loop.decide("restock", ctx, situation(ctx, inv), deadline=t0 + DEADLINE)
        plan = d["plan"] if d else []
        if d is not None:                               # a failed turn is tried again next time
            n = (last.get("plans_in_window", 0) + 1) if (minute is not None and last.get("window") is not None
                                                          and abs(minute - last["window"]) <= 2) else 1
            atomic.write_json(state_path, {"sig": sig, "ts": t0, "window": minute, "plans_in_window": n})
        refused = next((t["observation"][:200] for t in trace if "refused" in str(t["observation"])), None)
        w.emit("restock.plan", {"lines": len(plan), "plan": plan, "refused": refused, "used_model": True,
                                "why": (d or {}).get("why") or (d or {}).get("thought"),
                                "guard": None if d else "no valid plan from the model"})
        print(json.dumps({"buy": plan}))
    finally:
        w.close()
        tool_w.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"restock crashed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        print(json.dumps({"buy": []}))
