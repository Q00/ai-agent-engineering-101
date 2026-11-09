"""One bounded alternative after a confirmed negotiation ended without receipt.

These pure helpers neither authorize payment nor retry a request. The caller
must exclude the first shop's URL, keep the original order deadline, and call
them at most once. Seller prices, grades and cancellation prose are not proof.
"""
import math

from buyer_fast_selection import timeout_choice


def remaining_retry_decisions(status, *, max_decisions, seconds_left):
    """Return the unused shared decision budget, or zero for any ambiguity."""
    if (not isinstance(status, dict)
            or type(max_decisions) is not int or not 1 <= max_decisions <= 4
            or type(seconds_left) not in (int, float)
            or not math.isfinite(seconds_left) or seconds_left < 20
            or status.get("outcome") != "no_deal"
            or status.get("no_deal_only") is not True
            or status.get("receipt_seen") is not False):
        return 0
    used = status.get("decisions")
    if type(used) is not int or not 0 <= used < max_decisions:
        return 0
    reason, terminal = status.get("reason"), status.get("terminal_state")
    ended = ((reason == "seller_rejected" and terminal == "TASK_STATE_REJECTED")
             or (reason == "seller_cancelled" and terminal == "TASK_STATE_CANCELED")
             or (reason == "model_walk_away" and terminal == "TASK_STATE_CANCELED"
                 and status.get("cancel_confirmed") is True))
    return max_decisions - used if ended else 0


def _bounded_candidate(candidate):
    """Accept the local shortlist shape; reject malformed ranking evidence."""
    if not isinstance(candidate, dict):
        return None
    name, offers = candidate.get("name"), candidate.get("offers")
    own, reviews = candidate.get("own_memory"), candidate.get("reviews")
    if (not isinstance(name, str) or not name.strip() or not isinstance(offers, list)
            or not isinstance(own, dict) or not isinstance(reviews, dict)):
        return None
    for data, keys in ((own, ("trades", "settled", "satisfactory_settlements",
                             "unsatisfactory_settlements", "accepted_tradeoffs", "receipt_mismatches")),
                       (reviews, ("rated_count", "low_star_count"))):
        if any(type(data.get(key)) is not int or data[key] < 0 for key in keys):
            return None
    average = reviews.get("average_stars")
    if average is not None and (type(average) not in (int, float)
                               or not math.isfinite(average) or not 1 <= average <= 5):
        return None
    usable = []
    for offer in offers[:5]:
        if (not isinstance(offer, dict) or not isinstance(offer.get("sku"), str)
                or not offer["sku"].strip() or type(offer.get("qty")) is not int or offer["qty"] <= 0):
            continue
        history = offer.get("price_history", [])
        history = history if isinstance(history, list) else []
        history = [dict(group) for group in history[:2] if isinstance(group, dict)
                   and type(group.get("price_to_list")) in (int, float)
                   and math.isfinite(group["price_to_list"]) and group["price_to_list"] > 0]
        usable.append({**offer, "price_history": history})
    if not usable:
        return None
    return {**candidate, "offers": usable,
            "selection_role": candidate.get("selection_role", "evidence")}


def select_alternative(candidates, primary, *, seed):
    """Choose locally from the existing bounded shortlist, without new calls.

Unknown current grades and absent reviews remain eligible: the new negotiation
and checkout still enforce the order's certificate/quality/price requirements.
This hint never reuses an earlier receipt or authorizes an old price.
"""
    if (not isinstance(candidates, (list, tuple)) or not isinstance(primary, dict)
            or not isinstance(primary.get("name"), str) or not primary["name"].strip()):
        return None
    pool, seen = [], {primary["name"]}
    for candidate in candidates[:6]:
        bounded = _bounded_candidate(candidate)
        if bounded is None or bounded["name"] in seen:
            continue
        seen.add(bounded["name"])
        pool.append(bounded)
    choice = timeout_choice(pool, seed=seed)
    if choice is None:
        return None
    return {**choice, "reason": "Confirmed first negotiation ended without a receipt; "
            "locally selected one alternative from the existing shortlist. "
            "The shared time and decision limits and all purchase checks still apply."}
