"""Deterministic, bounded 20-minute policies; no network, LLM, or extra scheduler.

State belongs in the existing locked hub. Missing observations keep the policy.
Old stock is a *risk* signal, never proof of no sales over its entire lifetime.
"""
from __future__ import annotations

import json
import math
import sys

INTERVAL_S = 1200
MAX_FAILURE_EVENTS = 600
STOCKOUT_SKIP_S = 600       # buyer skips a (shop, SKU) pair after one "out of stock" reply
MISMATCH_SKIP_S = 300       # ... after one "no unit meets expiry/grade/warranty" reply


def number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def note_ledger_coverage(hub, rows, now):
    """Prove continuous sequence coverage; a truncated/gapped tail restarts warmup."""
    coverage = hub.setdefault("adaptive_ledger_coverage", {})
    entries = {int(e["seq"]): e for e in rows
               if number(e.get("seq")) and number(e.get("ts")) and 0 <= e["ts"] <= now}
    for seq, entry in sorted(entries.items()):
        previous = coverage.get("seq")
        if previous is not None and seq <= previous:
            continue
        if previous is None or seq != previous + 1:
            coverage["since"] = entry["ts"]
        coverage.update(seq=seq, through=entry["ts"])


def decide_restock(hub, inv, now, *, inventory_fresh=False):
    """Record observations only; no expansion/recovery overrides affect buying.

    All event counts use (now-1200, now]. At least 20 min continuous ledger
    coverage plus fresh, fully costed/aged inventory is required for a change.
    """
    state = hub.setdefault("adaptive_strategy", {})
    detection_since = hub.setdefault("no_rebuy_detection_since", now)
    state["mode"] = "metrics-only"
    state.pop("exploration_factor", None)
    state.pop("exploration_skus", None)
    if now - state.get("last_decision", -INTERVAL_S) < INTERVAL_S:
        return state
    state["last_decision"] = now
    coverage = hub.get("adaptive_ledger_coverage", {})
    units = inv.get("units")
    complete = (inventory_fresh and isinstance(units, list) and number(inv.get("balance"))
                and number(coverage.get("since")) and coverage["since"] <= now - INTERVAL_S
                and number(coverage.get("through")) and 0 <= now - coverage["through"] <= 120
                and all(isinstance(u, dict) and number(u.get("cost")) and u["cost"] >= 0
                        and number(u.get("supplied_at")) and 0 < u["supplied_at"] <= now
                        and u.get("sku") for u in units))
    if not complete:
        state["reason"] = "incomplete-observation: policy held"
    else:
        active = [u for u in units if u.get("grade") != "expired"]
        sales = [e for e in hub.get("own_sale_events", {}).values()
                 if now - INTERVAL_S < e.get("ts", 0) <= now]
        radar = hub.get("demand_radar", {})
        misses = sum(sum(now - INTERVAL_S < t <= now for t in times)
                     for times in radar.get("missed_ts", {}).values())
        cost = sum(u["cost"] for u in active)
        # Same supplied_at/no-sale definition as buying; exposure is a metric only.
        old_cost = sum(u["cost"] for u in active
                       if now - max(u["supplied_at"], detection_since) >= 4 * 3600
                       and hub.get("own_last_sale_ts", {}).get(u["sku"], 0) < u["supplied_at"])
        obs = {"at": now, "since": now - INTERVAL_S, "balance": inv["balance"],
               "own_sales": len(sales), "misses": misses, "stock_cost": cost,
               "old_cost": old_cost, "old_cost_share": old_cost / cost if cost else 0,
               "held_skus": len({u["sku"] for u in active})}
        state["reason"] = "observations only: POLICY section 9 controls buying"
        state["baseline"] = obs
    # JSON-escaped, local numeric/categorical summary only; never seller prose.
    return state


def record_failure(hub, order, url, sku, reason, now):
    """One failure per order/pair, not one per counter/retry. No inferred shop guilt."""
    if not order or reason not in ("price", "rejected", "timeout", "network-timeout", "unsigned",
                                   "stockout", "unit_mismatch"):
        return
    state = hub.setdefault("adaptive_strategy", {})
    events = [e for e in state.get("buyer_failures", []) if now - INTERVAL_S < e["ts"] <= now]
    event = {"order": str(order), "url": url.rstrip("/"), "sku": sku,
             "reason": reason, "ts": now}
    if not any((e["order"], e["url"], e["sku"]) == (event["order"], event["url"], sku) for e in events):
        events.append(event)
    state["buyer_failures"] = events[-MAX_FAILURE_EVENTS:]


def refresh_buyer_policy(hub, now):
    state = hub.setdefault("adaptive_strategy", {})
    if now - state.get("buyer_last_decision", -INTERVAL_S) < INTERVAL_S:
        return
    state["buyer_last_decision"] = now
    events = [e for e in state.get("buyer_failures", []) if now - INTERVAL_S < e["ts"] <= now]
    state["buyer_failures"] = events[-MAX_FAILURE_EVENTS:]
    grouped = {}
    for e in events:
        # Only transport/signature failures span SKUs. Price/stock stay pair-local.
        scope = "" if e["reason"] in ("network-timeout", "unsigned") else e["sku"]
        key = (e["url"], scope, e["reason"])
        grouped.setdefault(key, set()).add(e["order"])
    cooldowns = [{"url": url, "sku": sku, "reason": reason, "failures": len(orders),
                  "until": now + INTERVAL_S, "hard": reason == "unsigned"}
                 for (url, sku, reason), orders in sorted(grouped.items()) if len(orders) >= 3]
    state["buyer_cooldowns"] = cooldowns
    print(json.dumps(f"[buyer] [adaptive] 20m buyer cooldowns={len(cooldowns)} events={len(events)}"),
          file=sys.stderr, flush=True)


def filter_candidates(candidates, hub, now):
    """Keep ordinary utility ranking; if all soft-cooled, try the least failed safe pair."""
    active = [c for c in hub.get("adaptive_strategy", {}).get("buyer_cooldowns", [])
              if now < c["until"] <= now + INTERVAL_S]
    # A single stock-out/mismatch reply is enough: the pair is skipped outright (no fallback),
    # unlike price/rejection cooldowns that need three distinct orders.
    stale = {(e["url"], e["sku"]) for e in hub.get("adaptive_strategy", {}).get("buyer_failures", [])
             if (e.get("reason") == "stockout" and 0 <= now - e["ts"] < STOCKOUT_SKIP_S)
             or (e.get("reason") == "unit_mismatch" and 0 <= now - e["ts"] < MISMATCH_SKIP_S)}
    available, fallback = [], []
    for candidate in candidates:
        _, shop, sku = candidate
        if (shop["url"].rstrip("/"), sku["sku"]) in stale:
            continue
        matches = [c for c in active if c["url"] == shop["url"]
                   and (not c["sku"] or c["sku"] == sku["sku"])]
        if not matches:
            available.append(candidate)
        elif not any(c["hard"] for c in matches) and not shop.get("dishonest"):
            fallback.append((sum(c["failures"] for c in matches), candidate))
    if available:
        return available
    return [min(fallback, key=lambda pair: (pair[0], -pair[1][0]))[1]] if fallback else []
