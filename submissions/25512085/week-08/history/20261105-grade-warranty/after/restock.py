"""POLICY.md §9 unified buying. Stdout remains a single {buy: [...]} JSON line."""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from adaptive_strategy import decide_restock
import seller_cash_policy as cash_policy
from store import (
    ROUND_END_TS, STOP_BUYING_BEFORE_END_S, apply_ledger_rows,
    fetch_ledger_rows, fetch_own_shop_urls, fetch_public_buying_data, load_catalog, load_hub,
    load_inventory, migrate_no_rebuy, mutate_hub, queue_forum_action,
    update_capital_stage,
)

POPULARITY_S = 40 * 60
NO_DEMAND_S = 2 * 3600
NO_REBUY_AGE_S = 4 * 3600
MAX_NO_REBUY_SKUS = 500
WINDOW_LEAD_S = 60
MIN_FOOD_LIST_PRICE = 10_000
MAX_FOOD_LIST_PRICE = 120_000
FOOD_MIN_SHELF_HOURS = 180
DEFAULT_RATIO = .40
RATIO_CACHE_S = 30 * 60
RATIO_MIN, RATIO_MAX = .30, .75   # Growth phase: allow up to the top shops' stock ratio (POLICY 9.1)
FILL_WINDOWS = 2                  # Close the stock deficit over ~2 supply windows (~40 min)
WINDOW_EQUITY_CAP = .50           # Runaway guard per supply window
ENDGAME_SCHEDULE = {
    # Exclusive lower bound in hours remaining (exact boundary enters the next phase).
    96: {"name": "normal", "ratio_cap": None, "grades": ("hot", "popular", "observe"), "hot_max_depth": 8},
    72: {"name": "taper-1", "ratio_cap": .50, "grades": ("hot", "popular", "observe"), "hot_max_depth": 6},
    48: {"name": "taper-2", "ratio_cap": .30, "grades": ("hot", "popular"), "hot_max_depth": 4},
    36: {"name": "taper-3", "ratio_cap": .15, "grades": ("hot",), "hot_max_depth": 2},
    -math.inf: {"name": "stop", "ratio_cap": 0, "grades": (), "hot_max_depth": 0},
}


def endgame_phase(now):
    hours_left = (ROUND_END_TS - now) / 3600
    phase = ENDGAME_SCHEDULE[-math.inf] if ROUND_END_TS - now <= STOP_BUYING_BEFORE_END_S else next(
        ENDGAME_SCHEDULE[lower] for lower in sorted(ENDGAME_SCHEDULE, reverse=True) if hours_left > lower)
    return phase, {"endgame_phase": phase["name"], "ratio_cap": phase["ratio_cap"], "hours_left": hours_left}


def taper_ratio(ratio, phase):
    # §9 has already clamped R; never apply RATIO_MIN again after the endgame cap.
    return ratio if phase["ratio_cap"] is None else min(ratio, phase["ratio_cap"])


def supply_window(inv: dict[str, Any]) -> tuple[float, float] | None:
    window = inv.get("restock") or {}
    try:
        opens, closes = float(window["opens_at"]), float(window["closes_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if (math.isfinite(opens) and math.isfinite(closes) and opens < closes
            and opens - WINDOW_LEAD_S <= time.time() < closes):
        return opens, closes
    return None


def market_ratio(shops, board, catalog):
    """Pure calculation from full public API snapshots, never order-specific files."""
    shops = shops.get("shops", []) if isinstance(shops, dict) else shops
    board = board.get("shops", []) if isinstance(board, dict) else board
    metrics = {s.get("name"): s for s in board if isinstance(s, dict)}
    ratios = []
    for shop in shops:
        if not isinstance(shop, dict):
            continue
        data = {**shop, **metrics.get(shop.get("name"), {})}
        revenue, profit, stock = data.get("revenue"), data.get("profit"), shop.get("stock")
        if (not isinstance(revenue, (int, float)) or not math.isfinite(revenue)
                or not isinstance(profit, (int, float)) or not math.isfinite(profit)
                or not isinstance(stock, dict)):
            continue
        if any(s not in catalog or not isinstance(q, (int, float)) or not math.isfinite(q) or q < 0
               for s, q in stock.items()):
            continue
        estimated = sum(q * int(catalog[s].get("list_price", 0)) * .75 for s, q in stock.items())
        cash = 5_000_000 + profit
        if cash < 0 or cash + estimated <= 0:
            continue
        ratios.append((revenue, str(shop.get("name", "")), estimated / (cash + estimated)))
    if len(ratios) < 3:
        return DEFAULT_RATIO, {"source": "default: insufficient public shops/financials", "default": True,
                               "shops": len(ratios)}
    top = sorted(ratios, key=lambda r: (-r[0], r[1]))[:5]
    return max(RATIO_MIN, min(RATIO_MAX, statistics.median(r[2] for r in top))), {
        "source": "/api/shops+/api/board: top revenue median", "default": False, "shops": len(top)}


def resolve_market_ratio(hub, observation=None):
    now = time.time()
    if observation is not None and not observation[1]["default"]:
        ratio, source = observation
        hub["market_ratio_cache"] = {"ratio": ratio, "ts": now, "shops": source["shops"], "source": source["source"]}
        return ratio, {**source, "cached": False, "ts": now}
    cached = hub.get("market_ratio_cache", {})
    ratio, ts = cached.get("ratio"), cached.get("ts")
    if (isinstance(ratio, (int, float)) and RATIO_MIN <= ratio <= RATIO_MAX
            and isinstance(ts, (int, float)) and 0 <= now - ts <= RATIO_CACHE_S):
        return ratio, {"source": cached["source"], "shops": cached["shops"], "ts": ts,
                       "default": False, "cached": True, "refresh_failed": True}
    return DEFAULT_RATIO, {"source": "default: public API unavailable/insufficient, no fresh cache",
                           "default": True, "cached": False, "shops": 0}


def popularity(inquiries, misses, settlements, *, hot_max_depth=None):
    if hot_max_depth is None:
        hot_max_depth = ENDGAME_SCHEDULE[max(ENDGAME_SCHEDULE)]["hot_max_depth"]
    if inquiries >= 4:
        return "hot", min(hot_max_depth, max(3, inquiries))
    if inquiries >= 2 or misses >= 2 or settlements >= 21:
        return "popular", 2
    if inquiries == 1 or misses == 1 or 11 <= settlements <= 20:
        return "observe", 1
    return "none", 0


def estimated_cost(hub, sku, info):
    actual = hub.get("purchase_costs", {}).get(sku, {}).get("cost", 0)
    return max(1, math.ceil(actual if actual > 0 else int(info.get("list_price", 0)) * .75))


def build_restock_plan(market_dir: Path) -> dict[str, Any]:
    catalog, inv = load_catalog(market_dir), load_inventory(market_dir)

    def stopped(reason, *, bankrupt=False):
        cs = update_capital_stage({"capital_stage": {}}, inv)
        stock = cs.get("stock_cost_value", 0)
        equity = int(inv.get("balance") or 0) + stock
        ratio, source = resolve_market_ratio(load_hub())
        phase, endgame = endgame_phase(time.time())
        ratio = taper_ratio(ratio, phase)
        print(json.dumps({"E": equity, "R": ratio, "R_source": source, "T": ratio * equity,
                          "deficit": max(0, ratio * equity - stock), "W": 0,
                          "grades": {g: {"skus": 0, "reserved": 0} for g in ("hot", "popular", "observe")},
                          "grade_budget": {"hot_popular": 0, "observe": 0},
                          "no_rebuy_new": 0, "no_rebuy_total": len(load_hub().get("no_rebuy", {})),
                          "excluded": {}, "stop": reason, **endgame}), file=sys.stderr, flush=True)
        return {"buy": [], "bankrupt": True} if bankrupt else {"buy": []}

    if not catalog or not inv:
        return stopped("missing_input")
    balance = int(inv.get("balance") or 0)
    sellable = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    if balance < int(inv.get("bankrupt_line") or 0) and int(inv.get("bankruptcies_left") or 0) > 0 and not sellable:
        return stopped("bankruptcy", bankrupt=True)
    if time.time() >= ROUND_END_TS - STOP_BUYING_BEFORE_END_S:
        return stopped("round_end")
    snapshot = load_hub()
    rows = fetch_ledger_rows(int(snapshot.get("ledger_seq", 0)))
    window = supply_window(inv)
    if window and str(window) not in snapshot.get("restock_windows", {}):
        rows += fetch_ledger_rows(0)
    own_urls = fetch_own_shop_urls()
    public_data = fetch_public_buying_data()
    observation = market_ratio(*public_data, catalog) if public_data is not None else None
    try:
        fresh = 0 <= time.time() - (market_dir / "inventory.json").stat().st_mtime <= 120
    except OSError:
        fresh = False
    with mutate_hub() as hub:
        hub["own_shop_urls"] = sorted(set(hub.get("own_shop_urls", [])) | set(own_urls))
        if window and not hub.get("restock_windows"):
            hub.setdefault("restock_windows", {})[str(window)] = {
                "baseline_actual": 0, "reserved": 0, "blocked": True}
        return plan_with_hub(hub, market_dir, catalog, inv, rows, inventory_fresh=fresh,
                             ratio_observation=observation)


def plan_with_hub(hub, market_dir, catalog, inv, rows, *, inventory_fresh=False, ratio_observation=None):
    """Locked caller persists reservations, inquiries and no-rebuy history atomically."""
    now = time.time()
    migrate_no_rebuy(hub)
    apply_ledger_rows(hub, rows, catalog)
    detection_since = hub.setdefault("no_rebuy_detection_since", now)
    cs = update_capital_stage(hub, inv)
    decide_restock(hub, inv, now, inventory_fresh=inventory_fresh)  # metrics only
    radar = hub.get("demand_radar", {})
    units = inv.get("units") or []
    active = [u for u in units if u.get("grade") != "expired"]
    held = collections.Counter(u.get("sku") for u in active)
    costs = hub.setdefault("purchase_costs", {})
    for u in units:
        if u.get("sku") in catalog and u.get("cost", 0) > 0:
            sku, ts = u["sku"], u.get("supplied_at", 0)
            if ts >= costs.get(sku, {}).get("ts", 0):
                costs[sku] = {"cost": u["cost"], "ts": ts}
    no_rebuy = hub.setdefault("no_rebuy", {})
    new_no_rebuy = 0
    for u in units:
        sku, supplied = u.get("sku"), u.get("supplied_at")
        if (sku in catalog and isinstance(supplied, (int, float)) and supplied > 0
                and now - max(supplied, detection_since) >= NO_REBUY_AGE_S
                and hub.get("own_last_sale_ts", {}).get(sku, 0) < supplied and sku not in no_rebuy):
            if len(no_rebuy) < MAX_NO_REBUY_SKUS:
                no_rebuy[sku] = now
                new_no_rebuy += 1
            else:
                hub["no_rebuy_overflow"] = True  # never evict a round-long prohibition
    coverage = hub.get("adaptive_ledger_coverage", {})
    covered = (coverage.get("since", now) <= now - NO_DEMAND_S
               and 0 <= now - coverage.get("through", 0) <= 120)
    ratio, source = resolve_market_ratio(hub, ratio_observation)
    phase, endgame = endgame_phase(now)
    growth = cash_policy.ready(hub, inv, now, inventory_fresh, phase['name'])
    cash_policy.note_stock(hub, held, now)
    cash_signals = {r['sku'] for r in cash_policy.sale_rows(hub) if 0 <= now-r['ts'] <= 3600}
    cash_models = {}
    ratio = taper_ratio(ratio, phase)
    stock = cs["stock_cost_value"]
    equity = int(inv.get("balance") or 0) + stock
    target = ratio * equity
    deficit = max(0, target - stock)
    excluded = collections.Counter()
    candidates = {g: [] for g in ("hot", "popular", "observe")}
    storage = float(inv.get("storage_per_day", .01))
    for sku, info in catalog.items():
        events = radar.get("sku_inquiries", {}).get(sku.lower(), [])
        recent = [e for e in events if 0 <= now - e["ts"] <= POPULARITY_S]
        misses = sum(bool(e.get("miss")) for e in recent)
        # Unheld SKUs use stockout inquiries only, not old quotes for stock no longer held.
        inquiries = len(recent) if held[sku] else misses
        sales = radar.get("sku_sold_ts", {}).get(sku, [])
        grade, depth = popularity(inquiries, misses, sum(0 <= now - t <= POPULARITY_S for t in sales),
                                  hot_max_depth=phase["hot_max_depth"])
        if growth and (grade != 'none' or sku in cash_signals):
            model = cash_policy.forecast(hub, sku, info, catalog, now, inquiries)
            if model['own20'] >= 2 or inquiries >= 4:
                grade = 'hot'
            elif model['market60'] >= 10 or model['own60'] or misses:
                grade = 'popular'
            elif model['market60'] >= 3:
                grade = 'observe'
            depth = model['depth'] if grade != 'none' else 0
            cash_models[sku] = model
        lp, cost = int(info.get("list_price", 0)), estimated_cost(hub, sku, info)
        prices = radar.get("sku_settles", {}).get(sku, [])
        expected_price = statistics.median(prices) if prices else .95 * lp
        margin = expected_price * .97 - cost  # net expected proceeds, including settlement fee
        if growth and sku in cash_models:
            estimate = cash_policy.procurement(hub,sku,info,inv,max(0,depth-held[sku]),catalog,now)
            if estimate is None:
                margin = -math.inf
            else:
                cost,margin = estimate['reservation_unit_cost'],estimate['expected_unit_contribution']
                cash_models[sku].update(estimate)
                cash_models[sku]['score20'] = cash_models[sku]['prediction20']*margin
        reason = None
        if sku in no_rebuy or hub.get("no_rebuy_overflow"):
            reason = "no_rebuy" if sku in no_rebuy else "no_rebuy_history_full"
        elif covered and not any(0 <= now - t <= NO_DEMAND_S for t in sales) and not any(
                0 <= now - e["ts"] <= NO_DEMAND_S for e in events):
            reason = "no_demand_2h"
        elif depth == 0:
            reason = "no_popularity"
        elif grade not in phase["grades"]:
            reason = "endgame_grade"
        elif lp <= 0:
            reason = "invalid_price"
        elif (info.get("kind") == "food" and grade != "hot"
              and (not MIN_FOOD_LIST_PRICE <= lp <= MAX_FOOD_LIST_PRICE
                    or (not held[sku] and (info.get("shelf_hours") or 0) < FOOD_MIN_SHELF_HOURS))):
            reason = "food_safety"
        elif margin < 2 * storage * cost:
            reason = "storage_margin"
        elif depth <= held[sku]:
            reason = "target_met"
        if reason:
            excluded[reason] += 1
        else:
            candidates[grade].append((sku, depth - held[sku], cost, margin))
    for group in candidates.values():
        group.sort(key=lambda c: (-c[3], c[0]))
    restore = sum(n * c for g in ("hot", "popular") for s, n, c, m in candidates[g] if not held[s])
    calculated = max(0, int(min(cs["available_budget"], max(restore, deficit / FILL_WINDOWS), WINDOW_EQUITY_CAP * equity)))
    if growth:
        calculated, reserve = cash_policy.spending_budget(hub,inv,cs['cash_floor'],now)
        cs['available_budget'] = min(cs['available_budget'],max(0,inv['balance']-reserve))
        cs['cash_floor'] = reserve
    if phase["ratio_cap"] is not None and deficit <= 0:
        calculated = 0
    diag = {"E": equity, "R": ratio, "R_source": source, "T": target, "deficit": deficit,
            "W": calculated, "cash_floor": cs["cash_floor"], "restore_cost": restore,
            "grades": {g: {"skus": len(v), "reserved": 0} for g, v in candidates.items()},
            "grade_budget": {"hot_popular": calculated, "observe": calculated // 2},
            "no_rebuy_new": new_no_rebuy, "no_rebuy_total": len(no_rebuy), "excluded": excluded,
            "no_demand_deferred": not covered, **endgame}
    diag['seller_cash_policy'] = {'active':growth,'objective':'forecast own 20m sales * refund-adjusted contribution',
                                  'models':cash_models if growth else {}}

    def finish(orders, reason=None):
        if inventory_fresh:
            try:
                feed_path=market_dir/'forum.json'
                feed=json.loads(feed_path.read_text('utf-8')) if 0<=now-feed_path.stat().st_mtime<=120 else []
            except (OSError,ValueError):
                feed=[]
            post=cash_policy.availability_plan(active,hub,catalog,now,feed)
            if post:
                # The outbox append must succeed before reserving this notice's slot.
                queued=queue_forum_action(market_dir,post['action'])
                if queued:
                    fs=hub.setdefault('forum_state',{})
                    fs['last_shop_post_ts']=int(now)
                    announced=fs.setdefault('availability_announced',{})
                    announced.update({s:post['demand_ts'] for s in post['skus']})
                    fs['availability_announced']=dict(sorted(announced.items(),key=lambda x:x[1])[-500:])
                    if post['thread']:
                        fs['availability_replied_threads']=(fs.get('availability_replied_threads',[])+[post['thread']])[-300:]
        if reason:
            diag["stop"] = reason
        print(json.dumps(diag, ensure_ascii=False), file=sys.stderr, flush=True)
        return {"buy": orders}

    if time.time() >= ROUND_END_TS - STOP_BUYING_BEFORE_END_S:
        return finish([], "round_end")
    window = supply_window(inv)
    if window is None:
        return finish([], "outside_window")
    actual = sum(s["total"] for s in hub.get("own_supplies", {}).values() if window[0] <= s["ts"] < window[1])
    state = hub.setdefault("restock_windows", {}).setdefault(str(window), {"baseline_actual": actual, "reserved": 0})
    if state.get("blocked"):
        return finish([], "first_window")
    cap = state.setdefault("window_cap", calculated)
    diag["W"] = cap  # immutable initial ceiling; current cash can only reduce usable funds
    used = max(actual, state["baseline_actual"] + state["reserved"])
    budget = min(cs["available_budget"], calculated, max(0, cap - used))
    diag["usable_budget"] = budget
    planned = state.setdefault("planned_skus", {})
    grade_spent = state.setdefault("grade_reserved", {g: 0 for g in candidates})
    spent, orders = 0, []
    groups = [(g,c) for g,group in candidates.items() for c in group]
    if growth:
        orders,spent = cash_policy.allocate(groups,cash_models,held,budget,cap,grade_spent,planned)
    for grade, candidate in ([] if growth else groups):
        sku, need, cost, margin = candidate
        if sku in planned:
            excluded["window_duplicate"] += 1
            continue
        allowance = budget - spent
        if grade == "observe":
            allowance = min(allowance, max(0, cap // 2 - grade_spent[grade]))
        qty = min(need, 20*(10-len(orders)) if growth else 20, max(0, allowance) // cost)
        if not qty or len(orders) >= 10:
            continue
        remaining = qty
        while remaining:
            batch = min(20,remaining)
            orders.append({"sku":sku,'qty':batch})
            remaining -= batch
        planned[sku] = qty
        spent += qty * cost
        grade_spent[grade] += qty * cost
    state["reserved"] += spent
    for grade in candidates:
        diag["grades"][grade]["reserved"] = grade_spent[grade]
    diag["grade_budget"] = {"hot_popular": cap, "observe": cap // 2}
    # Availability notices also run outside supply windows, through finish().
    return finish(orders)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market", help="Path to .market directory")
    args = ap.parse_args()
    print(json.dumps(build_restock_plan(Path(args.state)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

