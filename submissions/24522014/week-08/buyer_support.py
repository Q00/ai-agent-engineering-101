"""Sparse, relevant buyer research for seller profit. No model/network calls here."""

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import time
from collections import Counter

from market_state import read, write
from r3_selection import select, positive
from settlement_memory import payment_outcomes
from purchase_monitor import settlement_price

ROOT = Path(__file__).resolve().parent
MODEL_ALLOWED = ("reserved", "gate_disabled")


def config():
    value = read(ROOT / "buyer_support.json")
    balanced = value.get("mode") == "balanced"
    if (type(value["enabled"]) is not bool or not 1 <= value["max_attempts_per_utc_day"] <= (4320 if balanced else 12)
            or not (20 if balanced else 1800) <= value["min_attempt_interval_seconds"] <= 86400
            or not (300 if balanced else 3600) <= value["pair_cooldown_seconds"] <= 86400
            or not 60 <= value["reference_max_age_seconds"] <= 3600
            or value["min_catalogue_value"] < (1 if balanced else 10000) or value["sufficient_public_samples"] < 3
            or not 0 <= value["alternative_tolerance_ratio"] <= .02
            or not 0 <= value["reference_premium_ratio"] <= .02
            or not 1 <= value["max_turns"] <= 3):
        raise ValueError("Invalid seller-support buyer bounds")
    if balanced and (not 1 <= value.get("max_model_orders_per_day", 0) <= 24
                     or not 1 <= value.get("max_model_orders_per_hour", 0) <= 2):
        raise ValueError("Balanced model negotiation quota exceeded")
    return value


def age(row, now):
    try:
        stamp = datetime.fromisoformat(row["timestamp"])
        return now-stamp.timestamp() if stamp.tzinfo else math.inf
    except (ValueError, TypeError, KeyError):
        return math.inf


def initial():
    return {"schema": 1, "round": 3, "attempts": []}


def availability(state, order_id, settings, now):
    if state.get("schema") != 1 or state.get("round") != 3:
        raise ValueError("Buyer support state must be R3 schema 1")
    if not settings["enabled"]:
        return "disabled"
    if not isinstance(order_id, str) or not order_id:
        return "missing_order_identity"
    attempts = state["attempts"]
    if any(r["order"] == order_id for r in attempts):
        return "duplicate_order"
    day = datetime.fromtimestamp(now, timezone.utc).date()
    if sum(datetime.fromtimestamp(r["at"], timezone.utc).date() == day for r in attempts) >= settings["max_attempts_per_utc_day"]:
        return "daily_attempt_limit"
    if attempts and now-max(r["at"] for r in attempts) < settings["min_attempt_interval_seconds"]:
        return "attempt_cooldown"
    return "available"


def context(md, catalog, settings, now):
    targets = {}
    inventory_path = md / "inventory.json"
    inv = read(inventory_path, {})
    if inv.get("round") == 3 and -10 <= now-inventory_path.stat().st_mtime <= 120:
        targets.update({u["sku"]: "own_stock" for u in inv.get("units", [])})
    sales = read(md / "r3-restock-memory.json", {})
    if sales.get("round") == 3:
        for sku, count in sorted(sales.get("sales", {}).items(), key=lambda p: -p[1])[:10]:
            if count > 0:
                targets.setdefault(sku, "own_sale_history")
    leader = read(md / "leader-intel.json", {})
    rivals, urls = set(), set()
    if leader.get("round") == 3 and leader.get("basis") == "shop_profit" and leader.get("status") == "ok" and 0 <= now-leader.get("observed_at", 0) <= 3600:
        for row in leader.get("leaders", [])[:3]:
            if row.get("login") == "kcwarni":
                continue
            shop = row.get("shop") or {}
            if shop.get("name"):
                rivals.add(shop["name"].casefold())
            if shop.get("url"):
                urls.add(shop["url"].rstrip("/"))
            for sku, count in row.get("sampled_sales", {}).get("skus", [])[:5]:
                if count >= 3:
                    targets.setdefault(sku, "profit_leader_sales")
    targets = {sku: reason for sku, reason in targets.items() if catalog.get(sku, {}).get("kind") == "goods"
               and positive(catalog[sku].get("list_price")) and catalog[sku]["list_price"] >= settings["min_catalogue_value"]}
    return {"targets": targets, "rival_names": rivals, "rival_urls": urls}


def choose(shops, memory, catalog, budget, policy, portfolio, public_rows, ctx, state, settings, now, kind="goods"):
    memory = [r for r in memory if r.get("round") == 3]
    # Keep durable mismatch/outage evidence; only positive price references expire.
    recent_memory = [dict(r, verification="stale_price_reference") if r.get("verification") == "market_settled"
                     and not 0 <= age(r, now) <= settings["reference_max_age_seconds"] else r for r in memory]
    public = {r["seq"]: r for r in public_rows if r.get("round") == 3 and r.get("kind") == "settle"
              and type(r.get("seq")) is int and type(r.get("ts")) in (int, float)
              and 0 <= now-r["ts"] <= settings["reference_max_age_seconds"]
              and settlement_price(r.get("data") or {}) is not None
              and type((r.get("data") or {}).get("warranty_hours") or 0) in (int, float)
              and math.isfinite((r.get("data") or {}).get("warranty_hours") or 0)
              and ((r.get("data") or {}).get("warranty_hours") or 0) >= 0
              and (r.get("data") or {}).get("grade") in policy["grades"]
              and (r.get("data") or {}).get("claim_honest") is not False}
    _, _, _, _, evidence = select(shops, recent_memory, catalog, budget, policy=policy, portfolio=portfolio,
                                  kind=kind, public_rows=list(public.values()), now=now)
    by_name = {s["name"]: s for s in shops}
    eligible = []
    balanced = settings.get("mode") == "balanced"
    for item in evidence:
        shop = by_name[item["shop"]]
        sku = item["sku"]
        if balanced and kind != "goods":
            item["negotiation_cap"] = min(item["negotiation_cap"], int(item["catalogue_value"]*(1-policy["minimum_surplus_ratio"])))
            item["reference_within_cap"] = item["past_settled_price"] is None or item["past_settled_price"] <= item["negotiation_cap"]
            item["estimated_surplus"] = item["catalogue_value"]-(item["past_settled_price"] or item["negotiation_cap"])
        item["support_eligible"] = False
        references = [r for r in public.values() if r["data"].get("shop") == shop["url"] and r["data"].get("item") == sku]
        latest = max(references, key=lambda r: r["ts"], default=None)
        item["reference_condition"] = ({"grade": latest["data"]["grade"],
                                        "warranty_hours": latest["data"].get("warranty_hours") or 0}
                                       if latest and item["reference_source"] == "public_r3_settlement" else None)
        reason = None
        owner = shop.get("owner") or ""
        owner = owner.get("login", "") if isinstance(owner, dict) else str(owner)
        if shop["name"].casefold() == "kcwarni" or owner.casefold() == "kcwarni":
            reason = "same_owner_shop"
        elif not balanced and sku not in ctx["targets"]:
            reason = "not_relevant_to_seller"
        elif any(r["shop"].casefold() == shop["name"].casefold() and r["sku"] == sku
                 and now-r["at"] < settings["pair_cooldown_seconds"] for r in state["attempts"]):
            reason = "pair_attempt_cooldown"
        elif not balanced and any(r.get("shop_name") == shop["name"] and r.get("item") == sku and r.get("verification") == "market_settled"
                 and r.get("outcome") == "purchased" and 0 <= age(r, now) <= settings["pair_cooldown_seconds"] for r in memory):
            reason = "recent_own_information_sufficient"
        elif not balanced and max(Counter((r["data"].get("grade"), r["data"].get("warranty_hours") or 0) for r in public.values()
                         if r["data"].get("shop") == shop["url"] and r["data"].get("item") == sku
                         and (r["data"].get("warranty_hours") or 0) >= item["warranty_required"]).values(), default=0) >= settings["sufficient_public_samples"]:
            reason = "public_information_sufficient"
        elif item["risk"]["recent_transport_errors"] >= 2:
            reason = "repeated_transport_failure"
        elif item["attributed_faults"] and not item["warranty_required"]:
            reason = "known_fault_without_enforced_coverage"
        elif not item["reference_within_cap"]:
            reason = "known_price_above_value_cap"
        elif item["reference_condition"] and item["reference_condition"]["warranty_hours"] < item["warranty_required"]:
            reason = "reference_coverage_below_required_terms"
        item["support_skip_reason"] = reason
        if reason:
            continue
        reference = item["past_settled_price"]
        if positive(reference):
            item["negotiation_cap"] = min(item["negotiation_cap"], math.floor(reference*(1+settings["reference_premium_ratio"])))
        item.update(support_eligible=True, research_relevance=ctx["targets"].get(sku, "buyer_surplus_opportunity"),
                    rival_at_selection=shop["name"].casefold() in ctx["rival_names"] or shop["url"].rstrip("/") in ctx["rival_urls"])
        eligible.append(item)
    if not eligible:
        return None, None, budget, "No new, relevant, value-preserving seller research opportunity.", evidence
    best = min(eligible, key=lambda e: (bool(e["attributed_faults"]), -e["estimated_surplus"], e["shop"], e["sku"]))
    # Rival avoidance applies only to genuinely comparable observed conditions.
    if best["rival_at_selection"] and positive(best["past_settled_price"]):
        alternatives = [e for e in eligible if not e["rival_at_selection"] and e["sku"] == best["sku"]
                        and e["warranty_required"] == best["warranty_required"]
                        and e["reference_condition"] is not None and e["reference_condition"] == best["reference_condition"]
                        and bool(e["attributed_faults"]) == bool(best["attributed_faults"])
                        and positive(e["past_settled_price"]) and e["past_settled_price"] <=
                        best["past_settled_price"] + best["catalogue_value"]*settings["alternative_tolerance_ratio"]]
        if alternatives:
            best = min(alternatives, key=lambda e: (e["past_settled_price"], e["shop"]))
            best["alternative_selected"] = True
    reason = f"Seller-profit research: {best['shop']}/{best['sku']}; {best['research_relevance']}; cap={best['negotiation_cap']}."
    return by_name[best["shop"]], best["sku"], best["negotiation_cap"], reason, evidence


def reserve(md, order_id, shop, sku, cap, rival, settings, now):
    from strategy_controller import acquire, release
    handle = acquire(md / "buyer-support.lock")
    if handle is None:
        return "busy"
    try:
        path = md / "buyer-support-state.json"
        state = read(path, initial())
        status = availability(state, order_id, settings, now)
        if status != "available":
            return status
        if any(r["shop"].casefold() == shop["name"].casefold() and r["sku"] == sku
               and now-r["at"] < settings["pair_cooldown_seconds"] for r in state["attempts"]):
            return "pair_attempt_cooldown"
        state["attempts"] = [r for r in state["attempts"] if 0 <= now-r["at"] <= 7*86400][-4999:]
        state["attempts"].append({"at": now, "order": order_id, "shop": shop["name"], "url": shop["url"],
                                   "sku": sku, "cap": cap, "rival_at_selection": rival})
        write(path, state)
        return "reserved"
    finally:
        release(handle)


def prepare(md, shops, memory, catalog, budget, policy, portfolio, snapshot, order, now=None):
    md = Path(md)
    now = time.time() if now is None else now
    settings = config()
    state = read(md / "buyer-support-state.json", initial())
    status = availability(state, order.get("order"), settings, now)
    balanced = settings.get("mode") == "balanced"
    if status != "available" or (not balanced and order.get("kind") != "goods"):
        status = status if status != "available" else "no_relevant_goods_order"
        return None, None, budget, status, [], {"status": status, "model_calls": 0}
    ctx = context(md, catalog, settings, now)
    if not balanced and not set(order.get("acceptable") or []) & ctx["targets"].keys():
        return None, None, budget, "no_relevant_seller_sku", [], {"status": "no_relevant_seller_sku", "model_calls": 0}
    rows = snapshot.get("rows", []) if snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600 else []
    if portfolio is None:
        from buyer_portfolio import reconcile
        portfolio = reconcile(md, rows, now)
    shop, sku, cap, reason, evidence = choose(shops, memory, catalog, budget, policy, portfolio, rows, ctx, state, settings, now, kind=order.get("kind"))
    status = "no_useful_research"
    if shop:
        selected = next(e for e in evidence if e["shop"] == shop["name"] and e["sku"] == sku)
        status = reserve(md, order.get("order"), shop, sku, cap, selected["rival_at_selection"], settings, now)
        if status != "reserved":
            shop, sku, reason = None, None, status
    return shop, sku, cap, reason, evidence, {"status": status, "purpose": "balanced_player_score" if balanced else "seller_profit_support",
               "fast_rules": balanced,
               "max_turns": settings["max_turns"], "funding": "market_order_budget_not_seller_cash"}


def model_gate_enabled():
    """Only an explicit false turns the gate off; a missing or unreadable setting keeps it."""
    try:
        value = json.loads((ROOT / "buyer_support.json").read_text(encoding="utf-8")).get("model_gate_enabled")
    except (OSError, ValueError, AttributeError):
        return True
    return value is not False


def note_ungated_model_order(md, order_id, now):
    """Keep usage measurable while the gate is off. Separate from model_orders, so turning the gate back on
    does not count this period against its limits. Bookkeeping must never stop the model call."""
    try:
        from strategy_controller import acquire, release
        handle = acquire(md / "buyer-support.lock")
        if handle is None:
            return
        try:
            path = md / "buyer-support-state.json"
            state = read(path, initial())
            rows = [r for r in state.get("ungated_model_orders", []) if 0 <= now-r["at"] <= 86400*2]
            rows.append({"at": now, "order": order_id})
            state["ungated_model_orders"] = rows
            write(path, state)
        finally:
            release(handle)
    except Exception:
        return


def model_reservation(md, order_id, now=None):
    """Return 'reserved' or 'gate_disabled', or the specific reason no model negotiation is available."""
    from strategy_controller import acquire, release
    md = Path(md)
    now = time.time() if now is None else now
    if not model_gate_enabled():
        note_ungated_model_order(md, order_id, now)
        return "gate_disabled"
    settings = config()
    handle = acquire(md / "buyer-support.lock")
    if handle is None:
        return "model_gate_lock_busy"
    try:
        path = md / "buyer-support-state.json"
        state = read(path, initial())
        rows = [r for r in state.get("model_orders", []) if 0 <= now-r["at"] <= 86400*2]
        if any(r["order"] == order_id for r in rows):
            return "model_order_already_reserved"
        day = datetime.fromtimestamp(now, timezone.utc).date()
        if sum(datetime.fromtimestamp(r["at"], timezone.utc).date() == day for r in rows) >= settings["max_model_orders_per_day"]:
            return "model_quota_day"
        if sum(now-r["at"] < 3600 for r in rows) >= settings["max_model_orders_per_hour"]:
            return "model_quota_hour"
        rows.append({"at": now, "order": order_id})
        state["model_orders"] = rows
        write(path, state)
        return "reserved"
    finally:
        release(handle)


def reserve_model(md, order_id, now=None):
    return model_reservation(md, order_id, now) in MODEL_ALLOWED


def report(md):
    md = Path(md)
    attempts = read(md / "buyer-support-state.json", initial())["attempts"]
    payments = payment_outcomes(md / "payments.jsonl")
    paid, seen = [], set()
    for attempt in attempts:
        payment = payments.get(attempt["order"], {})
        ledger = payment.get("ledger")
        if (payment.get("paid") is True and type(ledger) is int and ledger > 0 and ledger not in seen
                and payment.get("shop") == attempt["url"] and payment.get("item") == attempt["sku"]
                and positive(payment.get("price")) and payment["price"] <= attempt["cap"]):
            seen.add(ledger)
            paid.append({**attempt, "paid": payment["price"], "ledger": ledger})
    return {"attempts": len(attempts), "confirmed_paid_deals": len(paid),
            "buyer_paid_to_other_shops": sum(r["paid"] for r in paid),
            "buyer_paid_to_profit_leaders_at_selection": sum(r["paid"] for r in paid if r["rival_at_selection"]),
            "funding": "market_order_budget_not_seller_cash",
            "seller_profit_effect": "not_causally_verified", "model_calls_for_report": 0}


def research_rows(md, now=None):
    """External price evidence only; never count these as our seller's sales."""
    md = Path(md)
    now = time.time() if now is None else now
    state = read(md / "buyer-support-state.json", initial())
    if state.get("round") != 3 or state.get("schema") != 1:
        raise ValueError("Research price memory must belong to Round 3")
    attempts = state["attempts"]
    payments = payment_outcomes(md / "payments.jsonl")
    rows = {}
    for attempt in attempts:
        payment = payments.get(attempt["order"], {})
        ledger = payment.get("ledger")
        if payment.get("paid") is not True or type(ledger) is not int or ledger <= 0:
            continue
        deal = read(md / "buyer-deals" / f"{ledger}.json", {})
        if (deal.get("order") != attempt["order"] or deal.get("shop") != attempt["url"] or deal.get("sku") != attempt["sku"]
                or payment.get("shop") != deal.get("shop") or payment.get("item") != deal.get("sku")
                or payment.get("price") != deal.get("price") or not positive(deal.get("price")) or deal["price"] > attempt["cap"]
                or deal.get("grade") not in ("A", "B") or deal.get("fault") or deal.get("refund_confirmed")
                or not 0 <= now-deal.get("purchased_at", 0) <= 3600):
            continue
        rows[ledger] = {"round": 3, "kind": "settle", "seq": ledger, "ts": deal["purchased_at"],
             "origin": "local_paid_buyer_research", "time_basis": "local_post_payment_review",
             "data": {"shop": deal["shop"], "item": deal["sku"], "amount": deal["price"], "grade": deal["grade"],
                      "warranty_hours": deal.get("warranty_hours") or 0, "unit": deal.get("unit")}}
    return list(rows.values())


def observe(md, now=None):
    md = Path(md)
    now = time.time() if now is None else now
    previous = read(md / "buyer-support-summary.json", {})
    if 0 <= now-previous.get("at", -86400) < 1800:
        return previous
    summary = {"at": now, **report(md)}
    write(md / "buyer-support-summary.json", summary)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent / "logs/buyer-support" / (stamp+".json"), summary)
    return summary
