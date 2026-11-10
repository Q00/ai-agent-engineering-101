"""Small A-grade warranty/control comparison with full liability reservation."""

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import time
from uuid import uuid4

from market_state import read, write, task_file

ROOT = Path(__file__).resolve().parent


def liability(md, now=None):
    md = Path(md)
    now = time.time() if now is None else now
    local = read(md / "local-settlements.json", {}).get("records", {})
    settled = {r.get("unit"): r.get("ts", 0) for r in local.values() if r.get("round") == 3}
    total, signed = 0, set()
    for source in (md / "signed-offers").glob("*.json"):
        offer = read(source)
        task = offer.get("task")
        if task:
            signed.add(task)
        hours = offer.get("warranty_hours") or 0
        if hours <= 0:
            continue
        status = read(task_file(md, "checkout-events", task), {}).get("status") if task else None
        if status in ("refused", "canceled", "expired"):
            continue
        start = max(offer["issued_at"], settled.get(offer["unit"], 0))
        if status in ("processing", "uncertain") or now < start+hours*3600+600:
            total += offer["price"]
    state = read(md / "warranty-trial.json", {})
    total += sum(a["quote"] for task, a in state.get("assignments", {}).items()
                 if a.get("hours") and task not in signed and now < a["expires_at"])
    return total


def config():
    c = read(ROOT / "warranty_trial.json")
    if (type(c["enabled"]) is not bool or c["hours"] != 2 or not 1 <= c["max_treatment_per_day"] <= 5
            or not 1 <= c["max_control_per_day"] <= 5 or not 0 < c["max_exposure"] <= 1000000
            or not 0 < c["cash_exposure_ratio"] <= .1 or c["refund_stress_ratio"] < .2
            or c["refund_stress_ratio"] >= .5 or c["quote_lease_seconds"] != 600 or c["maturity_seconds"] < 21600):
        raise ValueError("Warranty trial exceeds authorized bounds")
    return c


def six_hour_settings(path=None):
    """The A-grade six-hour warranty's settings when switched on and inside its bounds; anything else is off."""
    c = read(ROOT / "a_grade_warranty.json" if path is None else path, {})
    try:
        if (c.get("enabled") is not True or c["hours"] != 6 or not 0 < c["max_exposure"] <= 90000000 or not 0 < c["cash_exposure_ratio"] <= .7
                or not 0 <= c["refund_ratio"] < .2 or c["quote_lease_seconds"] != 600):
            return None
    except (AttributeError, KeyError, TypeError):
        return None
    return c


def six_hour(md, path, state, task, unit, terms, c, ordinary, now):
    """Every A-grade unit, not a comparison: enrolled stays False so the trial's evaluation never counts it, and liability() still reserves it."""
    inventory = read(md / "inventory.json", {})
    if inventory.get("round") != 3 or not -10 <= now-(md / "inventory.json").stat().st_mtime <= 120:
        return ordinary
    cost = unit["cost"]
    storage = cost * .01 * max(0, now-unit.get("supplied_at", now))/86400
    floor = max(terms["floor"], math.floor((cost+storage)/(1-c["refund_ratio"]))+1)
    if terms["quote"] < floor:
        return ordinary
    reserved = liability(md, now)
    balance = inventory.get("balance", 0)
    stock_storage = sum(u.get("cost", 0)*.01*7 for u in inventory.get("units", []) if u.get("kind") == "goods")
    reserve = max(1000000, inventory.get("start", 5000000)*.2)+stock_storage
    if (reserved+terms["quote"] > min(c["max_exposure"], balance*c["cash_exposure_ratio"])
            or balance-reserved-terms["quote"] < reserve):
        return ordinary
    record = {"hours": c["hours"], "floor": floor, "enrolled": False, "group": "six_hour",
              "day": datetime.fromtimestamp(now, timezone.utc).date().isoformat(), "unit": unit["unit"], "sku": unit["sku"],
              "cost": cost, "quote": terms["quote"], "at": now, "expires_at": now+c["quote_lease_seconds"]}
    entries = {k: v for k, v in state["assignments"].items() if now-v["at"] <= 7*86400}
    entries[task] = record
    state["assignments"] = entries
    write(path, state)
    return record


def assignment(md, task, unit, terms, now=None):
    from strategy_controller import acquire, release
    md = Path(md)
    now = time.time() if now is None else now
    c = config()
    path = md / "warranty-trial.json"
    handle = acquire(md / "warranty-trial.lock")
    if handle is None:
        raise ValueError("Warranty allocation busy; retry before changing promised terms")
    try:
        state = read(path, {"assignments": {}, "paused": False})
        entries = state["assignments"]
        if task in entries:
            if entries[task].get("hours") and now >= entries[task]["expires_at"]:
                raise ValueError("Warranty quote expired; request a new task")
            return entries[task]
        ordinary = {"hours": 0, "floor": terms["floor"], "enrolled": False}
        six = six_hour_settings()
        if (six and unit.get("kind") == "goods" and unit.get("grade") == "A" and unit.get("cert")
                and not terms.get("below_cost_allowed")):
            return six_hour(md, path, state, task, unit, terms, six, ordinary, now)
        if (not c["enabled"] or state.get("paused") or unit.get("kind") != "goods" or unit.get("grade") != "A"
                or not unit.get("cert") or terms.get("below_cost_allowed")):
            return ordinary
        inventory = read(md / "inventory.json", {})
        if inventory.get("round") != 3 or not -10 <= now-(md / "inventory.json").stat().st_mtime <= 120:
            return ordinary
        controls = read(md / "strategy-state.json", {})
        if controls.get("active") or terms.get("strategy_experiment"):
            return ordinary  # Do not start a second experiment during a price trial.
        cost = unit["cost"]
        storage = cost * .01 * max(0, now-unit.get("supplied_at", now))/86400
        stressed_floor = math.floor((cost+storage)/(1-c["refund_stress_ratio"]))+1
        if terms["quote"] < stressed_floor:
            return ordinary
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        group = "warranty" if int(hashlib.sha256(task.encode()).hexdigest(), 16) % 2 == 0 else "control"
        count = sum(a.get("day") == day and a.get("group") == group for a in entries.values())
        if count >= c["max_treatment_per_day" if group == "warranty" else "max_control_per_day"]:
            return ordinary
        reserved = liability(md, now)
        balance = inventory.get("balance", 0)
        stock_storage = sum(u.get("cost", 0)*.01*7 for u in inventory.get("units", []) if u.get("kind") == "goods")
        reserve = max(1000000, inventory.get("start", 5000000)*.2)+stock_storage
        if group == "warranty" and (reserved+terms["quote"] > min(c["max_exposure"], balance*c["cash_exposure_ratio"])
                                    or balance-reserved-terms["quote"] < reserve):
            return ordinary
        record = {"hours": c["hours"] if group == "warranty" else 0,
                  "floor": max(terms["floor"], stressed_floor) if group == "warranty" else terms["floor"],
                  "enrolled": True, "group": group, "day": day, "unit": unit["unit"], "sku": unit["sku"],
                  "cost": cost, "quote": terms["quote"], "at": now, "expires_at": now+c["quote_lease_seconds"]}
        entries = {k: v for k, v in entries.items() if now-v["at"] <= 7*86400}
        entries[task] = record
        state["assignments"] = entries
        write(path, state)
        return record
    finally:
        release(handle)


def refunds_in(rows):
    """{unit: refunded amount} from public rows. The market reports refunds as `refunded` on broken rows; a refund row without an amount means None, the full price."""
    found = {}
    for row in rows:
        data = row.get("data") or {}
        unit = data.get("unit")
        if not unit or row.get("round") != 3 or row.get("kind") not in ("broken", "refund"):
            continue
        amount = data.get("refunded")
        if type(amount) in (int, float) and amount > 0:
            found[unit] = max(amount, found.get(unit) or 0)
        elif row.get("kind") == "refund":
            found.setdefault(unit, None)
    return found


def stratum(catalog, sku):
    """Top catalogue category. Two groups rarely sell the same SKU at this volume; margins are compared inside this wider group."""
    return str((catalog.get(sku) or {}).get("category") or "unknown").split(">")[0]


def evaluate(md, now=None):
    from strategy_controller import acquire, release
    md = Path(md)
    now = time.time() if now is None else now
    prior = read(md / "warranty-evaluation.json", {})
    if 0 <= now-prior.get("at", -86400) < 1800:
        return prior
    maturity = config()["maturity_seconds"]
    handle = acquire(md / "warranty-trial.lock")
    if handle is None:
        return {"status": "busy"}
    try:
        state = read(md / "warranty-trial.json", {"assignments": {}, "paused": False})
        index = read(md / "local-settlements.json", {})
        snapshot = read(md / "market-intel.json", {})
        catalog = read(md / "catalog.json", {})
        valid = (index.get("active_round") == 3 and index.get("r3_anchor_found") and not index.get("conflicting_ledgers")
                 and 0 <= now-index.get("collected_at", 0) <= 3600 and snapshot.get("round") == 3
                 and 0 <= now-snapshot.get("observed_at", 0) <= 3600)
        groups = {"warranty": {}, "control": {}}
        offers = {}
        for source in (md / "signed-offers").glob("*.json"):
            offer = read(source)
            a = state.get("assignments", {}).get(offer.get("task"), {})
            if a.get("enrolled"):
                offers.setdefault(offer["unit"], []).append((offer, a))
        # Public rows are a bounded recent sample, so refunds found are kept here, and the board's complete count backs them up below.
        own_units = {r.get("unit") for r in index.get("records", {}).values()} | set(offers)
        refunds = {**prior.get("refunds_seen", {}), **{unit: amount for unit, amount in refunds_in(snapshot.get("rows", []) if valid else []).items() if unit in own_units}}
        official = ((snapshot.get("competition") or {}).get("own") or {}).get("shops", {}).get("refunds") if valid else None
        official = official if type(official) in (int, float) else None
        for sale in index.get("records", {}).values() if valid else []:
            matches = [(o, a) for o, a in offers.get(sale.get("unit"), []) if o.get("price") == sale.get("amount")]
            groups_found = {a["group"] for _, a in matches}
            if len(groups_found) != 1 or sale.get("source") != "local_acp_settled" or sale.get("round") != 3 or now-sale["ts"] < maturity or sale["amount"] <= 0:
                continue
            a = matches[0][1]
            refunded = 0
            if sale["unit"] in refunds:
                refunded = sale["amount"] if refunds[sale["unit"]] is None else min(refunds[sale["unit"]], sale["amount"])
            # Margin on the price, since one category holds prices far apart. Compare only strata represented in both groups below.
            groups[a["group"]].setdefault(stratum(catalog, a["sku"]), []).append((sale["amount"]-refunded-a["cost"])/sale["amount"])
        shared = set(groups["warranty"]) & set(groups["control"])
        treated = [v for key in shared for v in groups["warranty"][key]]
        controls = [v for key in shared for v in groups["control"][key]]
        warranted_units = {unit for unit, values in offers.items() if any(a.get("hours") for _, a in values)}
        refund_seen = bool(warranted_units & set(refunds))
        # Only trial offers carry a warranty, and a refund needs one, so any refund on the board's complete count is ours to stop for.
        refund_counted = bool(warranted_units) and bool(official)
        worse = False
        if len(treated) >= 3 and len(controls) >= 3:
            control_mean = sum(controls)/len(controls)
            worse = sum(treated)/len(treated) < control_mean-abs(control_mean)*.1
        if refund_seen or refund_counted or worse:
            state["paused"] = True
            state["pause_reason"] = "observed_refund_or_matched_margin_deterioration"
            state["pause_evidence"] = {"refund_seen": refund_seen, "refund_counted": refund_counted, "margin_worse": worse, "at": now}
            write(md / "warranty-trial.json", state)
        result = {"at": now, "status": "paused" if state.get("paused") else "collecting_mature_comparisons",
                  "valid_sources": bool(valid), "comparison_unit": "top_category_margin_on_price", "maturity_seconds": maturity,
                  "warranty_mature_total": sum(map(len, groups["warranty"].values())), "control_mature_total": sum(map(len, groups["control"].values())),
                  "warranty_mature_count": len(treated), "control_mature_count": len(controls), "matched_strata": sorted(shared),
                  "refunds_seen": refunds, "official_refunds": official, "refunds_without_a_row": max(0, (official or 0)-len(refunds)),
                  "skipped_ledger_ranges": len(snapshot.get("skipped_seq_ranges") or []), "model_calls": 0,
                  "note": "Partial refund observations; no causal gain or complete failure-rate claim. It can only pause the trial, never widen it. Existing warranties remain honored."}
        write(md / "warranty-evaluation.json", result)
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(md.parent / "logs/warranty-trial" / (stamp+"-"+uuid4().hex[:8]+".json"), result)
        return result
    finally:
        release(handle)
