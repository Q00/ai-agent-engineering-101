"""No-LLM stock planner: local economics plus bounded, cached public evidence.

Keep 40% of starting capital in cash, budget worst-case supply cost and count
all inventory against caps. Print instructions; the official kit buys stock.
"""
import argparse
from collections import Counter
import fcntl
import math
from pathlib import Path
import json
import time

from feedback import append_event, atomic_json, digest, load_json, number
from focused_stock import configuration as focused_config, gates as focused_gates, observe_control, epoch_control
from adaptive_stock import (configuration as adaptive_config, candidates as adaptive_candidates,
                            prepare_control as prepare_adaptive, remaining_credit as adaptive_credit,
                            paused_skus as adaptive_paused)
from inventory import Inventory, read_json
from sales_performance import sku_metrics, update as update_sales
from market_evidence import demand_scores, demand_profiles, refresh as refresh_market
from market_clock import schedule_now
from hourly_demand import refresh as refresh_demand, ranks as demand_ranks
from opening_strategy import update as update_openings
from shop_policy import policy_at
from shop_horizon import phase as round_phase, purchase_window
from stock_budget import StockBudget
from stock_lifecycle import update as update_stock, metrics as stock_metrics
from supply_pricing import worst_cost

ROOT = Path(__file__).resolve().parent
WINDOW_HANDOFF_SECONDS = 10
SUPPLY_LINE_SECONDS = 10


def supply_window(inv, now):
    """Use the kit's explicit supply schedule, never infer a 20-minute clock."""
    raw = inv.get("restock")
    if not isinstance(raw, dict) or type(raw.get("open")) is not bool:
        return {"status": "window_invalid"}
    dates = {k: raw[k] for k in ("opens_at", "closes_at")
             if number(raw.get(k)) and raw[k] >= 0}
    if raw["open"] is False:
        return {"status": "window_closed", **dates}
    if (len(dates) != 2 or dates["closes_at"] <= dates["opens_at"]
            or not number(now)):
        return {"status": "window_invalid", **dates}
    status = ("window_closed" if now < dates["opens_at"] or now >= dates["closes_at"]
              else "window_closing" if dates["closes_at"] - now <= WINDOW_HANDOFF_SECONDS
              else "window_open")
    return {"status": status, **dates}


def _skip(root, path, state, window, now, status=None):
    """One diagnostic per state transition, rather than one row every hook."""
    event = {**window, "status": status or window["status"]}
    if state.get("last_skip") != event:
        state["last_skip"] = event
        atomic_json(path, state)
        append_event(root / "logs/restock-decisions.jsonl", {
            "time": now, "round": state["round"], **event, "buy": []})
    return {"buy": []}


def plan(inv, catalog, demand, memory, attempts, cfg, now, public=None, *, lifecycle=None, horizon=None,
         focus_control=None, adaptive_control=None, research=None):
    if (not number(inv.get("balance")) or not number(inv.get("start")) or inv["start"] <= 0
            or not isinstance(catalog, dict) or len(catalog) > 20000
            or not isinstance(demand, list) or len(demand) > 5000):
        raise ValueError("Incomplete inventory, catalog or category data")
    reserve = math.ceil(inv["start"] * cfg["cash_reserve_percent"] / 100)
    budget = max(0, math.floor(inv["balance"]) - reserve)
    exposure = StockBudget(inv, cfg)
    # The live hook always supplies the validated round schedule. Pure callers
    # may omit it when testing the independent capital/selection calculations.
    stage = (round_phase(horizon, inv.get("round"), now) if horizon is not None else
             {"phase": "unscheduled_calculation", "allow_purchases": True, "allow_exploration": True})
    slots = min(cfg["max_units_per_run"], max(0, cfg["max_stock_units"] - len(inv["units"])))
    held = Counter()
    categories_held = Counter()
    for u in inv["units"]:
        if not isinstance(u, dict) or not isinstance(u.get("sku"), str) or not isinstance(u.get("category"), str):
            raise ValueError("Unknown stock units must not bypass caps")
        held[u["sku"]] += 1
        categories_held[u["category"]] += 1
    categories = {d["category"] for d in demand if isinstance(d, dict) and isinstance(d.get("category"), str)}
    candidates = {}
    focus = focused_config(cfg)
    candidate_skus = focus["primary_skus"] + focus["secondary_skus"] if focus else catalog
    for sku in candidate_skus:
        p = catalog.get(sku)
        if (isinstance(p, dict) and isinstance(sku, str) and isinstance(p.get("category"), str)
                and p["category"] in categories and p.get("kind") in ("food", "goods")
                and type(p.get("list_price")) is int and p["list_price"] > 0):
            # Grade and congestion are unknown until delivery. Reserve the
            # documented maximum surge even when the current factor is lower.
            candidates[sku] = {**p, "worst_cost": worst_cost(p["list_price"])}
    sales = memory["sales"]
    sku_sales = Counter(s["sku"] for s in sales)
    category_sales = Counter(s["category"] for s in sales)
    economics = sku_metrics(memory, now)
    lifecycle_stats = stock_metrics(lifecycle, now) if lifecycle is not None else {}
    focused = focused_gates(inv, candidates, economics, cfg, now, public, focus_control) if focus else None
    if focused:
        candidates = {sku: {**candidates[sku], "secondary_eligible": gate["secondary"]}
                      for sku, gate in focused["eligible"].items()}
    popular = demand_scores(public, now)
    slow = {u["sku"] for u in inv["units"] if number(u.get("supplied_at"))
            and now - u["supplied_at"] >= cfg["slow_stock_hours"] * 3600}
    paused = slow | {sku for sku, m in economics.items() if m["mature"] >= cfg["min_mature_sales"]
                     and (m["net_estimate"] <= 0 or m["refunded"] / m["settled"] >= .5)}
    # A vanished unit is not a confirmed sale or spoilage. Pause new stock of
    # that SKU while the separate lifecycle record awaits settlement evidence.
    paused |= {sku for sku, m in lifecycle_stats.items()
               if m["unresolved_cost"] > 0 or m["held_expired_cost"] > 0}
    adaptive_rules = adaptive_config(cfg)
    candidate_exclusions = {}
    adaptive = (adaptive_candidates(inv, catalog, categories, economics, cfg, now, public, paused=paused,
                                   allowed=adaptive_control, diagnostics=candidate_exclusions, research=research)
                if adaptive_rules and len(inv["units"]) < adaptive_rules["low_stock_units"]
                and stage["allow_exploration"] else {})
    candidates.update({sku: row["product"] for sku, row in adaptive.items()})
    windows = {sku: purchase_window(horizon, inv.get("round"), p, economics.get(sku), now)
               for sku, p in candidates.items()} if horizon is not None else {}
    tried = Counter(a["sku"] for a in attempts)
    buy, choices = Counter(), []
    remaining = budget
    replenishment = (adaptive_rules or {}).get("replenishment", {})
    coverage_enabled = replenishment.get("enabled") is True
    food_rules = (adaptive_rules or {}).get("food_pilots", {})
    food_limit = food_rules.get("max_units_per_run", 0) if food_rules.get("enabled") else 0

    def stock_cap(sku):
        if sku in adaptive:
            return min(cfg["max_units_per_sku"], adaptive[sku]["cap"])
        if focused:
            return min(cfg["max_units_per_sku"], focused["eligible"][sku]["cap"])
        cap = cfg["max_food_units_per_sku"] if candidates[sku]["kind"] == "food" else cfg["max_units_per_sku"]
        m = economics.get(sku, {})
        proven = (m.get("mature", 0) >= cfg["min_mature_sales"]
                  and m.get("net_estimate", 0) > 0 and m.get("speed_score", 0) > 0
                  and not m.get("unknown", 0) and not m.get("refunded", 0))
        # Normal warranty exposure is expected for a shop that keeps selling.
        # It cannot establish profitability by itself, but it must not erase
        # three mature profitable observations. While it remains, limit depth
        # to two; incomplete costs/refunds still limit new exposure to one.
        cap = min(cap, 2 if proven and m.get("pending", 0) else 3 if proven else 1)
        return cap

    def blockers(sku):
        """Use the same limits for every allocation pass and its diagnostics."""
        p, reasons = candidates[sku], []
        if not stage["allow_purchases"]:
            reasons.append("round_purchases_stopped")
        if sku in paused:
            reasons.append("paused_stock_or_economics")
        if len(choices) >= slots:
            reasons.append("total_unit_limit")
        if held[sku] + buy[sku] >= stock_cap(sku):
            reasons.append("sku_depth_limit")
        if sku in adaptive:
            credit = adaptive_credit((adaptive_control or {}).get(sku, {"planned_qty": 0, "settled_qty": 0}),
                                     capacity=stock_cap(sku))
            if buy[sku] >= credit:
                reasons.append("unconfirmed_supply_capacity")
            if sum(buy[s] for s in adaptive) >= adaptive_rules["max_units_per_run"]:
                reasons.append("adaptive_unit_limit")
            if len(inv["units"]) + len(choices) >= adaptive_rules["low_stock_units"]:
                reasons.append("adaptive_stock_target")
            if p["kind"] == "food" and sum(buy[s] for s in adaptive
                                          if candidates[s]["kind"] == "food") >= food_limit:
                reasons.append("food_unit_limit")
        elif focused and buy[sku] >= focused["eligible"][sku]["remaining_credit"]:
            reasons.append("unconfirmed_supply_capacity")
        if p["worst_cost"] > remaining:
            reasons.append("cash_reserve")
        cost_reason = exposure.reason(sku, p)
        if cost_reason:
            reasons.append(cost_reason)
        if not windows.get(sku, (True, None))[0]:
            reasons.append("insufficient_round_sell_time")
        return reasons

    def available():
        return [sku for sku in candidates if not blockers(sku)]

    def add(sku, reason):
        nonlocal remaining
        p = candidates[sku]
        exposure.add(sku, p)
        buy[sku] += 1
        remaining -= p["worst_cost"]
        categories_held[p["category"]] += 1
        choices.append({"sku": sku, "reason": reason, "budgeted_cost": p["worst_cost"]})

    turnover = adaptive_rules.get("turnover_goods", {}) if adaptive_rules else {}
    turnover_enabled = turnover.get("enabled") is True
    hourly_priority = demand_ranks(research, cfg, inv.get("round"), now)
    if hourly_priority:
        # Research changes order only: available() rechecks current funds,
        # supply credits, losses, per-SKU depth, food and horizon constraints.
        for _ in range(cfg["hourly_demand"]["priority_units_per_run"]):
            eligible = [sku for sku in available() if sku in hourly_priority and buy[sku] == 0]
            if not eligible:
                break
            sku = min(eligible, key=lambda s: hourly_priority[s])
            add(sku, "hourly_market_demand")
    if adaptive and turnover_enabled:
        priority = {sku: rank for rank, sku in enumerate(adaptive)}
        priority_slots = min(turnover["priority_units_per_run"], adaptive_rules["max_units_per_run"], slots,
                             max(0, adaptive_rules["low_stock_units"] - len(inv["units"])))
        for _ in range(priority_slots):
            eligible = [sku for sku in available() if sku in adaptive
                        and adaptive[sku].get("turnover_candidate") and buy[sku] == 0]
            if not eligible:
                break
            # Reserve room before expensive focused goods consume the budget.
            # Category breadth is a preference, never a requirement that would
            # exclude several independently demanded bedding products.
            sku = min(eligible, key=lambda s: (categories_held[candidates[s]["category"]], priority[s]))
            add(sku, "adaptive_affordable_turnover")

    if adaptive and coverage_enabled:
        # A small eligible food allocation must not depend on every proven
        # bedding position already being filled. It shares all existing caps.
        for _ in range(replenishment["food_priority_units"]):
            eligible = [sku for sku in available() if sku in adaptive and candidates[sku]["kind"] == "food"]
            if not eligible:
                break
            add(eligible[0], "adaptive_food_demand_pilot")

    if focused:
        def focus_rank(sku):
            evidence = focused["evidence"][sku]
            pressure = evidence.get("sample_to_stock_ratio")
            measured = number(pressure) and pressure >= 0
            return (int(focused["eligible"][sku]["secondary"]), held[sku] + buy[sku],
                    -int(measured), -pressure if measured else 0,
                    -evidence["buyers"] if measured else 0, focused["order"].index(sku))
        for _ in range(slots - len(choices)):
            eligible = [sku for sku in available() if sku in focused["eligible"]]
            if not eligible:
                break
            # Fill one of each primary before adding depth. Secondary stock is
            # considered only after its own evidence/profit/capital gates pass.
            sku = min(eligible, key=focus_rank)
            add(sku, focused["eligible"][sku]["reason"])
    if adaptive:
        early_units = sum(buy[sku] for sku in adaptive)
        alternative_slots = min(max(0, adaptive_rules["max_units_per_run"] - early_units), slots - len(choices),
                                max(0, adaptive_rules["low_stock_units"] - len(inv["units"]) - sum(buy.values())))
        priority = {sku: rank for rank, sku in enumerate(adaptive)}
        for _ in range(alternative_slots):
            eligible = [sku for sku in available() if sku in adaptive]
            if not eligible:
                break
            diverse = False
            if coverage_enabled and sum(buy[s] > 0 for s in adaptive) < replenishment["distinct_skus_before_depth"]:
                first_units = [s for s in eligible if buy[s] == 0]
                if first_units:
                    eligible, diverse = first_units, True
            def mix_rank(s):
                food = candidates[s]["kind"] == "food"
                # Affordable goods already received distinct priority slots.
                # Next replenish proven goods, then the small food allocation;
                # ordinary pilots fill unused slots. Keep legacy food-first
                # behavior when the optional turnover policy is disabled.
                tier = (0 if stock_cap(s) > 1 else 1 if food else 2) if turnover_enabled else int(not food)
                if coverage_enabled:
                    # Compare current activity only after proven profitability.
                    # Ratios balance coverage across SKUs; they are not forecasts
                    # of guaranteed sales or margins. Breadth is a soft target.
                    return (tier, 0 if diverse else (held[s] + buy[s]) / stock_cap(s),
                            -adaptive[s]["evidence"]["sampled_turnover_value"], priority[s])
                return tier, priority[s]
            sku = min(eligible, key=mix_rank)
            food = candidates[sku]["kind"] == "food"
            add(sku, "adaptive_food_demand_pilot" if food else
                "adaptive_proven_replenishment" if stock_cap(sku) > 1 else "adaptive_public_demand_pilot")
    exploration_slots = cfg["exploration_units"] if stage["allow_exploration"] and not focused else 0
    preferred_slots = max(0, slots - exploration_slots) if sales and not focused else 0
    for _ in range(preferred_slots):
        eligible = [s for s in available() if sku_sales[s] or category_sales[candidates[s]["category"]]]
        if not eligible:
            break
        def rank(s):
            m = economics.get(s, {})
            proven = m.get("mature", 0) >= cfg["min_mature_sales"] and m.get("net_estimate", 0) > 0
            return (-int(proven), -m.get("speed_score", 0) if proven else 0,
                    -sku_sales[s], -category_sales[candidates[s]["category"]],
                    held[s] + buy[s], candidates[s]["worst_cost"], s)
        sku = min(eligible, key=rank)
        add(sku, "profit_and_turnover" if economics.get(sku, {}).get("mature", 0) >= cfg["min_mature_sales"]
            else "limited_own_sample" if sku_sales[sku] else "own_settled_category")
    # With no sales evidence, all slots are small exploration buys. Once there
    # is evidence, unproven items never consume the performance allocation.
    explore_slots = min(slots - len(choices), exploration_slots if sales else slots) if stage["allow_exploration"] and not focused else 0
    for _ in range(explore_slots):
        eligible = [s for s in available() if held[s] + buy[s] == 0]
        if not eligible:
            break
        sku = min(eligible, key=lambda s: (-popular.get(s, 0), tried[s], categories_held[candidates[s]["category"]],
                    -category_sales[candidates[s]["category"]], digest([int(now // 86400), s])))
        add(sku, "public_demand_exploration" if popular.get(sku) else "small_exploration")
    status = (stage.get("reason", stage["phase"]) if not stage["allow_purchases"] else
              "cash_floor_no_stock" if budget == 0 and not inv["units"] else
              "cash_floor_hold" if budget == 0 else "planned" if choices else "no_eligible_stock")
    bankruptcy_eligible = (number(inv.get("bankrupt_line")) and inv["balance"] < inv["bankrupt_line"]
                           and type(inv.get("bankruptcies_left")) is int and inv["bankruptcies_left"] > 0)
    allocation_blocks = {sku: reasons for sku in candidates if (reasons := blockers(sku))}
    return {"buy": [{"sku": sku, "qty": qty} for sku, qty in buy.items()]}, {
        "cash_floor": reserve, "balance_observed": inv["balance"], "budgeted_cost": budget - remaining,
        "projected_min_balance": math.floor(inv["balance"]) - (budget - remaining),
        "choices": choices, "own_settled_sample": len(sales),
        "paused_skus": sorted(paused)[:20],
        "operation_status": status, "needs_attention": status == "cash_floor_no_stock",
        "bankruptcy_eligible": bankruptcy_eligible, "automatic_bankruptcy": False,
        "exposure": exposure.summary(), "horizon": stage,
        "focused_goods": focused,
        "hourly_demand": {"report_id": research.get("report_id") if hourly_priority else None,
                          "generated_at": research.get("generated_at") if hourly_priority else None,
                          "ranked_skus": len(hourly_priority),
                          "priority_units_planned": sum(c["reason"] == "hourly_market_demand" for c in choices),
                          "scope": "priority among currently eligible candidates; all purchase limits apply"},
        "adaptive_goods": {"enabled": bool(adaptive_rules),
            "exclusion_counts": dict(Counter(candidate_exclusions.values())),
            "excluded_candidates": dict(list(candidate_exclusions.items())[:32]),
            "excluded_candidates_truncated": len(candidate_exclusions) > 32,
            "max_units_per_run": adaptive_rules["max_units_per_run"] if adaptive_rules else None,
            "low_stock_units": adaptive_rules["low_stock_units"] if adaptive_rules else None,
            "observed_stock_units": len(inv["units"]), "pool": {
            sku: {**row["evidence"], "remaining_credit": adaptive_credit(
                (adaptive_control or {}).get(sku, {"planned_qty": 0, "settled_qty": 0}), capacity=stock_cap(sku))}
            for sku, row in adaptive.items()},
            "max_units_per_sku": (min(cfg["max_units_per_sku"], replenishment["max_units_per_sku"])
                                  if coverage_enabled else
                                  adaptive_rules.get("proven_max_units_per_sku", 1) if adaptive_rules else 1),
            "baseline_units_per_sku": adaptive_rules.get("proven_max_units_per_sku", 1) if adaptive_rules else 1,
            "pilot_units_per_sku": 1,
            "replenishment": {"enabled": coverage_enabled,
                "distinct_skus_planned": sum(buy[s] > 0 for s in adaptive),
                "distinct_skus_before_depth": replenishment.get("distinct_skus_before_depth")},
            "turnover_goods": {"enabled": turnover_enabled,
                "priority_units_planned": sum(c["reason"] == "adaptive_affordable_turnover" for c in choices),
                "max_list_price": turnover.get("max_list_price") if turnover_enabled else None},
            "food_max_units_per_run": (adaptive_rules.get("food_pilots", {}).get("max_units_per_run", 0)
                                       if adaptive_rules and adaptive_rules.get("food_pilots", {}).get("enabled") else 0)},
        "depth_limits": {sku: stock_cap(sku) for sku in buy},
        "allocation": {"candidate_count": len(candidates), "planned_units": len(choices),
                       "unit_limit": slots, "unused_units": max(0, slots - len(choices)),
                       "blocked_reason_counts": dict(Counter(r for reasons in allocation_blocks.values() for r in reasons)),
                       "blocked_candidates": dict(list(allocation_blocks.items())[:20]),
                       "blocked_candidates_truncated": len(allocation_blocks) > 20},
        "horizon_exclusions": dict(Counter(reason for allowed, reason in windows.values() if not allowed)),
        "stock_observations": {"held_cost": sum(m["held_cost"] for m in lifecycle_stats.values()),
                               "unresolved_cost": sum(m["unresolved_cost"] for m in lifecycle_stats.values()),
                               "expired_unconfirmed_cost": sum(m["expired_unconfirmed_cost"] for m in lifecycle_stats.values())},
        "wallet_profit": memory.get("wallet_profit"),
        "limits": "Sampled settlements/refunds; estimated storage/turnover. Missing units are uncertain losses, not proven spoilage; no automatic bankruptcy."}


def run_once(root=ROOT, now=None):
    now = time.time() if now is None else now
    started = time.monotonic()
    # Keep deterministic injected time, but still account for real network and
    # disk latency so an initially open window cannot authorize a late plan.
    clock = lambda: now + max(0, time.monotonic() - started)
    policy = policy_at(root)
    cfg = policy["restock"]
    if not cfg["enabled"]:
        return {"buy": []}
    md = root / ".market"
    md.mkdir(exist_ok=True)

    def market_time(rnd):
        return schedule_now(md, rnd, local_now=clock())

    def current_window(snapshot):
        authority = market_time(snapshot["round"])
        offset = authority.get("offset_seconds")
        # Keep transition logging stable despite subsecond Date/RTT jitter.
        return {**supply_window(snapshot, authority["now"]), "schedule_clock": {
            "status": authority["status"],
            "offset_seconds": round(offset / 60) * 60 if number(offset) else None}}

    with (md / "restock.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"buy": []}
        inv = Inventory(md).snapshot()
        if inv["round"] < 3:
            return {"buy": []}
        # Runs on the existing hook even when supply is closed. No new network
        # call: cached market evidence is collected by the existing poller.
        research = refresh_demand(root, inv, cfg, clock())
        path = md / "restock-control.json"
        state = load_json(path) if path.exists() else {"round": inv["round"], "attempts": []}
        if not isinstance(state, dict) or not isinstance(state.get("attempts"), list) or len(state["attempts"]) > 500:
            raise ValueError("Invalid restock control; refusing to reset limits")
        if state.get("round") != inv["round"]:
            state = {"round": inv["round"], "attempts": []}
        if state.get("last_run") is not None and not number(state["last_run"]):
            raise ValueError("Invalid restock interval state")
        previous = state.get("last_window")
        if previous is not None and (not isinstance(previous, dict)
                or any(not number(previous.get(k)) or previous[k] < 0 for k in ("opens_at", "closes_at"))
                or previous["closes_at"] <= previous["opens_at"]):
            raise ValueError("Invalid supply window control; refusing to reset it")
        window = current_window(inv)
        if window["status"] != "window_open":
            focus = focused_config(cfg)
            # The existing 60-second kit hook does this lightweight preparation
            # once per announced opening. It never sleeps or polls in a loop.
            opens = window.get("opens_at")
            if (focus and window["status"] == "window_closed" and number(opens)
                    and 0 < opens - market_time(inv["round"])["now"] <= focus.get("prepare_before_seconds", 180)
                    and state.get("prepared_opening") != opens):
                prepared = refresh_market(root, inv["round"], clock(), deadline_seconds=8)
                research = refresh_demand(root, inv, cfg, clock(), public=prepared)
                state["prepared_opening"] = opens
                profiles = demand_profiles(prepared, clock())
                state["prepared_demand_usable"] = any(
                    profiles.get(sku, {}).get("sales", 0) >= 1
                    and profiles.get(sku, {}).get("buyers", 0) >= 1
                    for sku in focus["primary_skus"] + focus["secondary_skus"])
                if not state["prepared_demand_usable"] and adaptive_config(cfg):
                    # Read already-cached files only; preparation never adds a
                    # network request or allocates inventory/supply credits.
                    try:
                        cached_catalog, cached_demand = read_json(md / "catalog.json"), read_json(md / "demand.json")
                        cached_categories = {r["category"] for r in cached_demand if isinstance(r, dict)
                                             and isinstance(r.get("category"), str)}
                        state["prepared_demand_usable"] = bool(adaptive_candidates(
                            inv, cached_catalog, cached_categories, {}, cfg, clock(), prepared))
                    except (ValueError, OSError, TypeError, KeyError):
                        state["prepared_demand_usable"] = False
                atomic_json(path, state)
            return _skip(root, path, state, window, clock())
        # One plan per observed opening, including server extensions of the
        # same window. Legacy last_run never hides the first open-window hook.
        if previous and window["opens_at"] <= previous["opens_at"]:
            return _skip(root, path, state, window, clock(), "already_planned")
        demand_path = md / "demand.json"
        if not -5 <= clock() - demand_path.stat().st_mtime <= 120:
            raise ValueError("Category file is stale")
        catalog, demand = read_json(md / "catalog.json"), read_json(demand_path)
        # Leave time for the kit handoff and each paced supply line.
        remaining = window["closes_at"] - market_time(inv["round"])["now"]
        retry_opening = (window["opens_at"] if state.get("prepared_opening") == window["opens_at"]
                         and state.get("prepared_demand_usable") is False else None)
        public = refresh_market(root, inv["round"], clock(),
                                opening_retry=retry_opening,
                                opening_retry_now=market_time(inv["round"])["now"] if retry_opening is not None else None,
                                deadline_seconds=max(1, min(12, remaining - WINDOW_HANDOFF_SECONDS - SUPPLY_LINE_SECONDS)))
        # The network read can outlive the previous inventory snapshot.
        inv = Inventory(md).snapshot()
        checked = current_window(inv)
        if checked["status"] != "window_open":
            return _skip(root, path, state, checked, clock())
        if inv["round"] != state["round"] or checked["opens_at"] != window["opens_at"]:
            return _skip(root, path, state, checked, clock(), "window_changed")
        current = clock()
        if public and public.get("round") != inv["round"]:
            public = None
        research = refresh_demand(root, inv, cfg, current, public=public)
        memory = update_sales(root, inv["round"], current, public=public, inventory=inv)
        lifecycle = update_stock(root, inv, memory, current)
        focus = focused_config(cfg)
        focused_control = None
        focused_epoch = state.get("focused_reset_at")
        lifecycle_stats = stock_metrics(lifecycle, current)
        adaptive_rules = adaptive_config(cfg)
        adaptive_control = None
        adaptive_epoch = state.get("adaptive_reset_at")
        if focus:
            held = Counter(u["sku"] for u in inv["units"])
            actual_units = {u["unit"] for u in inv["units"]}
            reservations = Inventory(md).holds
            confirmed_units = {s["unit"] for s in memory.get("sales", [])
                               if isinstance(s.get("unit"), str) and type(s.get("ledger")) is int
                               and s["ledger"] > 0}
            previous_focus, focused_epoch = epoch_control(state.get("focused_supply"), focused_epoch, memory)
            focused_control = observe_control(previous_focus, memory, focus,
                lifecycle_stats=lifecycle_stats, held=held,
                next_window=bool(previous and checked["opens_at"] > previous["opens_at"]),
                unresolved_receipts=bool(set(reservations) - actual_units - confirmed_units))
            if adaptive_rules:
                prior_adaptive, adaptive_epoch = epoch_control(state.get("adaptive_supply"), adaptive_epoch, memory)
                economics = sku_metrics(memory, current)
                categories = {d["category"] for d in demand if isinstance(d, dict)
                              and isinstance(d.get("category"), str)}
                pool = (adaptive_candidates(inv, catalog, categories, economics, cfg, current, public,
                                            paused=adaptive_paused(inv, economics, lifecycle_stats, cfg, current),
                                            research=research)
                        if len(inv["units"]) < adaptive_rules["low_stock_units"] else {})
                adaptive_control = prepare_adaptive(prior_adaptive, memory, pool, adaptive_rules,
                    lifecycle_stats=lifecycle_stats, held=held,
                    next_window=bool(previous and checked["opens_at"] > previous["opens_at"]),
                    unresolved_receipts=bool(set(reservations) - actual_units - confirmed_units))
        try:
            update_openings(root, inv, memory, public, policy, current)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            append_event(root / "logs/opening-strategy.jsonl", {"time": now, "status": "unavailable",
                         "error": type(exc).__name__})
        attempts = []
        for row in state["attempts"]:
            if not isinstance(row, dict) or not number(row.get("time")) or not isinstance(row.get("sku"), str):
                raise ValueError("Malformed stock planning memory")
            if 0 <= current - row["time"] <= 7 * 86400:
                attempts.append(row)
        # Reserve the official contract's ten-second spacing per supply line.
        # Counting units here is conservative: repeated SKUs collapse to fewer
        # lines, while cold-start exploration may use one line per unit.
        line_capacity = max(0, int((checked["closes_at"] - market_time(inv["round"])["now"]
                                    - WINDOW_HANDOFF_SECONDS) // SUPPLY_LINE_SECONDS))
        run_cfg = {**cfg, "max_units_per_run": min(cfg["max_units_per_run"], line_capacity)}
        decision, evidence = plan(inv, catalog, demand, memory, attempts, run_cfg, current, public,
                                  lifecycle=lifecycle, horizon=policy["horizon"], focus_control=focused_control,
                                  adaptive_control=adaptive_control, research=research)
        latest = Inventory(md).snapshot()
        checked = current_window(latest)
        if checked["status"] != "window_open":
            return _skip(root, path, state, checked, clock())
        if latest["round"] != state["round"] or checked["opens_at"] != window["opens_at"]:
            return _skip(root, path, state, checked, clock(), "window_changed")
        if latest != inv:
            return _skip(root, path, state, checked, clock(), "inventory_changed")
        if decision["buy"] and (checked["closes_at"] - market_time(latest["round"])["now"]
                                <= WINDOW_HANDOFF_SECONDS + SUPPLY_LINE_SECONDS * len(decision["buy"])):
            return _skip(root, path, state, checked, clock(), "window_closing")
        state["attempts"] = (attempts + [{"time": current, "sku": row["sku"], "planned_qty": row["qty"]}
                                        for row in decision["buy"]])[-500:]
        if focused_control is not None:
            for row in decision["buy"]:
                control = focused_control if row["sku"] in focused_control else adaptive_control
                control[row["sku"]]["planned_qty"] += row["qty"]
                control[row["sku"]]["observed_at_plan"] = lifecycle_stats.get(row["sku"], {}).get("observed_units", 0)
            state["focused_supply"] = focused_control
            state["focused_reset_at"] = focused_epoch
        if adaptive_control is not None:
            state["adaptive_supply"] = adaptive_control
            state["adaptive_reset_at"] = adaptive_epoch
        # Persist before returning instructions to the kit. A crash may skip a
        # buy, but cannot knowingly emit the same plan a second time.
        state.update(last_run=clock(), last_window={k: checked[k] for k in ("opens_at", "closes_at")})
        state.pop("last_skip", None)
        atomic_json(path, state)
        append_event(root / "logs/restock-decisions.jsonl", {"time": clock(), "round": inv["round"],
                     "status": "planned", "window": checked,
                     "supply_lines": len(decision["buy"]), "window_unit_cap": run_cfg["max_units_per_run"],
                     **decision, **evidence})
        return decision


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, required=True)
    args = ap.parse_args()
    try:
        if args.state.resolve() != (ROOT / ".market").resolve():
            raise ValueError("State must be this agent's .market directory")
        result = run_once()
    except Exception as exc:
        append_event(ROOT / "logs/restock-decisions.jsonl", {"time": time.time(),
                     "status": "refused", "error": type(exc).__name__, "detail": str(exc)[:300]})
        result = {"buy": []}  # A custom hook never falls back to random supply.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
