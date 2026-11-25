"""Source-only opt-in trial. No IO except the caller's existing flushed log.

Eligibility probes B's pure result BEFORE assignment in both arms. This is a
pre-treatment input predicate, never a selection on treatment outcomes.
policy_ok means existing negotiation checks passed, NOT receipt certification.
"""
import hashlib
import json
import math
import re

import buyer_firm_followup

ENABLED = True
START = 1794050700  # 2026-11-07 20:25 KST, pending reviewed deployment
END = START + 1800
STUDY = "firm-followup-20261107-2025"


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is not None


def arm(order_id):
    """Stable 1:1 hash allocation probability, not an exact count balance."""
    return "AB"[hashlib.sha256((STUDY + "\0" + order_id).encode("ascii")).digest()[0] & 1]


def turn(baseline, meta, text, history, limit, *, order, shop, sku, policy_ok, now, log):
    """Retain baseline unless this exact eligible goods follow-up is assigned B."""
    if baseline is not None or ENABLED is not True:
        return baseline
    if not isinstance(order, dict):
        return baseline
    started = order.get("_buyer_started_at")
    if not all(_number(v) for v in (START, END, now, started)) or not (
            0 < START < END == START + 1800 and START <= started <= now < END):
        return baseline
    if order.get("kind") != "goods" or not all(_id(v) for v in (STUDY, order.get("order"), shop, sku)):
        return baseline
    candidate = buyer_firm_followup.turn(meta, text, history, limit,
                                         arm="B", goods=True, policy_ok=policy_ok)
    if candidate is None:
        return baseline
    context = order.get("_firm_trial")
    if context is None:
        selected = arm(order["order"])
        # Sole assignment boundary: log() flushes before any treatment decision.
        log("[firm-assignment] " + json.dumps({"study": STUDY, "order": order["order"],
            "arm": selected, "time": now, "shop": shop, "sku": sku}, sort_keys=True))
        context = order["_firm_trial"] = {"study": STUDY, "arm": selected,
            "assigned_at": now, "eligible": 0, "actual_decisions": 0, "matches": []}
    if context["study"] != STUDY or context["arm"] != arm(order["order"]):
        return baseline
    result = candidate if context["arm"] == "B" else baseline
    context["eligible"] += 1
    context["actual_decisions"] += int(result is not None)
    match = {"history_index": len(history), "baseline": baseline,
             "candidate": dict(candidate), "decision": dict(result) if result else None}
    context["matches"].append(match)
    log("[firm-matched] " + json.dumps({"study": STUDY, "order": order["order"],
        "arm": context["arm"], "first": context["eligible"] == 1, **match}, sort_keys=True))
    return result


def measurement(order, out):
    """History records offers attempted by buyer.talk, not confirmed wire delivery."""
    context = order.get("_firm_trial")
    if not context:
        return None
    matches = []
    history = out.get("history") or []
    for match in context["matches"]:
        index = match["history_index"] + 1
        entry = history[index] if index < len(history) else None
        matches.append({**match, "history_offer": entry[1] if entry and entry[0] == "you offered" else None})
    return {**context, "matches": matches}
