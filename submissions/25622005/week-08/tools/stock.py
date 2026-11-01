"""Restock and bankruptcy rules (strategy D1, D2, D4, D10, D14, L1). Pure functions except own_card_ok.

plan_restock decides which SKUs to buy and how many; the kit buys them (at most 10 lines, stopping at the
first line the balance cannot pay). Grades and costs are drawn on arrival, so affordability uses the worst
cost (stock.worst_cost_share x list price).
"""
import collections

import httpx

from memory.query import category_stats, regular_categories, sku_stats, supply_demand
from memory.store import hook_log


def _leaf(category):
    return str(category or "").split(">")[-1]


def _units(inv):
    return [u for u in (inv or {}).get("units") or [] if isinstance(u, dict) and u.get("grade") != "expired"]


def plan_restock(inv, catalog, demand, summary, pol, now):
    """{"buy": [{sku, qty}], "excluded": {category leaf or sku: reason}, "worst_cost", "headroom", "rules"}."""
    st = pol["stock"]
    units = _units(inv)
    cash = (inv or {}).get("balance") or 0
    inv_cost = sum(u.get("cost") or 0 for u in units)
    # D1: inventory cost within a share of all funds, cash above the floor
    headroom = int(max(0, min(st["inventory_cap_share"] * (cash + inv_cost) - inv_cost, cash - st["cash_floor"])))
    hours_left = (st["end_ts"] - now) / 3600
    rules = ["D1", "D2"]
    out = {"buy": [], "excluded": {}, "worst_cost": 0, "headroom": headroom, "rules": rules}
    if hours_left <= 0:
        out["excluded"]["*"] = "D14_round_over"
        return out
    have = collections.Counter(u.get("sku") for u in units)
    stale = {u.get("sku") for u in units if u.get("kind") == "goods"
             and now - (u.get("supplied_at") or now) > pol["seller"]["goods_stale_days"] * 86400}
    regular = set(regular_categories(summary))
    sd = supply_demand(summary)
    by_cat = collections.defaultdict(list)
    for sku, x in (catalog or {}).items():
        if isinstance(x, dict) and isinstance(x.get("list_price"), (int, float)):
            by_cat[x.get("category")].append(dict(x, sku=sku))
    taper = hours_left <= st["taper_h"]
    excluded, cands = out["excluded"], []

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
        sku = pick["sku"]
        if sku in stale:                                          # D4: unsold goods are not restocked
            excluded[sku] = "D4_stale_goods"
            continue
        is_regular = leaf in regular
        qty = st["per_sku_qty"] - have[sku]
        if taper:                                                 # D14: what can still sell before the end
            rate = sku_stats(summary, sku)["sales_per_h"]
            qty = min(qty, int(rate * hours_left * st["taper_factor"] - have[sku]))
            if is_regular:                                        # L1: keep regular customers' categories
                qty = max(qty, st["regular_min_stock"] - have[sku])
        if qty <= 0:
            excluded[sku] = "D14_taper" if taper else "stocked"
            continue
        sdv = sd.get(leaf, {})
        prio = (sdv.get("demand", 0.0) + 1) / ((sdv.get("supply") or 0) + 1) \
            if st["supply_demand_priority"] else 1.0
        cands.append(((not is_regular, tier, -prio, leaf), sku, min(qty, st["max_qty"]), pick["list_price"]))

    cands.sort(key=lambda c: c[0])
    worst = 0.0
    for _, sku, qty, price in cands:
        if len(out["buy"]) >= st["max_lines"]:
            excluded[sku] = "max_lines"
            continue
        unit_worst = st["worst_cost_share"] * price
        qty = min(qty, int((headroom - worst) // unit_worst)) if unit_worst > 0 else 0
        if qty < 1:
            excluded[sku] = "D1_cap"
            continue
        out["buy"].append({"sku": sku, "qty": qty})
        worst += qty * unit_worst
    out["worst_cost"] = int(round(worst))
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
