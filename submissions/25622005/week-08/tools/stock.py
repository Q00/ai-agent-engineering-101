"""Restock and bankruptcy rules (strategy D1, D2, D4, D10, D14, L1). Pure functions except own_card_ok.

plan_restock decides which SKUs to buy and how many; the kit buys them (at most 10 lines, stopping at the
first line the balance cannot pay). Grades and costs are drawn on arrival, so affordability uses the worst
cost (stock.worst_cost_share x list price). Supply opens about every 20 minutes for 3 minutes, so each SKU
is stocked to cover its expected sales until the next window (stock.cover_min x stock.cover_safety).
"""
import collections
import math

import httpx

from memory.query import category_stats, regular_categories, sku_stats, supply_demand
from memory.store import hook_log
from tools import pricing


def _leaf(category):
    return str(category or "").split(">")[-1]


def _units(inv):
    return [u for u in (inv or {}).get("units") or [] if isinstance(u, dict) and u.get("grade") != "expired"]


SUPPLY_GAP_S = 11                  # measured mean 10.8 s between supply calls: 10 s limit + the kit's refresh
CLOSE_MARGIN_S = 2


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


def surge_factors(inv):
    """{sku: factor} from inventory.json `surge` (a list of {sku, name, units, factor}); bad rows ignored."""
    s = (inv or {}).get("surge")
    return {x["sku"]: float(x["factor"]) for x in s if isinstance(x, dict) and isinstance(x.get("sku"), str)
            and _finite(x.get("factor")) and x["factor"] > 0} if isinstance(s, list) else {}


def held(inv):
    """{"skus": distinct SKUs in stock, "units": units in stock}, expired units left out."""
    units = _units(inv)
    return {"skus": len({u.get("sku") for u in units}), "units": len(units)}


def plan_restock(inv, catalog, demand, summary, pol, now):
    """{"buy": [{sku, qty}], "excluded": {category leaf or sku: reason}, "worst_cost", "headroom", "rules",
    "empty_picks": candidate SKUs with no unit in stock, "overpriced": units not counted as stock}.

    Overpriced units (pricing.overpriced: cost floor above list) neither count as stock nor make a SKU stale.
    A supply surge raises the worst cost by its factor; a SKU whose surged worst cost would put the floor
    above list (factor x worst_cost_share x floor_cost_mult > 1) is skipped until the surge falls.

    The strategist's focus SKUs (stock.focus_skus) are candidates even when they are not the category's pick
    or are above stock.goods_max_list; they are stocked focus_cover_mult times deeper, ordered before the
    rest, and their worst cost together stays within stock.focus_cap_share of the headroom."""
    st = pol["stock"]
    units = _units(inv)
    cash = (inv or {}).get("balance") or 0
    inv_cost = sum(u.get("cost") or 0 for u in units)
    # D1: inventory cost within a share of all funds, cash above the floor
    headroom = int(max(0, min(st["inventory_cap_share"] * (cash + inv_cost) - inv_cost, cash - st["cash_floor"])))
    hours_left = (st["end_ts"] - now) / 3600
    rules = ["D1", "D2"]
    over = [pricing.overpriced(u, pol) for u in units]
    out = {"buy": [], "excluded": {}, "worst_cost": 0, "headroom": headroom, "rules": rules, "empty_picks": 0,
           "overpriced": sum(over)}
    if hours_left <= 0:
        out["excluded"]["*"] = "D14_round_over"
        return out
    have = collections.Counter(u.get("sku") for u, o in zip(units, over) if not o)
    stale = {u.get("sku") for u, o in zip(units, over) if u.get("kind") == "goods" and not o
             and now - (u.get("supplied_at") or now) > pol["seller"]["goods_stale_days"] * 86400}
    surge = surge_factors(inv)
    regular = set(regular_categories(summary))
    sd = supply_demand(summary)
    by_cat, known = collections.defaultdict(list), {}
    for sku, x in (catalog or {}).items():
        if isinstance(x, dict) and isinstance(x.get("list_price"), (int, float)):
            known[sku] = dict(x, sku=sku)
            by_cat[x.get("category")].append(known[sku])
    taper = hours_left <= st["taper_h"]
    focus = set(st["focus_skus"])
    excluded, cands, seen = out["excluded"], [], set()

    def consider(sku, price, leaf, tier):
        """The per-SKU rules after the category ones: D4, the cover target, D14 taper, L1 priority."""
        seen.add(sku)
        if sku in stale:                                          # D4: unsold goods are not restocked
            excluded[sku] = "D4_stale_goods"
            return
        f = surge.get(sku, 1.0)
        # only a SKU that surges: a policy whose worst cost x floor_cost_mult is above 1 must not stop every SKU
        if f > 1 and f * st["worst_cost_share"] * pol["seller"]["floor_cost_mult"] > 1:
            excluded[sku] = "surge"                               # units bought now could not sell at list
            return
        is_regular, is_focus = leaf in regular, sku in focus
        rate = sku_stats(summary, sku)["sales_per_h"]
        # cover the sales expected until the next supply window (focus: focus_cover_mult times that),
        # at least per_sku_qty, at most max_qty
        cover = rate * st["cover_min"] / 60 * st["cover_safety"] * (st["focus_cover_mult"] if is_focus else 1)
        target = min(max(st["per_sku_qty"], math.ceil(cover)), st["max_qty"])
        qty = target - have[sku]
        if taper:                                                 # D14: what can still sell before the end
            qty = min(qty, int(rate * hours_left * st["taper_factor"] - have[sku]))
            if is_regular:                                        # L1: keep regular customers' categories
                qty = max(qty, st["regular_min_stock"] - have[sku])
        if qty <= 0:
            excluded[sku] = "D14_taper" if taper else "stocked"
            return
        sdv = sd.get(leaf, {})
        prio = (sdv.get("demand", 0.0) + 1) / ((sdv.get("supply") or 0) + 1) \
            if st["supply_demand_priority"] else 1.0
        # sold-out SKUs first: a shop without the SKU is not among the buyer's offers at all; then focus SKUs
        cands.append(((have[sku] > 0, not is_focus, not is_regular, tier, -prio, leaf), sku,
                      min(qty, st["max_qty"]), price * f, is_focus))

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
        consider(pick["sku"], pick["list_price"], leaf, tier)

    # focus SKUs that were not a category's pick: D2 shelf and D14 goods stop by the SKU itself, no price band
    for sku in st["focus_skus"]:
        x = known.get(sku)
        if x is None:
            excluded[sku] = "not_in_catalog"
            continue
        if sku in seen:
            continue
        leaf = _leaf(x.get("category"))
        if x.get("kind") == "food" or (x.get("kind") is None and x.get("shelf_hours")):
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
        consider(sku, x["list_price"], leaf, tier)

    cands.sort(key=lambda c: c[0])
    out["empty_picks"] = sum(1 for c in cands if not c[0][0])
    worst, focus_worst, focus_room = 0.0, 0.0, st["focus_cap_share"] * headroom
    for _, sku, qty, price, is_focus in cands:
        if len(out["buy"]) >= st["max_lines"]:
            excluded[sku] = "max_lines"
            continue
        unit_worst = st["worst_cost_share"] * price               # price includes the surge factor
        room = headroom - worst
        if is_focus:                                              # focus lines share at most focus_cap_share
            room = min(room, focus_room - focus_worst)
        qty = min(qty, int(room // unit_worst)) if unit_worst > 0 else 0
        if qty < 1:
            excluded[sku] = "focus_cap" if is_focus and headroom - worst >= unit_worst else "D1_cap"
            continue
        out["buy"].append({"sku": sku, "qty": qty})
        worst += qty * unit_worst
        if is_focus:
            focus_worst += qty * unit_worst
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
