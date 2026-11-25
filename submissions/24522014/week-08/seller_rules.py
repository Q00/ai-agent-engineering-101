"""Coded seller moves for messages the code can already read: an opening request and a numeric offer.

The values mirror what the model did across 2,760 logged decisions (analyze_seller_decisions.py): it opened at the quote,
never accepted below the floor, and conceded about 25%, 33%, then 47% of the remaining gap. seller_r3.py still applies
the floor, stock recheck, condition disclosure and signing to every move returned here.
"""

import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "seller_decision.json"
CONCESSION_BY_ASKS = {1: .25, 2: .33}
LATER_CONCESSION = .47
ACCEPT_WITHIN_ROOM = .10


def decision_mode(path=CONFIG):
    """Only an explicit "model" sends offers to the model; anything else, including an unreadable file, is rules."""
    try:
        return "model" if json.loads(Path(path).read_text(encoding="utf-8")).get("mode") == "model" else "rules"
    except (OSError, ValueError, AttributeError):
        return "rules"


def rule_move(ask, offer, floor, quote, asks_so_far):
    """Return (move, explanation). The move has the same shape as the model's."""
    if ask is None or offer is None:
        return {"action": "counter", "price": quote}, "rule: opening at the quote"
    if offer >= ask:
        return {"action": "counter", "price": ask}, "rule: hold the disclosed ask"
    room = ask - floor
    if offer >= floor and (room <= 0 or ask - offer <= ACCEPT_WITHIN_ROOM * room):
        return {"action": "accept", "price": offer}, "rule: offer clears the floor and is close to the ask"
    share = CONCESSION_BY_ASKS.get(asks_so_far, LATER_CONCESSION)
    target = max(offer, floor)
    price = max(math.ceil(ask - share * (ask - target)), floor)
    if price >= ask and ask > target:
        price = ask - 1
    if offer >= floor and price <= offer:
        return {"action": "accept", "price": offer}, "rule: the next concession would reach the offer"
    return {"action": "counter", "price": price}, f"rule: concede {share:.2f} of the gap"
