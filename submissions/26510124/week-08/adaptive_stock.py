"""Small goods pilots from observed demand, with bounded supply uncertainty.

Public prices rank trading activity, not predicted profit: grade and actual
cost are unknown until delivery. Cash planning still reserves maximum cost.
"""
from collections import Counter
import math
import re

from feedback import number
from focused_stock import observe_control
from market_evidence import demand_profiles
from stock_budget import StockBudget
from supply_pricing import surge_factors, worst_cost

SKU = re.compile(r"[GF][0-9a-f]{8}\Z")


def configuration(cfg):
    value = cfg.get("adaptive_goods")
    focus = cfg.get("focused_goods", {})
    return (value if isinstance(value, dict) and value.get("enabled") is True
            and focus.get("enabled") is True else None)


def candidates(inv, catalog, categories, economics, cfg, now, public, *, paused=(), allowed=None):
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
    ranked = []
    for sku, profile in profiles.items():
        if sku in excluded or held[sku] or allowed is not None and sku not in allowed:
            continue
        product = catalog.get(sku)
        if (not isinstance(sku, str) or not SKU.fullmatch(sku) or not isinstance(product, dict)
                or product.get("kind") != "goods" or product.get("category") not in categories
                or type(product.get("list_price")) is not int or product["list_price"] <= 0
                or profile["sales"] < rules["minimum_public_sales"]
                or profile["buyers"] < rules["minimum_public_buyers"]
                or profile.get("named_shops", 0) < rules["minimum_public_shops"]
                or factors.get(sku, 1.0) > rules["max_surge_factor"]):
            continue
        own = economics.get(sku, {})
        if (own.get("unknown", 0) or own.get("refunded", 0)
                or own.get("mature", 0) and own.get("net_estimate", 0) <= 0):
            continue
        costed = {**product, "worst_cost": worst_cost(product["list_price"])}
        if costed["worst_cost"] > budget or exposure.reason(sku, costed) is not None:
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
        evidence = {"sales": profile["sales"], "buyers": profile["buyers"],
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
        rank = (-int(measured), -activity, -diversity, -quality_price,
                -min(profile["named_shops"], 10), product["list_price"], sku)
        ranked.append((rank, sku, {"product": costed, "evidence": evidence}))
    return {sku: data for _, sku, data in sorted(ranked)[:rules["max_candidates"]]}


def remaining_credit(row):
    return max(0, 1 - max(0, row["planned_qty"] - row["settled_qty"] - row.get("retry_credit", 0)))


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
