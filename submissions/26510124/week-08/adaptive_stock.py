"""Evidence-gated goods growth and small food pilots, with bounded risk.

Public prices rank trading activity, not predicted profit: grade and actual
cost are unknown until delivery. Cash planning still reserves maximum cost.
"""
from collections import Counter
import math
import re

from feedback import number
from focused_stock import observe_control, proven
from market_evidence import demand_profiles
from stock_budget import StockBudget
from supply_pricing import surge_factors, worst_cost
from replenishment_profit import refund_recovery

SKU = re.compile(r"[GF][0-9a-f]{8}\Z")


def configuration(cfg):
    value = cfg.get("adaptive_goods")
    focus = cfg.get("focused_goods", {})
    return (value if isinstance(value, dict) and value.get("enabled") is True
            and focus.get("enabled") is True else None)


def depth_evidence(metric, cfg, kind="goods"):
    """Recent confirmed demand can deepen already proven goods, never pilots."""
    rules = configuration(cfg) or {}
    growth = rules.get("replenishment", {})
    enabled = isinstance(growth, dict) and growth.get("enabled") is True
    count = metric.get("recent_own_confirmed_1h")
    window = metric.get("recent_own_window_seconds")
    basis = metric.get("recent_own_time_basis")
    evidence = {"recent_own_confirmed_1h": count if type(count) is int and count >= 0 else 0,
                "recent_own_window_seconds": window,
                "recent_own_time_basis": basis,
                "coverage_seconds": growth.get("cover_seconds") if enabled else None,
                "coverage_units": None, "depth_cap": 1, "depth_reason": "unproven_pilot"}
    if kind != "goods":
        return {**evidence, "depth_reason": "food_pilot"}
    maximum = rules.get("proven_max_units_per_sku", 1)
    if type(maximum) is not int or not 1 <= maximum <= 2:
        raise ValueError("Invalid adaptive proven depth")
    if not (proven(metric, cfg["min_mature_sales"]) or refund_recovery(metric, cfg)):
        return evidence
    baseline = min(maximum, cfg["max_units_per_sku"], 2)
    evidence.update(depth_cap=baseline, depth_reason="proven_baseline")
    if not enabled:
        return evidence
    lookback, cover, ceiling = (growth.get(k) for k in
                               ("lookback_seconds", "cover_seconds", "max_units_per_sku"))
    if (type(lookback) is not int or lookback != 3600 or type(cover) is not int or cover <= 0
            or type(ceiling) is not int or not 1 <= ceiling <= 4):
        raise ValueError("Invalid adaptive replenishment coverage")
    if (type(count) is not int or count <= 0 or type(window) is not int
            or window != lookback or basis != "local_receipt_time"):
        return {**evidence, "depth_reason": "proven_without_recent_demand"}
    coverage = (count * cover + lookback - 1) // lookback
    cap = min(cfg["max_units_per_sku"], ceiling, 4, max(baseline, coverage))
    evidence.update(coverage_units=coverage, depth_cap=cap,
                    depth_reason="recent_own_demand_coverage" if cap > baseline else "proven_baseline")
    return evidence


def depth_cap(metric, cfg, kind="goods"):
    return depth_evidence(metric, cfg, kind)["depth_cap"]


def candidates(inv, catalog, categories, economics, cfg, now, public, *, paused=(), allowed=None, diagnostics=None):
    """Return a bounded pool of eligible alternatives, never arbitrary stock."""
    rules = configuration(cfg)
    if not rules:
        return {}
    factors = surge_factors(inv)
    if factors is None:
        return {}
    focus = cfg["focused_goods"]
    excluded = set(focus["primary_skus"] + focus["secondary_skus"]) | set(paused)
    held = Counter(unit["sku"] for unit in inv["units"])
    budget = max(0, math.floor(inv["balance"]) - math.ceil(inv["start"] * cfg["cash_reserve_percent"] / 100))
    exposure = StockBudget(inv, cfg)
    profiles = demand_profiles(public, now)
    food_rules = rules.get("food_pilots", {})
    food_enabled = isinstance(food_rules, dict) and food_rules.get("enabled") is True
    turnover_rules = rules.get("turnover_goods", {})
    turnover_enabled = isinstance(turnover_rules, dict) and turnover_rules.get("enabled") is True
    ranked, food_ranked, turnover_ranked = [], [], []
    def reject(sku, reason):
        if diagnostics is not None:
            diagnostics[sku] = reason
    for sku, profile in profiles.items():
        if sku in excluded or allowed is not None and sku not in allowed:
            reject(sku, "focused_paused_or_control_capacity")
            continue
        product = catalog.get(sku)
        if (not isinstance(sku, str) or not SKU.fullmatch(sku) or not isinstance(product, dict)
                or product.get("kind") not in ("goods", "food") or product.get("category") not in categories
                or type(product.get("list_price")) is not int or product["list_price"] <= 0):
            reject(sku, "catalog_or_category_unavailable")
            continue
        is_food = product["kind"] == "food"
        if is_food and (not food_enabled or product["list_price"] > food_rules["max_list_price"]
                        or not number(product.get("shelf_hours"))
                        or product["shelf_hours"] < food_rules["min_shelf_hours"]):
            reject(sku, "food_shelf_life_or_price_limit")
            continue
        thresholds = food_rules if is_food else rules
        if (profile["sales"] < thresholds["minimum_public_sales"]
                or profile["buyers"] < thresholds["minimum_public_buyers"]
                or profile.get("named_shops", 0) < rules["minimum_public_shops"]
                or factors.get(sku, 1.0) > rules["max_surge_factor"]):
            reject(sku, "surge_above_limit" if factors.get(sku, 1.0) > rules["max_surge_factor"]
                   else "insufficient_fresh_public_demand")
            continue
        own = economics.get(sku, {})
        recovered = refund_recovery(own, cfg)
        if (own.get("unknown", 0) or own.get("refunded", 0) and not recovered
                or own.get("mature", 0) and own.get("net_estimate", 0) <= 0):
            reject(sku, "unknown_cost_or_unprofitable_refund_history")
            continue
        depth = depth_evidence(own, cfg, product["kind"])
        cap = depth["depth_cap"]
        if held[sku] >= cap:
            reject(sku, "held_depth_limit")
            continue
        costed = {**product, "worst_cost": worst_cost(product["list_price"])}
        if costed["worst_cost"] > budget or exposure.reason(sku, costed) is not None:
            reject(sku, exposure.reason(sku, costed) or "cash_reserve")
            continue
        pressure = profile.get("sample_to_stock_ratio")
        measured = number(pressure) and pressure >= 0
        # Same-window sampled turnover value per advertised stock unit. This is
        # a comparison statistic, not a probability or an hourly sales forecast.
        activity = profile["median_price"] * (pressure if measured else profile["sales"])
        diversity = min(profile["buyers"], 10)
        comparable = next((p for p in profile["comparables"]
                           if p["grade"] == "A" and p["warranty_hours"] == 6), None)
        quality_price = (comparable["price"] / product["list_price"] if comparable else 0)
        evidence = {"kind": product["kind"], **depth,
                    "refund_recovery": recovered,
                    "own_mature": own.get("mature", 0), "own_net_estimate": own.get("net_estimate", 0),
                    "sales": profile["sales"], "buyers": profile["buyers"],
                    "named_shops": profile["named_shops"], "observed_shop_urls": profile["shops"],
                    "median_price": profile["median_price"], "surge_factor": factors.get(sku, 1.0),
                    "sample_to_stock_ratio": pressure, "observed_stock": profile.get("observed_stock"),
                    "sampled_turnover_value": round(activity, 2),
                    "comparable_A_6h_price": comparable["price"] if comparable else None,
                    "evidence_id": profile["evidence_id"],
                    "coverage": "sampled public settlements; delivered cost and profit remain unknown"}
        # Measured stock pressure precedes the unadjusted fallback statistic.
        # Buyer diversity and comparable quality/terms prevent price alone from
        # deciding which pilot to try; all money/stock caps are applied later.
        rank = (-int(proven(own, cfg["min_mature_sales"])), -int(measured), -activity, -diversity, -quality_price,
                -min(profile["named_shops"], 10), product["list_price"], sku)
        turnover = bool(turnover_enabled and not is_food and product["list_price"] <= turnover_rules["max_list_price"])
        evidence["turnover_candidate"] = turnover
        row = {"product": costed, "evidence": evidence, "cap": cap, "turnover_candidate": turnover}
        if is_food:
            evidence.update(catalog_shelf_hours=product["shelf_hours"],
                            shelf_life_basis="catalog duration; remaining expiry must be checked after delivery")
            # Cheap, diverse demand comes before food price: no inference that
            # a costly item is more profitable or that its expiry is verified.
            rank = (-int(measured), -pressure if measured else -profile["sales"],
                    -diversity, -min(profile["named_shops"], 10), -profile["sales"],
                    product["list_price"], sku)
            food_ranked.append((rank, sku, row))
        else:
            ranked.append((rank, sku, row))
            if turnover:
                # Reserve room for affordable demand before turnover value
                # can let many high-ticket products crowd out this whole set.
                # A sampled ratio is not a forecast, and every safety gate
                # above remains identical to the ordinary goods candidate.
                turnover_rank = (-int(proven(own, cfg["min_mature_sales"])), -profile["buyers"],
                                 -int(measured), -pressure if measured else 0,
                                 -profile["sales"], product["list_price"], sku)
                turnover_ranked.append((turnover_rank, sku, row))
    food_slots = min(len(food_ranked), food_rules["max_candidates"], rules["max_candidates"]) if food_enabled else 0
    if turnover_enabled:
        affordable = sorted(turnover_ranked)[:min(turnover_rules["max_candidates"], rules["max_candidates"])]
        chosen = {sku for _, sku, _ in affordable}
        food_slots = min(food_slots, rules["max_candidates"] - len(affordable))
        generic_slots = rules["max_candidates"] - len(affordable) - food_slots
        generic = [row for row in sorted(ranked) if row[1] not in chosen][:generic_slots]
        selected = affordable + generic + sorted(food_ranked)[:food_slots]
    else:
        selected = sorted(ranked)[:rules["max_candidates"] - food_slots] + sorted(food_ranked)[:food_slots]
    selected_skus = {sku for _, sku, _ in selected}
    for _, sku, _ in ranked + food_ranked:
        if sku not in selected_skus:
            reject(sku, "candidate_pool_limit")
    return {sku: data for _, sku, data in selected}


def remaining_credit(row, capacity=1):
    if type(capacity) is not int or not 1 <= capacity <= 4:
        raise ValueError("Invalid adaptive credit capacity")
    return max(0, capacity - max(0, row["planned_qty"] - row["settled_qty"] - row.get("retry_credit", 0)))


def prepare_control(previous, memory, pool, rules, *, lifecycle_stats, held,
                    next_window=False, unresolved_receipts=False):
    """Retain pending pilots when the market's preferred SKUs change.

    Retire only inactive, fully settled counters with no held/missing units.
    A full bounded map refuses new pilots rather than forgetting uncertain buys.
    """
    if (previous is not None and (not isinstance(previous, dict)
            or len(previous) > rules["max_retained_skus"]
            or any(not isinstance(sku, str) or not SKU.fullmatch(sku) for sku in previous))):
        raise ValueError("Invalid adaptive supply control")
    controls = dict(previous or {})
    arguments = {"lifecycle_stats": lifecycle_stats, "held": held, "next_window": next_window,
                 "unresolved_receipts": unresolved_receipts}
    controls = observe_control(controls, memory, {"primary_skus": list(controls), "secondary_skus": []}, **arguments)
    for sku in list(controls):
        row, lifecycle = controls[sku], lifecycle_stats.get(sku, {})
        if (sku not in pool and row["planned_qty"] <= row["settled_qty"] and not held.get(sku, 0)
                and not lifecycle.get("unresolved_cost", 0) and not lifecycle.get("held_expired_cost", 0)):
            del controls[sku]
    additions = [sku for sku in pool if sku not in controls][:max(0, rules["max_retained_skus"] - len(controls))]
    all_skus = list(controls) + additions
    return observe_control(controls, memory, {"primary_skus": all_skus, "secondary_skus": []},
                           **{**arguments, "next_window": False})


def paused_skus(inv, economics, lifecycle, cfg, now):
    """Mirror the planner's stock uncertainty exclusions before limiting the pool."""
    paused = {u["sku"] for u in inv["units"] if number(u.get("supplied_at"))
              and now - u["supplied_at"] >= cfg["slow_stock_hours"] * 3600}
    paused |= {sku for sku, row in lifecycle.items()
               if row.get("unresolved_cost", 0) > 0 or row.get("held_expired_cost", 0) > 0}
    return paused
