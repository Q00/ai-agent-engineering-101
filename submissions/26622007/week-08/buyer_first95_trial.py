"""Opt-in first goods CODE90 versus CODE95. No IO, timers or locks.

Call only after the existing ladder and firm policy. Assignment is based on
pre-treatment eligibility, never on a later counter or payment.
"""
import hashlib
import json
import math
import re

ENABLED = True
START = 1794053700  # 2026-11-07 21:15 KST
END = 1794055500  # 2026-11-07 21:45 KST
STUDY = "goods-first95-20261107-2115"


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is not None


def _integer(value):
    # Parser's maximum is twelve digits. Bound before float multiplication.
    return type(value) is int and 0 < value <= 999999999999


def _time(value):
    return type(value) in (int, float) and abs(value) < 10**12 and math.isfinite(value)


def arm(order_id):
    """Deterministic equal-probability allocation, not exact balance."""
    return "AB"[hashlib.sha256((STUDY + "\0" + order_id).encode("ascii")).digest()[0] & 1]


def turn(baseline, history, limit, steps, *, order, shop, sku, probe, now, log):
    if ENABLED is not True or not isinstance(baseline, dict) or baseline.get("action") != "offer":
        return baseline
    if not isinstance(order, dict) or order.get("kind") != "goods" or probe or steps != (.9,):
        return baseline
    started = order.get("_buyer_started_at")
    if not all(_time(v) for v in (START, END, started, now)) or not (
            0 < START < END == START + 1800 and START <= started <= now < END):
        return baseline
    if shop == "11im" or not all(_id(v) for v in (STUDY, order.get("order"), shop, sku)):
        return baseline
    ask, price = baseline.get("shop_price"), baseline.get("price")
    if not all(_integer(v) for v in (ask, price, limit)):
        return baseline
    # Only empty history or the very first shop ask; no previous offers/probes.
    if history != [] and history != [("shop asked", ask)]:
        return baseline
    original = min(int(ask * .9), ask, limit)
    candidate = min(int(ask * .95), ask, limit)
    if price != original or price <= 1 or candidate <= price:
        return baseline
    context = order.get("_first95_trial")
    selected = arm(order["order"])
    if context is None:
        log("[first95-assignment] " + json.dumps(dict(study=STUDY, order=order["order"],
            arm=selected, time=now, shop=shop, sku=sku), sort_keys=True))
        context = order["_first95_trial"] = dict(study=STUDY, arm=selected,
            assigned_at=now, matches=[])
    if not isinstance(context, dict) or context.get("study") != STUDY or context.get("arm") != selected or not isinstance(context.get("matches"), list):
        return baseline
    result = {**baseline, "price": candidate} if selected == "B" else baseline
    match = dict(history_index=len(history), baseline_price=price, candidate_price=candidate,
                 decision_price=result["price"], shop=shop, sku=sku, ask=ask, limit=limit)
    context["matches"].append(match)
    log("[first95-matched] " + json.dumps(dict(study=STUDY, order=order["order"],
        arm=selected, **match), sort_keys=True))
    return result


def measurement(order, out):
    context = order.get("_first95_trial")
    if not isinstance(context, dict):
        return None
    history = out.get("history") or []
    matches = []
    for match in context["matches"]:
        # buyer.talk appends current shop ask, then its attempted offer.
        index = match["history_index"] + 1
        entry = history[index] if index < len(history) else None
        matches.append({**match, "history_offer_attempt":
                        entry[1] if entry and entry[0] == "you offered" else None})
    return {**context, "matches": matches}
