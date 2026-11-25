"""Restock and bankruptcy rules (strategy D1, D2, D4, D10, D14, L1, Patch 3.2 surge). Pure functions except
own_card_ok.

plan_restock decides which SKUs to buy and how many; the kit buys them (at most 10 lines, stopping at the
first line the balance cannot pay). Grades and costs are drawn on arrival, so affordability uses the worst
cost (stock.worst_cost_share x list price x the expected surge factor). Supply opens about every 20 minutes for
3 minutes, so each SKU is stocked to cover its expected sales until the next window (stock.cover_min x
stock.cover_safety), held to stock.max_hold units and bought at most stock.max_qty units a line.
"""
import collections
import math

import httpx

from memory.query import (c_kind, category_stats, market_covered_h, market_rate, market_settles_48h, p_clear,
                          regular_categories, sku_stats, supply_1h, supply_demand)
from memory import demand as demand_model
from memory.store import hook_log
from tools import pricing


def _leaf(category):
    return str(category or "").split(">")[-1]


def _units(inv):
    return [u for u in (inv or {}).get("units") or [] if isinstance(u, dict) and u.get("grade") != "expired"]


SUPPLY_GAP_S = 11                  # measured mean 10.8 s between supply calls: 10 s limit + the kit's refresh
CLOSE_MARGIN_S = 2
F_MAX_CAP = 1.25                   # never buy above this surge factor, whatever the margin says
FOOD_COVER_SHELF = 0.25            # food holds at most this share of its shelf life in expected sales
PROBE_SALES = 2                    # a big-ticket SKU leaves its probe after this many of our sales in 6 h
NO_SALES_MIN_H = 24                # 'no_market_sales' needs the market fold to cover this many hours


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def supply_window(inv, now):
    """inventory.json's supply window: {"open", "opens_at", "closes_at", "in_s", "closes_in_s"}, the last two in
    seconds from now or None. No "restock" (an older market): open is None, read as open."""
    r = (inv or {}).get("restock")
    r = r if isinstance(r, dict) else {}
    at, to = r.get("opens_at"), r.get("closes_at")
    return {"open": r.get("open"), "opens_at": at, "closes_at": to,
            "in_s": int(round(at - now)) if _finite(at) else None,
            "closes_in_s": int(round(to - now)) if _finite(to) else None}


def lines_that_fit(win):
    """How many supply lines an open window still takes before it closes; None when unknown."""
    if win["open"] is not True or win["closes_in_s"] is None:
        return None
    return max(0, math.ceil((win["closes_in_s"] - CLOSE_MARGIN_S) / SUPPLY_GAP_S))


def window_closed(win, pol):
    """Closed and not opening within stock.window_lead_s: this run buys nothing."""
    return win["open"] is False and win["in_s"] is not None and win["in_s"] > pol["stock"]["window_lead_s"]


def _surge_rows(inv):
    s = (inv or {}).get("surge")
    return [x for x in s if isinstance(x, dict) and isinstance(x.get("sku"), str)] if isinstance(s, list) else []


def surge_factors(inv):
    """{sku: factor} from inventory.json `surge` (a list of {sku, name, units, factor}); bad rows ignored."""
    return {x["sku"]: float(x["factor"]) for x in _surge_rows(inv) if _finite(x.get("factor")) and x["factor"] > 0}


def surge_units(inv):
    """{sku: units the whole market bought in the last hour} from inventory.json `surge` (SKUs above the free
    units only); bad rows ignored."""
    return {x["sku"]: int(x["units"]) for x in _surge_rows(inv) if _finite(x.get("units")) and x["units"] >= 0}


def held(inv):
    """{"skus": distinct SKUs in stock, "units": units in stock}, expired units left out."""
    units = _units(inv)
    return {"skus": len({u.get("sku") for u in units}), "units": len(units)}


def _kind(x):
    k = x.get("kind")
    return k if k in ("food", "goods") else "food" if x.get("shelf_hours") else "goods"


def big_ticket(x, pol):
    """Goods listed above stock.big_ticket_list (x: a catalog entry or a unit)."""
    lst = x.get("list_price")
    return _kind(x) == "goods" and _finite(lst) and lst > pol["stock"]["big_ticket_list"]


def f_max(summary, kind, pol):
    """Patch 3.2: the highest surge factor a unit of this kind is bought at, so that the market's clearing price
    still covers our floor with stock.min_margin_share of list to spare:
    (p_clear - min_margin_share) / (c_kind x floor_cost_mult), within 1.0 .. F_MAX_CAP. Without p_clear: the old
    1 / (worst_cost_share x floor_cost_mult), within the same bounds."""
    st, fcm = pol["stock"], pol["seller"]["floor_cost_mult"]
    pc = p_clear(summary, kind)
    if fcm <= 0:
        return F_MAX_CAP
    f = (pc - st["min_margin_share"]) / (c_kind(summary, kind) * fcm) if pc is not None else \
        1 / (st["worst_cost_share"] * fcm)
    return min(F_MAX_CAP, max(1.0, f))


def surge_state(f_inv, u1h, fm, pol):
    """({"factor": now, "u1h", "free_left", "f_max"}, units we may still buy before the factor passes fm).
    u1h: the market's units of the SKU in the rolling hour, ours included."""
    mk = pol["market"]
    free, per, top = mk["surge_free"], mk["surge_per_unit"], mk["surge_max"]
    f_now = min(top, max(f_inv, 1 + per * max(0, u1h - free)))
    room = math.inf if per <= 0 else max(0, math.floor(free + (fm - 1) / per - u1h + 1e-9))
    return {"factor": round(f_now, 4), "u1h": u1h, "free_left": max(0, free - u1h), "f_max": round(fm, 4)}, room


def mean_factor(f_inv, u1h, qty, pol):
    """The expected mean surge factor of a line of qty units (the factor rises by surge_per_unit a unit past
    surge_free), at least the factor inventory.json shows now, at most surge_max."""
    mk = pol["market"]
    return min(mk["surge_max"], max(f_inv, 1 + mk["surge_per_unit"] * max(0, u1h + qty / 2 - mk["surge_free"])))


def plan_restock(inv, catalog, demand, summary, pol, now, planned=None, instock=None, analyst=None):
    """{"buy": [{sku, qty}], "excluded": {category leaf or sku: reason}, "worst_cost", "headroom", "rules",
    "empty_picks": candidate SKUs with no unit in stock, "overpriced": units not counted as stock,
    "surge": {sku: {factor, u1h, free_left, f_max}}, "big_ticket": {budget, held_cost, planned_worst}}.
    planned: {sku: units} of our lines planned in the last hour that the summary's supply fold has not seen
    (restock.py keeps them). instock: memory/instock.json (restock.py's shelf history) for the demand model
    (memory/demand.forecast: our in-stock rate or share x market rate, by the hour of day) when
    stock.demand_model is on; its reading per SKU is in out["demand"]. analyst: {sku: multiplier} from the demand
    analyst's fresh forecast (memory/demand.load_forecast), applied to the SKU's rate after the demand model.

    Overpriced units (pricing.overpriced: cost floor above list) neither count as stock nor make a SKU stale,
    except for big-ticket SKUs, where every unit counts. A stale goods unit (seller.goods_stale_days) blocks its
    SKU only when the SKU is not a focus or trend SKU and the market sold none of it in 48 h (or there is no
    market data). A category pick the market sold none of in 48 h is not bought ('no_market_sales'), once the
    market fold covers NO_SALES_MIN_H hours.

    Patch 3.2 surge: u1h = max(inventory.json surge units, the ingest's market supply in the last hour + our
    planned units the ingest has not seen). inventory.json is the market's live count (ours included, SKUs above
    the free units only), so planned units are added to the fold's count, never to it. A SKU whose factor is above
    f_max(kind) is skipped ('surge'); a line stops where the factor would pass f_max (an in-stock SKU's line cut
    below min_line_qty is skipped too); the worst cost uses the line's mean factor.

    The strategist's focus SKUs (stock.focus_skus) and then trend SKUs (stock.trend_skus) are candidates even
    when they are not the category's pick or are above stock.goods_max_list, ordered before the rest; their
    sales rate is at least stock.trend_share x the market's. Focus SKUs are stocked focus_cover_mult times
    deeper, and their worst cost together stays within stock.focus_cap_share of the headroom.

    Big-ticket goods (listed above stock.big_ticket_list): held cost + planned worst cost stay within
    stock.big_ticket_share x inventory_cap_share x (cash + inventory cost), and a SKU holds at most
    stock.bigticket_probe_qty units until we sold PROBE_SALES of it in 6 h.

    The target per SKU is max(per_sku_qty, cover) within stock.max_hold (food: cover within FOOD_COVER_SHELF x
    shelf life of sales); a top-up of a SKU in stock below min(stock.min_line_qty, target / 2) is skipped
    ('small_topup'); a line is at most stock.max_qty units."""
    st = pol["stock"]
    units = _units(inv)
    cash = (inv or {}).get("balance") or 0
    inv_cost = sum(u.get("cost") or 0 for u in units)
    # D1: inventory cost within a share of all funds, cash above the floor
    headroom = int(max(0, min(st["inventory_cap_share"] * (cash + inv_cost) - inv_cost, cash - st["cash_floor"])))
    hours_left = (st["end_ts"] - now) / 3600
    rules = ["D1", "D2"]
    over = [pricing.overpriced(u, pol) for u in units]
    known, by_cat = {}, collections.defaultdict(list)
    for sku, x in (catalog or {}).items():
        if isinstance(x, dict) and isinstance(x.get("list_price"), (int, float)):
            known[sku] = dict(x, sku=sku)
            by_cat[x.get("category")].append(known[sku])
    big_held = sum(u.get("cost") or 0 for u in units if big_ticket(known.get(u.get("sku")) or u, pol))
    big_budget = st["big_ticket_share"] * st["inventory_cap_share"] * (cash + inv_cost)
    out = {"buy": [], "excluded": {}, "worst_cost": 0, "headroom": headroom, "rules": rules, "empty_picks": 0,
           "overpriced": sum(over), "surge": {},
           "big_ticket": {"budget": int(big_budget), "held_cost": int(big_held), "planned_worst": 0}, "demand": {}}
    if hours_left <= 0:
        out["excluded"]["*"] = "D14_round_over"
        return out
    have = collections.Counter(u.get("sku") for u, o in zip(units, over) if not o)
    have_all = collections.Counter(u.get("sku") for u in units)
    stale = {u.get("sku") for u, o in zip(units, over) if u.get("kind") == "goods" and not o
             and now - (u.get("supplied_at") or now) > pol["seller"]["goods_stale_days"] * 86400}
    surge, s_units, planned = surge_factors(inv), surge_units(inv), planned or {}
    fmax = {k: f_max(summary, k, pol) for k in ("food", "goods")}
    regular = set(regular_categories(summary))
    sd = supply_demand(summary)
    taper = hours_left <= st["taper_h"]
    focus, trend = set(st["focus_skus"]), set(st["trend_skus"]) - set(st["focus_skus"])
    long_tail_check = market_covered_h(summary) >= NO_SALES_MIN_H
    excluded, cands, seen = out["excluded"], [], set()

    def consider(x, leaf, tier):
        """The per-SKU rules after the category ones: D4, surge, the cover target, the big-ticket probe, D14
        taper, small top-ups, L1 priority."""
        sku, price, kind = x["sku"], x["list_price"], _kind(x)
        seen.add(sku)
        is_focus, is_trend = sku in focus, sku in trend
        if sku in stale and not (is_focus or is_trend) and not market_settles_48h(summary, sku):
            excluded[sku] = "D4_stale_goods"                      # unsold here and nowhere else either
            return
        f_inv = surge.get(sku, 1.0)
        u1h = max(s_units.get(sku, 0), (supply_1h(summary, sku) or 0) + planned.get(sku, 0))
        info, surge_room = surge_state(f_inv, u1h, fmax[kind], pol)
        out["surge"][sku] = info
        if info["factor"] > fmax[kind] + 1e-9:
            excluded[sku] = "surge"                               # units bought now would not clear above the floor
            return
        is_regular, big = leaf in regular, big_ticket(x, pol)
        rate = sku_stats(summary, sku)["sales_per_h"]
        if is_focus or is_trend:                                  # a share of what the market sells of it
            rate = max(rate, st["trend_share"] * (market_rate(summary, sku) or 0.0))
        if st.get("demand_model"):                                # memory/demand: in-stock rate, share, hour of day
            f = demand_model.forecast(summary, instock, sku, now, st["cover_min"] / 60, pol)
            if f["rate"] is not None:
                out["demand"][sku] = {"rate": f["rate"], "src": f["src"], "factor": f["factor"], "share": f["share"],
                                      "instock_h": f["instock_h"], "sold": f["sold"], "plain": round(rate, 4)}
                rate = max(rate, f["rate"])
        mult = (analyst or {}).get(sku)                           # the demand analyst's multiplier (strategist/analyst.py)
        if isinstance(mult, (int, float)) and mult > 0 and rate > 0:
            out["demand"].setdefault(sku, {"plain": round(rate, 4)})["analyst"] = mult
            rate *= mult
        # cover the sales expected until the next supply window (focus: focus_cover_mult times that),
        # food at most FOOD_COVER_SHELF of its shelf life of sales; at least per_sku_qty, at most max_hold
        want = math.ceil(rate * st["cover_min"] / 60 * st["cover_safety"] * (st["focus_cover_mult"] if is_focus else 1))
        if kind == "food" and _finite(x.get("shelf_hours")) and x["shelf_hours"] > 0:
            want = min(want, math.floor(rate * FOOD_COVER_SHELF * x["shelf_hours"]))
        target = min(max(st["per_sku_qty"], want), st["max_hold"])
        n = have_all[sku] if big else have[sku]                    # big-ticket: overpriced units count too
        probe = big and sku_stats(summary, sku)["sold_6h"] < PROBE_SALES and n < target
        if probe:
            target = min(target, st["bigticket_probe_qty"])
        qty = target - n
        if taper:                                                 # D14: what can still sell before the end
            qty = min(qty, int(rate * hours_left * st["taper_factor"] - n))
            if is_regular:                                        # L1: keep regular customers' categories
                qty = max(qty, st["regular_min_stock"] - n)
        if qty <= 0:
            excluded[sku] = "D14_taper" if taper else "big_ticket_probe" if probe else "stocked"
            return
        small = min(st["min_line_qty"], target / 2)
        if n > 0 and qty < small:                                 # a 1-2 unit line spends a whole supply call
            excluded[sku] = "small_topup"
            return
        qty = min(qty, st["max_qty"])
        if surge_room < qty:                                      # the surge allowance cuts the line
            if surge_room < 1 or (n > 0 and surge_room < small):  # too little left for a line
                excluded[sku] = "surge"
                return
            qty = surge_room
        sdv = sd.get(leaf, {})
        prio = (sdv.get("demand", 0.0) + 1) / ((sdv.get("supply") or 0) + 1) \
            if st["supply_demand_priority"] else 1.0
        # sold-out SKUs first: a shop without the SKU is not among the buyer's offers at all; then focus, trend
        cands.append({"key": (n > 0, 0 if is_focus else 1 if is_trend else 2, not is_regular, tier, -prio, leaf),
                      "sku": sku, "qty": qty, "price": price * mean_factor(f_inv, u1h, qty, pol),
                      "focus": is_focus, "big": big})

    for d in demand or []:
        leaf, median, kind = d.get("item") or _leaf(d.get("category")), d.get("median_price"), d.get("kind")
        skus = by_cat.get(d.get("category"), [])
        if not skus or not median:
            excluded[leaf] = "no_skus"
            continue
        # D2: long-shelf food first; short-shelf food only after enough use_within_hours observations
        if kind == "food":
            shelf = d.get("shelf_hours") or max(s.get("shelf_hours") or 0 for s in skus)
            if shelf >= st["food_shelf_hours"]:
                tier = 0
            elif category_stats(summary, leaf)["uwh"]["n"] >= st["short_food_min_uwh_obs"]:
                tier = 1
            else:
                excluded[leaf] = "D2_short_shelf_wait"
                continue
        else:
            if hours_left <= st["goods_stop_h"]:
                excluded[leaf] = "D14_goods_stop"
                continue
            if median > st["goods_max_list"]:
                excluded[leaf] = "D2_goods_over_max_list"
                continue
            tier = 1
        # D2: the SKU nearest the category's median price, inside the acceptable band
        pool = [s for s in skus if 0.5 * median <= s["list_price"] <= 1.5 * median
                and (kind == "food" or s["list_price"] <= st["goods_max_list"])]
        if not pool:
            excluded[leaf] = "D2_no_sku_in_band"
            continue
        pick = min(pool, key=lambda s: (abs(s["list_price"] - median), s["list_price"], s["sku"]))
        if long_tail_check and pick["sku"] not in focus | trend and market_settles_48h(summary, pick["sku"]) == 0:
            excluded[pick["sku"]] = "no_market_sales"            # the long tail: a supply line that sells nothing
            continue
        consider(pick, leaf, tier)

    # focus, then trend SKUs that were not a category's pick: D2 shelf and D14 goods stop by the SKU itself,
    # no price band
    for sku in list(st["focus_skus"]) + list(st["trend_skus"]):
        x = known.get(sku)
        if x is None:
            excluded[sku] = "not_in_catalog"
            continue
        if sku in seen:
            continue
        leaf = _leaf(x.get("category"))
        if _kind(x) == "food":
            if (x.get("shelf_hours") or 0) >= st["food_shelf_hours"]:
                tier = 0
            elif category_stats(summary, leaf)["uwh"]["n"] >= st["short_food_min_uwh_obs"]:
                tier = 1
            else:
                excluded[sku] = "D2_short_shelf_wait"
                continue
        elif hours_left <= st["goods_stop_h"]:
            excluded[sku] = "D14_goods_stop"
            continue
        else:
            tier = 1
        consider(x, leaf, tier)

    cands.sort(key=lambda c: c["key"])
    out["empty_picks"] = sum(1 for c in cands if not c["key"][0])
    worst, focus_worst, big_worst = 0.0, 0.0, 0.0
    focus_room, big_room = st["focus_cap_share"] * headroom, max(0.0, big_budget - big_held)
    for c in cands:
        sku = c["sku"]
        if len(out["buy"]) >= st["max_lines"]:
            excluded[sku] = "max_lines"
            continue
        unit_worst = st["worst_cost_share"] * c["price"]           # price includes the mean surge factor
        room = headroom - worst
        if c["focus"]:                                            # focus lines share at most focus_cap_share
            room = min(room, focus_room - focus_worst)
        if c["big"]:                                              # big-ticket units within their budget
            room = min(room, big_room - big_worst)
        qty = min(c["qty"], int(room // unit_worst)) if unit_worst > 0 else 0
        if qty < 1:
            excluded[sku] = "D1_cap" if headroom - worst < unit_worst else \
                "big_ticket_cap" if c["big"] and big_room - big_worst < unit_worst else "focus_cap"
            continue
        out["buy"].append({"sku": sku, "qty": qty})
        worst += qty * unit_worst
        if c["focus"]:
            focus_worst += qty * unit_worst
        if c["big"]:
            big_worst += qty * unit_worst
    out["worst_cost"] = int(round(worst))
    out["big_ticket"]["planned_worst"] = int(round(big_worst))
    if st["supply_demand_priority"]:
        rules.append("L1")
    if regular:
        rules.append("L1.regular")
    if stale:
        rules.append("D4")
    if taper or hours_left <= st["goods_stop_h"]:
        rules.append("D14")
    return out


def should_bankrupt(inv, summary, pol, now):
    """D10: (file?, inputs). Below the line, a filing left, at least 48 h to the end, and stock that will not
    recover enough (cost x 24 h sell-through < threshold) or no sale for 12 h. No summary: never."""
    st = pol["stock"]
    inv = inv or {}
    units = _units(inv)
    seller = (summary or {}).get("seller") or {}
    sold = seller.get("sold_24h") or 0
    rate = sold / (sold + len(units)) if sold + len(units) else 0.0
    inv_cost = sum(u.get("cost") or 0 for u in units)
    started, last = (summary or {}).get("started_at"), seller.get("last_sale_ts")
    quiet_s = st["bankrupt_zero_sales_h"] * 3600
    zero_sales = started is not None and now - started >= quiet_s and (last is None or now - last >= quiet_s)
    inputs = {"balance": inv.get("balance"), "line": inv.get("bankrupt_line"), "left": inv.get("bankruptcies_left"),
              "hours_to_end": round((st["end_ts"] - now) / 3600, 1), "inv_cost": inv_cost, "sold_24h": sold,
              "sell_rate_24h": round(rate, 4), "recover": int(inv_cost * rate), "zero_sales_12h": zero_sales,
              "summary": bool(summary)}
    ok = (isinstance(inv.get("balance"), (int, float)) and isinstance(inv.get("bankrupt_line"), (int, float))
          and inv["balance"] < inv["bankrupt_line"] and (inv.get("bankruptcies_left") or 0) > 0
          and inputs["hours_to_end"] >= st["bankrupt_min_hours_to_end"] and bool(summary)
          and (inputs["recover"] < st["bankrupt_recovery_threshold"] or zero_sales))
    return ok, inputs


def own_card_ok(url, timeout=3.0):
    """Does our shop's public agent card answer? Unknown URL: True (logged), so a missing setting never stops
    restocking; a card that does not answer: False."""
    if not url:
        hook_log("restock", "own shop URL unknown (memory/own_url or OWN_SHOP_URL), card check skipped")
        return True
    try:
        r = httpx.get(f"{url.rstrip('/')}/.well-known/agent-card.json", timeout=timeout)
    except Exception as e:
        hook_log("restock", f"own card {url}: {type(e).__name__}: {e}")
        return False
    if r.status_code != 200:
        hook_log("restock", f"own card {url}: HTTP {r.status_code}")
        return False
    try:
        name = r.json().get("name", "")
    except Exception:
        name = ""
    if "psyoung82" not in str(name):
        hook_log("restock", f"own card {url}: name {name!r} does not contain psyoung82")
    return True
