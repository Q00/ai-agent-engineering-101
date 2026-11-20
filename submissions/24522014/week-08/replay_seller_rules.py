"""Read-only replay of the coded seller rule over the model's logged decisions.

The buyer's recorded offers answered the model's asks, not the rule's, so everything past the first difference in a
negotiation is unknowable. Margin figures only cover earlier acceptances at an offer that was really made; they are estimates.
"""

from collections import Counter, defaultdict
import json
from pathlib import Path
from datetime import datetime, timezone
import re
from statistics import median

from analyze_seller_decisions import ROOT, load_calls, spread
import seller_rules

FIRST_BUYER = re.compile(r"\((G[0-9a-f]+)\)")


def stamp_seconds(name):
    return datetime.strptime(name.split("-")[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()


def compare(calls, accept_within=None):
    """Decision by decision: same action, and how far the rule's counter is from the model's."""
    original = seller_rules.ACCEPT_WITHIN_ROOM
    if accept_within is not None:
        seller_rules.ACCEPT_WITHIN_ROOM = accept_within
    try:
        actions, opening_gap, counter_gap, below_floor = Counter(), [], [], 0
        for call in calls:
            model_action = "accept" if call["action"] == "accept" else "counter" if call["action"] == "ask" else "invalid"
            if call["offers"] and call["asks"]:
                move, _ = seller_rules.rule_move(call["asks"][-1], call["offers"][-1], call["floor"], call["quote"], len(call["asks"]))
                actions[f"followup model={model_action} rule={move['action']}"] += 1
                if move["action"] == "counter" and model_action == "counter" and call["price"]:
                    counter_gap.append(move["price"] / call["price"] - 1)
            else:
                move, _ = seller_rules.rule_move(None, None, call["floor"], call["quote"], 0)
                actions[f"opening model={model_action} rule={move['action']}"] += 1
                if call["price"]:
                    opening_gap.append(move["price"] / call["price"] - 1)
            if move.get("price", call["floor"]) < call["floor"]:
                below_floor += 1
        return {"actions": dict(sorted(actions.items())), "opening_rule_over_model": spread(opening_gap),
                "counter_rule_over_model": spread(counter_gap), "rule_below_floor": below_floor}
    finally:
        seller_rules.ACCEPT_WITHIN_ROOM = original


def negotiations(calls):
    """Group calls by following the growing transcript: a call continues the open chain with the same first request and first ask."""
    chains = defaultdict(list)
    open_chain = {}
    for call in calls:
        key = (call["first"], call["quote"])
        if not call["offers"]:
            chain_id = f"{key}-{call['name']}"
            open_chain[key] = chain_id
            chains[chain_id].append(call)
        elif key in open_chain:
            chains[open_chain[key]].append(call)
    return chains


def matched_deal(chain, offers):
    """A signed offer for this SKU at this chain's last ask or accepted price, shortly after its last call."""
    last = chain[-1]
    sku = FIRST_BUYER.search(last["first"])
    if not sku:
        return None
    price = last["price"]
    at = stamp_seconds(last["name"])
    for offer in offers:
        if not offer.get("used") and offer["sku"] == sku[1] and offer["price"] == price and 0 <= offer["issued_at"] - at <= 300:
            offer["used"] = True
            return offer
    return None


def margin_estimate(calls, root, accept_within=None):
    """If the rule would have accepted a real offer earlier, compare that offer with what the negotiation actually reached."""
    original = seller_rules.ACCEPT_WITHIN_ROOM
    if accept_within is not None:
        seller_rules.ACCEPT_WITHIN_ROOM = accept_within
    try:
        offers = []
        for path in (Path(root) / ".market/signed-offers").glob("*.json"):
            offers.append(json.loads(path.read_text(encoding="utf-8")))
        offers.sort(key=lambda o: o.get("issued_at", 0))
        chains = negotiations(calls)
        result = Counter()
        given_up, new_margin = [], []
        for chain in chains.values():
            deal = matched_deal(chain, offers)
            result["negotiations"] += 1
            result["with_matched_deal"] += bool(deal)
            for call in chain:
                if not (call["offers"] and call["asks"]):
                    continue
                move, _ = seller_rules.rule_move(call["asks"][-1], call["offers"][-1], call["floor"], call["quote"], len(call["asks"]))
                if move["action"] != "accept":
                    continue
                if call["action"] == "accept":
                    result["same_accept"] += 1
                elif deal:
                    result["earlier_accept_on_a_deal"] += 1
                    given_up.append(deal["price"] - call["offers"][-1])
                else:
                    result["accept_where_no_deal_was_matched"] += 1
                    new_margin.append(call["offers"][-1] - call["floor"])
                break
        return {"counts": dict(result), "price_given_up_on_earlier_accepts": spread(given_up), "price_given_up_sum": sum(given_up),
                "lower_bound_margin_over_floor_on_extra_deals": spread(new_margin), "extra_margin_over_floor_sum": sum(new_margin),
                "note": "Extra deals assume the buyer would still pay at checkout. Floor already includes margin and carrying cost."}
    finally:
        seller_rules.ACCEPT_WITHIN_ROOM = original


def report(root=ROOT):
    calls = load_calls(root)
    return {"calls_replayed": len(calls), "model_calls_saved_by_rules_mode": len(calls), "at_threshold": {
        str(share): {"decisions": compare(calls, share), "margin": margin_estimate(calls, root, share)} for share in (.05, .10, .20)}}


def main():
    print(json.dumps(report(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
