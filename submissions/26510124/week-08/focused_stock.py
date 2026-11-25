"""Small, evidence-gated goods pilots. No model, network, or purchases here."""
from copy import deepcopy

from feedback import number
from market_evidence import demand_profiles
from supply_pricing import surge_factors
from replenishment_profit import refund_recovery


def configuration(cfg):
    focus = cfg.get("focused_goods", {})
    return focus if isinstance(focus, dict) and focus.get("enabled") is True else None


def proven(metric, minimum):
    return (metric.get("mature", 0) >= minimum and metric.get("net_estimate", 0) > 0
            and metric.get("speed_score", 0) > 0 and not metric.get("unknown", 0)
            and not metric.get("refunded", 0))


def epoch_control(previous, saved_reset_at, memory):
    """A verified newer ledger reset rebases counters; absent data never does."""
    incoming = memory.get("reset_at")
    for value in (saved_reset_at, incoming):
        if value is not None and (not number(value) or value < 0):
            raise ValueError("Invalid focused supply epoch")
    if incoming is None:
        return previous, saved_reset_at
    if saved_reset_at is not None and incoming < saved_reset_at:
        raise ValueError("Stale focused supply epoch")
    if saved_reset_at is None or incoming > saved_reset_at:
        return None, incoming
    return previous, saved_reset_at


def observe_control(previous, memory, focus, *, lifecycle_stats=None, held=None,
                    next_window=False, unresolved_receipts=False):
    """Count only new confirmed settlements, retaining a bounded replay cursor.

    Existing history is a baseline, not credit for retrying a failed future
    buy. Emitted supply plans consume outstanding capacity until a subsequent
    settlement is observed. A later window can authorize one bounded retry
    when neither an acquisition nor a sale was observed; repeated ambiguity
    then pauses the SKU instead of silently replenishing it forever.
    """
    skus = focus["primary_skus"] + focus["secondary_skus"]
    if previous is not None and (not isinstance(previous, dict) or set(previous) - set(skus)):
        raise ValueError("Invalid focused supply control")
    result = deepcopy(previous or {})
    for sku in skus:
        sales = [s for s in memory.get("sales", []) if s.get("sku") == sku
                 and type(s.get("ledger")) is int and s["ledger"] > 0]
        newest = max((s["ledger"] for s in sales), default=0)
        if sku not in result:
            result[sku] = {"planned_qty": 0, "settled_qty": 0, "last_ledger": newest,
                           "retry_credit": 0, "observed_at_plan": 0}
            continue
        row = result[sku]
        if (not isinstance(row, dict) or set(row) != {"planned_qty", "settled_qty", "last_ledger", "retry_credit", "observed_at_plan"}
                or any(type(v) is not int or v < 0 for v in row.values())):
            raise ValueError("Invalid focused supply counters")
        fresh = {s["ledger"] for s in sales if s["ledger"] > row["last_ledger"]}
        row["settled_qty"] += len(fresh)
        row["last_ledger"] = max(row["last_ledger"], newest)
        observed = (lifecycle_stats or {}).get(sku, {})
        if (next_window and lifecycle_stats is not None and held is not None
                and not unresolved_receipts and row["planned_qty"] > row["settled_qty"]
                and row["retry_credit"] == 0 and not fresh and not held.get(sku, 0)
                and not observed.get("unresolved_cost", 0)
                and observed.get("observed_units", 0) == row["observed_at_plan"]):
            row["retry_credit"] = 1  # One bounded retry, not proof the first buy failed.
    return result


def gates(inv, candidates, economics, cfg, now, public, control):
    focus = configuration(cfg)
    if not focus:
        return None
    profiles = demand_profiles(public, now)
    factors = surge_factors(inv)
    eligible, excluded, evidence = {}, {}, {}
    primaries, secondaries = focus["primary_skus"], focus["secondary_skus"]
    primary_proven = any(proven(economics.get(s, {}), cfg["min_mature_sales"])
                         or refund_recovery(economics.get(s, {}), cfg) for s in primaries)
    for sku in primaries + secondaries:
        p = candidates.get(sku)
        row = (control or {}).get(sku, {"planned_qty": 0, "settled_qty": 0})
        profile = profiles.get(sku, {})
        initial = row["planned_qty"] == 0
        own = economics.get(sku, {})
        minimum_sales = 1 if initial and sku in primaries else focus["minimum_public_sales"]
        minimum_buyers = 1 if initial and sku in primaries else focus["minimum_public_buyers"]
        reason = None
        if not p or p.get("kind") != "goods":
            reason = "focused_product_unavailable"
        elif factors is None:
            reason = "surge_unknown"
        elif factors.get(sku, 1.0) > focus["max_surge_factor"]:
            reason = "surge_above_limit"
        elif profile.get("sales", 0) < minimum_sales or profile.get("buyers", 0) < minimum_buyers:
            reason = "insufficient_fresh_public_demand"
        elif own.get("unknown", 0) or own.get("refunded", 0) and not refund_recovery(own, cfg):
            reason = "own_cost_unknown_or_refund_observed"
        elif own.get("mature", 0) and own.get("net_estimate", 0) <= 0:
            reason = "own_mature_profit_not_positive"
        elif sku in secondaries and not primary_proven:
            reason = "secondary_needs_mature_primary_profit"
        cap = 2 if sku in primaries and (proven(own, cfg["min_mature_sales"])
                                       or refund_recovery(own, cfg)) else 1
        outstanding = max(0, row["planned_qty"] - row["settled_qty"] - row.get("retry_credit", 0))
        if reason is None and outstanding >= cap:
            reason = "awaiting_own_settlement_or_supply_reconciliation"
        evidence[sku] = {"sales": profile.get("sales", 0), "buyers": profile.get("buyers", 0),
                         "surge_factor": factors.get(sku, 1.0) if factors is not None else None,
                         "outstanding_planned_units": outstanding, "depth_cap": cap,
                         "initial_pilot": initial,
                         "unconfirmed_supply_retry_credit": row.get("retry_credit", 0),
                         "observed_stock": profile.get("observed_stock"),
                         "sample_to_stock_ratio": profile.get("sample_to_stock_ratio"),
                         "coverage": "sampled completed purchases; not unmet demand or guaranteed sales"}
        if reason:
            excluded[sku] = reason
        else:
            eligible[sku] = {"cap": cap, "remaining_credit": cap - outstanding,
                             "secondary": sku in secondaries,
                             "reason": "focused_first_pilot" if initial else "focused_confirmed_replenishment"}
    return {"eligible": eligible, "excluded": excluded, "evidence": evidence,
            "order": primaries + secondaries}
