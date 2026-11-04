"""Bounded R3 policy trials with frozen windows, delayed evaluation and rollback.

No model/network calls or code editing. Called by the short-lived restock hook.
"""

import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from market_state import read, write
from strategy_policy import BASE, validate
from local_settlements import collect as local_settlements

ROOT = Path(__file__).resolve().parent


def config():
    value = read(ROOT/"strategy_controller.json")
    if (value["check_interval_seconds"] < 1800 or value["min_change_seconds"] < 7200
            or value["baseline_seconds"] < 7200 or value["trial_seconds"] < 7200
            or value["maturity_seconds"] < 21600 or not 0 < value["price_step"] <= .03
            or not 1 <= value["max_daily_experiments"] <= 2 or value["min_new_deals"] < 10
            or value["min_target_trades"] < 3 or not 0 < value["lease_seconds"] <= 3600):
        raise ValueError("Controller configuration violates experiment bounds")
    if (type(value["enabled"]) is not bool or not 0 < value["min_gain"] <= .2
            or not 0 < value["max_loss"] <= .1 or not .05 <= value["rating_drop"] <= .1
            or value["check_interval_seconds"] > value["lease_seconds"]/2
            or value["max_trial_seconds"] <= value["trial_seconds"]+value["maturity_seconds"]):
        raise ValueError("Invalid evaluation, rating or lease settings")
    return value


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def compact(observation):
    return {k: copy.deepcopy(v) for k, v in observation.items()
            if k not in ("own_sales", "price_candidates", "stock", "local_counters")}


def initial():
    return {"schema": 1, "round": 3, "controls": copy.deepcopy(BASE), "active": None,
            "history": [], "last_change_at": -86400, "day": "", "experiments_today": 0,
            "version": 0, "phase": "collecting_baseline"}


def rates(start, end):
    hours = (end["at"]-start["at"])/3600
    if hours <= 0:
        raise ValueError("Nonpositive observation window")
    return {"deals": (end["deals"]-start["deals"])/hours,
            "revenue": (end["revenue"]-start["revenue"])/hours,
            "operating": (end["operating_value"]-start["operating_value"])/hours}


def cohort(entries, sku, grade, start, end, experiment=None):
    return {str(r["ledger"]): r for r in entries if r["sku"] == sku and r["grade"] == grade
            and r.get("warranty_hours", 0) == 0 and start < r["ts"] <= end
            and (experiment is None or r.get("experiment") == experiment)}


def cohort_rate(records, start, end):
    hours = (end-start)/3600
    return {"count": len(records), "deals_per_hour": len(records)/hours,
            "gross_per_hour": sum(r["amount"]-r["cost"] for r in records.values())/hours}


def choose(observation, controls, settings):
    choices = []
    for item in observation.get("price_candidates", []):
        if not observation.get("price_consumer_ready") or item.get("kind") != "goods":
            continue
        sku, median = item["sku"], item.get("median")
        quote, floor = item.get("quote"), item.get("floor")
        if not all(finite(v) for v in (median, quote, floor)) or item.get("count", 0) < 3 or quote <= floor:
            continue
        old = controls["quote_multipliers"].get(sku, 1)
        direction = -1 if quote > median*1.05 else 1 if quote < median*.95 else 0
        multiplier = round(min(1.03, max(.94, old*(1+direction*settings["price_step"]))), 4)
        expected = max(floor, min(item["list_price"], math.ceil(quote*multiplier/old)))
        if not direction or multiplier == old or expected == quote:
            continue
        candidate = copy.deepcopy(controls)
        if sku not in candidate["quote_multipliers"] and len(candidate["quote_multipliers"]) >= 20:
            continue
        candidate["quote_multipliers"][sku] = multiplier
        choices.append({"kind": "quote_cut" if direction < 0 else "quote_raise", "sku": sku,
                        "grade": item["grade"], "candidate": validate(candidate),
                        "reason": "quote_above_comparables" if direction < 0 else "quote_below_comparables",
                        "gap_ratio": abs(quote/median-1), "expected_quote": expected,
                        "comparable_price": median, "sample_count": item["count"],
                        "benchmark_source": item.get("benchmark_source", "market_settlements"),
                        "benchmark_basis": item.get("benchmark_basis"),
                        "leader_logins": item.get("leader_logins", []),
                        "leader_ledger_refs": item.get("leader_ledger_refs", [])})
    choices.sort(key=lambda c: (c["benchmark_source"] != "top3_settlements", -c["gap_ratio"], c["sku"]))
    if choices:
        return choices
    if (observation.get("aged_units", 0) >= 3 and observation["stock_cost"] > observation["balance"]*.2
            and controls["restock_budget_rate"] > .02):
        candidate = copy.deepcopy(controls)
        candidate["restock_budget_rate"] = round(max(.02, candidate["restock_budget_rate"]-.01), 2)
        return [{"kind": "restock_throttle", "candidate": validate(candidate), "reason": "capital_tied_in_aged_stock"}]
    return []


def transition(original, observation, settings):
    """Pure state transition; tests advance timestamps without sleeping or trading."""
    state = copy.deepcopy(original or initial())
    if state.get("schema") != 1 or state.get("round") != 3:
        raise ValueError("Controller state must belong to Round 3 schema 1")
    validate(state["controls"])
    now = observation["at"]
    events = []
    def event(event_name, **details):
        events.append({"event": event_name, "at": now, **details})
    def rollback(reason):
        active = state["active"]
        if active:
            state["controls"] = active["before"]
            state["active"] = None
            state["last_change_at"] = now
            event("rollback", experiment=active["id"], reason=reason)
    if not settings["enabled"]:
        rollback("operator_disabled")
        state.update(controls=copy.deepcopy(BASE), phase="disabled")
        return state, events
    if not observation.get("valid"):
        rollback("stale_or_invalid_observation")
        state["phase"] = "waiting_for_fresh_r3_data"
        return state, events
    if observation.get("objective") and state.get("objective") != observation["objective"]:
        previous_objective = state.get("objective", "player_top3")
        rollback("operator_objective_changed")
        state["objective"] = observation["objective"]
        event("objective_changed", before=previous_objective, after=state["objective"])
    state.pop("wait_reason", None)
    if observation.get("self_evidence") == "local_acp_v1" and state.get("self_evidence") != "local_acp_v1":
        rollback("self_evidence_source_migration")
        counters = observation["local_counters"]
        for previous in state["history"]:
            confirmed = [r for r in counters if r["ts"] <= previous["at"]]
            previous.update(deals=len(confirmed), revenue=sum(r["amount"] for r in confirmed), self_evidence="local_acp_v1")
        state["self_evidence"] = "local_acp_v1"
        event("self_evidence_migrated", source="local_acp_v1", confirmed=observation["deals"])
    if observation["balance"] <= observation["reserve"]:
        rollback("cash_reserve_guard")
        state["controls"]["restock_paused"] = True
        state["phase"] = "cash_guard"
        event("cash_guard", balance=observation["balance"], reserve=observation["reserve"])
        return state, events
    if state["controls"]["restock_paused"]:
        if observation["balance"] < observation["reserve"]*1.1:
            state["phase"] = "cash_guard"
            return state, events
        state["controls"]["restock_paused"] = False
        event("cash_guard_released")
    history = state["history"]
    if history and (observation["deals"] < history[-1]["deals"]
                    or observation["bankruptcies"] > history[-1]["bankruptcies"]):
        rollback("counter_reset_or_bankruptcy")
        state.update(controls=copy.deepcopy(BASE), history=[], phase="collecting_baseline")
        history = state["history"]
        event("baseline_reset")
    if not history or observation["market_at"] > history[-1]["market_at"]:
        history.append(compact(observation))
    state["history"] = [h for h in history if now-h["at"] <= 86400][-60:]
    active = state["active"]
    if active:
        end = active.get("post_end")
        cutoff = end["at"] if end else now
        active["post_cohort"].update(cohort(observation.get("own_sales", []), active.get("sku"), active.get("grade"),
                                             active["started_at"], cutoff, active["id"]))
        baseline = active["baseline_end"]
        if observation["reviews"]-baseline["reviews"] >= 5 and observation["stars"] <= baseline["stars"]-settings["rating_drop"]:
            rollback("rating_guard")
            state["phase"] = "cooldown"
            return state, events
        if now-active["started_at"] >= settings["max_trial_seconds"]:
            rollback("trial_timeout_or_insufficient_evidence")
            state["phase"] = "cooldown"
            return state, events
        if (not end and now-active["started_at"] >= settings["trial_seconds"]
                and observation["deals"]-baseline["deals"] >= settings["min_new_deals"]):
            before = rates(active["baseline_start"], baseline)
            current = rates(baseline, observation)
            if (current["operating"] < before["operating"]-max(1, abs(before["operating"])*settings["max_loss"])
                    or current["deals"] < before["deals"]*.7):
                rollback("early_performance_guard")
                state["phase"] = "cooldown"
                return state, events
        if not end and now-active["started_at"] >= settings["trial_seconds"]:
            active["post_end"] = compact(observation)
            active["phase"] = "maturing"
            # Stop exposing new deals while the fixed trial cohort matures.
            state["controls"] = active["before"]
            state["last_change_at"] = now
            end = active["post_end"]
            event("trial_window_closed", experiment=active["id"], maturity_until=end["at"]+settings["maturity_seconds"])
        if not end or now-end["at"] < settings["maturity_seconds"]:
            state["phase"] = active["phase"]
            return state, events
        before_rates = rates(active["baseline_start"], baseline)
        after_rates = rates(baseline, end)
        enough = end["deals"]-baseline["deals"] >= settings["min_new_deals"]
        # Margin/throughput comparisons are observations, not causal attribution.
        gain, loss = settings["min_gain"], settings["max_loss"]
        acceptable_operating = after_rates["operating"] >= before_rates["operating"]-max(1, abs(before_rates["operating"])*loss)
        if active["kind"].startswith("quote_"):
            prior = cohort_rate(active["baseline_cohort"], active["baseline_start"]["at"], baseline["at"])
            after = cohort_rate(active["post_cohort"], baseline["at"], end["at"])
            enough = enough and min(prior["count"], after["count"]) >= settings["min_target_trades"]
            if observation.get("objective") == "seller_profit_rank_1":
                improved = after["gross_per_hour"] > 0 and after["gross_per_hour"] >= prior["gross_per_hour"]*(1+gain)
            elif active["kind"] == "quote_cut":
                improved = (after["deals_per_hour"] >= prior["deals_per_hour"]*(1+gain)
                            and after["gross_per_hour"] >= prior["gross_per_hour"]*(1-loss))
            else:
                improved = (after["gross_per_hour"] >= prior["gross_per_hour"]*(1+gain)
                            and after["deals_per_hour"] >= prior["deals_per_hour"]*(1-loss))
            evaluation = {"before": prior, "after": after}
        else:
            improved = end["stock_cost"] < baseline["stock_cost"] and after_rates["deals"] >= before_rates["deals"]*(1-loss)
            evaluation = {"before": before_rates, "after": after_rates}
        # Explicit refunds in the target cohort invalidate optimistic gross margins.
        cohort_units = {r["unit"] for r in active["post_cohort"].values()}
        refund_hit = bool(cohort_units & set(observation.get("refunded_units", [])))
        event("evaluation", experiment=active["id"], enough=enough, improved=improved,
              operating_ok=acceptable_operating, refund_in_cohort=refund_hit, comparison=evaluation,
              objective=observation.get("objective", "player_top3"))
        if enough and improved and acceptable_operating and not refund_hit:
            state["controls"] = active["candidate"]
            state["active"] = None
            state["last_change_at"] = now
            state["phase"] = "kept"
            event("keep", experiment=active["id"], comparison=evaluation)
        else:
            rollback("inconclusive" if not enough else "no_gain_or_deterioration")
            state["phase"] = "cooldown"
        return state, events
    if now-state["last_change_at"] < settings["min_change_seconds"]:
        state["phase"] = "cooldown"
        return state, events
    starts = [h for h in state["history"] if settings["baseline_seconds"] <= now-h["at"] <= settings["baseline_seconds"]+3600]
    if not starts:
        state["phase"] = "collecting_baseline"
        return state, events
    start = max(starts, key=lambda h: h["at"])
    if observation["deals"]-start["deals"] < settings["min_new_deals"]:
        state["phase"] = "waiting_for_settlements"
        return state, events
    profit_objective = observation.get("objective") == "seller_profit_rank_1"
    if ((profit_objective and observation.get("profit_rank") == 1)
            or (not profit_objective and observation.get("rank") is not None and observation["rank"] <= 3)):
        state["phase"] = "maintaining_profit_lead" if profit_objective else "maintaining_top3"
        return state, events
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    if state["day"] != day:
        state.update(day=day, experiments_today=0)
    if state["experiments_today"] >= settings["max_daily_experiments"]:
        state["phase"] = "daily_trial_limit"
        return state, events
    choices = choose(observation, state["controls"], settings)
    for choice in choices:
        choice["own_baseline_settlements"] = len(cohort(observation.get("own_sales", []), choice.get("sku"),
                                                    choice.get("grade"), start["at"], now))
        choice["own_evidence_source"] = observation.get("self_evidence", "legacy_fixture")
    event("diagnosis", rank=observation.get("rank"), gap_to_third=observation.get("gap_to_third"), candidates=choices[:5],
          buyer_surplus=observation.get("buyer_surplus"), forum_status=observation.get("forum_status"),
          leader_benchmark=observation.get("leader_benchmark"))
    for choice in choices:
        prior = cohort(observation.get("own_sales", []), choice.get("sku"), choice.get("grade"), start["at"], now)
        if choice["kind"].startswith("quote_") and len(prior) < settings["min_target_trades"]:
            continue
        identity = uuid4().hex
        state["active"] = {**choice, "id": identity, "phase": "testing", "started_at": now,
                           "before": copy.deepcopy(state["controls"]), "baseline_start": start,
                           "baseline_end": compact(observation), "baseline_cohort": prior, "post_cohort": {}}
        state["controls"] = choice["candidate"]
        state.update(phase="testing", last_change_at=now, experiments_today=state["experiments_today"]+1)
        event("start", experiment=identity, kind=choice["kind"], reason=choice["reason"],
              before=state["active"]["before"], after=choice["candidate"])
        break
    else:
        state["phase"] = "waiting_for_comparable_evidence"
        state["wait_reason"] = "no_eligible_market_candidate" if not choices else "insufficient_local_same_condition_settlements"
    return state, events


def observation(md, now):
    from r3_policy import selling_terms
    snapshot = read(md/"market-intel.json", {})
    inventory_path = md/"inventory.json"
    inv = read(inventory_path, {})
    shop = snapshot.get("competition", {}).get("own", {}).get("shops") or {}
    buyer = snapshot.get("competition", {}).get("own", {}).get("buyers") or {}
    result = {"at": now, "valid": False, "market_at": snapshot.get("observed_at", 0)}
    if (snapshot.get("round") != 3 or inv.get("round") != 3
            or not 0 <= now-result["market_at"] <= 3600 or now-inventory_path.stat().st_mtime > 120
            or any(not finite(shop.get(k)) for k in ("deals", "visits", "revenue", "stars", "reviews"))
            or shop.get("alive") is not True):
        return result
    stock = inv.get("units", [])
    try:
        local = local_settlements(md, now)
    except (OSError, ValueError, TypeError) as error:
        result["local_evidence_error"] = f"{type(error).__name__}: {error}"
        return result
    if not local["ready"] or not -10 <= local["source_age_seconds"] <= 120:
        result["local_evidence_error"] = "missing R3 anchor, conflicting receipts, or stale local log"
        return result
    cost = sum(u["cost"] for u in stock)
    rate = inv.get("storage_per_day", .01)
    reserve = max(500000, inv.get("start", 5000000)*.2) + sum(u["cost"]*rate*7 for u in stock if u.get("kind") == "goods")
    # Build unit costs once from historical pricing logs; newer signed offers carry cost directly.
    cache_path = md/"strategy-unit-costs.json"
    costs = read(cache_path)
    if costs is None:
        costs = {}
        for path in (md.parent/"logs/pricing").glob("*.json"):
            entry = read(path)
            if entry.get("unit"):
                costs[entry["unit"]] = entry["cost"]
    offers = {}
    for path in (md/"signed-offers").glob("*.json"):
        offer = read(path)
        offers.setdefault(offer["unit"], []).append(offer)
        if finite(offer.get("cost")):
            costs[offer["unit"]] = offer["cost"]
        coverage = offer.get("warranty_hours") or 0
        if coverage > 0 and now < offer["issued_at"]+coverage*3600+600:
            reserve += offer["price"]
    missing_costs = {r["unit"] for r in local["rows"] if r["unit"] not in costs}
    if missing_costs:
        # Older resident sellers did not include cost in their signed-offer metadata.
        for path in (md.parent/"logs/pricing").glob("*.json"):
            entry = read(path)
            if entry.get("unit") in missing_costs and finite(entry.get("cost")):
                costs[entry["unit"]] = entry["cost"]
    write(cache_path, costs)
    own_sales, refunded = [], []
    missing_metadata = []
    for sale in local["rows"]:
        unit, amount = sale["unit"], sale["amount"]
        candidates_for_unit = offers.get(unit, [])
        matching = [o for o in candidates_for_unit if o.get("price") == amount
                    and finite(o.get("issued_at")) and o["issued_at"] <= sale["ts"]+1]
        skus = {o.get("sku") for o in candidates_for_unit if isinstance(o.get("sku"), str)}
        identities = {o.get("strategy_experiment") for o in matching}
        warranties = {o.get("warranty_hours") or 0 for o in matching}
        # The cost and SKU must be backed by this seller's own unit metadata.
        if len(skus) != 1 or unit not in costs:
            missing_metadata.append(sale["ledger"])
            continue
        identity = next(iter(identities)) if len(identities) == 1 else None
        coverage = next(iter(warranties)) if len(warranties) == 1 else None
        own_sales.append({**sale, "sku": next(iter(skus)), "cost": costs[unit], "experiment": identity,
                          "warranty_hours": coverage,
                          "receipt_attribution": "matched" if len(identities) == 1 else "ambiguous_or_missing"})
    # Public settlements never populate own_sales. Refunds are adverse corroborating signals only.
    for row in snapshot.get("rows", []):
        data = row.get("data") or {}
        unit = data.get("unit")
        if row.get("round") != 3 or unit not in offers:
            continue
        if row.get("kind") == "refund":
            refunded.append(unit)
    consumer = read(md/"strategy-consumer.json", {})
    ready = consumer.get("protocol") == 1 and 0 <= now-consumer.get("heartbeat_at", 0) <= 1200
    purchase = read(md/"purchase-intel.json", {})
    candidates = []
    if purchase.get("status") == "ok" and 0 <= now-purchase.get("source_observed_at", 0) <= 3600:
        comparisons = {c["unit"]: c for c in purchase.get("summary", {}).get("own_stock_comparison", [])}
        products = {p["sku"]: p for p in purchase.get("summary", {}).get("products", [])}
        catalog = read(md/"catalog.json", {})
        from strategy_policy import effective
        runtime = effective(md, now)
        for unit in stock:
            comparison = comparisons.get(unit["unit"], {})
            prices = comparison.get("same_grade_zero_warranty_prices") or {}
            if not prices:
                # A new stock unit can use the same SKU/grade market comparison.
                conditions = products.get(unit["sku"], {}).get("conditions", [])
                prices = next((g["prices"] for g in conditions
                               if g.get("grade") == unit.get("grade") and g.get("warranty_hours") == 0), {})
            terms = selling_terms(unit, catalog, now, signals=snapshot.get("skus", {}).get(unit["sku"], {}), runtime=runtime)
            if unit.get("kind") == "goods" and not terms["below_cost_allowed"]:
                candidates.append({"sku": unit["sku"], "grade": unit["grade"], "kind": "goods", "floor": terms["floor"],
                                   "quote": terms["quote"], "list_price": unit["list_price"],
                                   "median": prices.get("median"), "count": prices.get("count", 0)})
    try:
        from leader_monitor import enrich
        from strategy_policy import effective
        candidates, leader_benchmark = enrich(md, candidates, stock, read(md/"catalog.json", {}),
                                              snapshot, effective(md, now), now)
    except (OSError, ValueError, KeyError, TypeError):
        leader_benchmark = {"status": "unavailable"}
    result.update(valid=True, balance=inv["balance"], reserve=reserve, stock_cost=cost,
                  objective="seller_profit_rank_1", profit_rank=leader_benchmark.get("profit_rank"),
                  profit_gap_to_first=leader_benchmark.get("profit_gap_to_first"),
                  leader_benchmark=leader_benchmark,
                  self_evidence="local_acp_v1",
                  local_counters=[{k: r[k] for k in ("ledger", "ts", "amount")} for r in local["rows"]],
                  local_evidence={"confirmed": len(local["rows"]), "cost_attributed": len(own_sales),
                                  "missing_metadata_ledgers": missing_metadata, "offset": local["offset"],
                                  "conflicting_ledgers": local["conflicting_ledgers"]},
                  deals=len(local["rows"]), revenue=sum(r["amount"] for r in local["rows"]),
                  board_deals=shop["deals"], board_revenue=shop["revenue"],
                  operating_value=inv["balance"]+cost-inv.get("start", 5000000)-inv.get("injected", 0),
                  bankruptcies=inv.get("bankruptcies", 0), rank=snapshot.get("competition", {}).get("rank"),
                  gap_to_third=snapshot.get("competition", {}).get("gap_to_third"),
                  buyer_surplus=buyer.get("surplus"), forum_status=read(md/"forum-intel.json", {}).get("status"),
                  price_consumer_ready=ready, price_candidates=candidates, own_sales=own_sales, refunded_units=refunded,
                  aged_units=sum(now-u.get("supplied_at", now) >= 86400 for u in stock), stock=stock,
                  **{k: shop[k] for k in ("visits", "stars", "reviews")})
    return result


def acquire(path):
    """An OS lock is released even if the controller process crashes."""
    handle = path.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def run(md, now=None, force=False):
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    lock = md/"strategy-controller.lock"
    handle = acquire(lock)
    if handle is None:
        return {"status": "busy"}
    try:
        settings = config()
        state = read(md/"strategy-state.json", initial())
        obs = observation(md, now) if settings["enabled"] else {"at": now, "valid": False}
        urgent = not settings["enabled"] or not obs.get("valid") or obs.get("balance", 0) <= obs.get("reserve", 0)
        urgent = urgent or (obs.get("self_evidence") == "local_acp_v1" and state.get("self_evidence") != "local_acp_v1")
        urgent = urgent or (obs.get("objective") is not None and state.get("objective") != obs["objective"])
        active = state.get("active")
        if active and obs.get("valid"):
            baseline = active["baseline_end"]
            urgent = urgent or (obs["reviews"]-baseline["reviews"] >= 5
                                and obs["stars"] <= baseline["stars"]-settings["rating_drop"])
            urgent = urgent or obs["deals"] < baseline["deals"] or obs["bankruptcies"] > baseline["bankruptcies"]
        if not force and not urgent and 0 <= now-state.get("checked_at", -86400) < settings["check_interval_seconds"]:
            return {"status": state["phase"], "next_check_at": state["checked_at"]+settings["check_interval_seconds"]}
        state, events = transition(state, obs, settings)
        state.update(checked_at=now, version=state["version"]+1)
        published = validate(state["controls"])
        if not obs.get("valid") and settings["enabled"]:
            published = copy.deepcopy(BASE)
            published["restock_paused"] = True
        active = state.get("active")
        policy = {"schema": 1, "round": 3, "version": state["version"], "published_at": now,
                  "valid_until": now+settings["lease_seconds"], "controls": published,
                  "experiment": active["id"] if active and active["phase"] == "testing" else None}
        journal = {"at": now, "phase": state["phase"], "observation": compact(obs),
                   "wait_reason": state.get("wait_reason"),
                   "events": events, "policy": policy, "active_experiment": active, "model_calls": 0}
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(md.parent/"logs/strategy-controller"/(stamp+"-"+uuid4().hex[:8]+".json"), journal)
        write(md/"strategy-state.json", state)
        write(md/"strategy-policy.json", policy)
        # Notification failures must never interrupt trading or policy guards.
        try:
            from improvement_notices import publish
            publish(md, obs, events)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {"status": state["phase"], "version": state["version"], "events": [e["event"] for e in events],
                "wait_reason": state.get("wait_reason"),
                "price_consumer_ready": obs.get("price_consumer_ready", False), "model_calls": 0}
    finally:
        release(handle)


if __name__ == "__main__":
    print(json.dumps(run(ROOT/".market", force=True), ensure_ascii=False))
