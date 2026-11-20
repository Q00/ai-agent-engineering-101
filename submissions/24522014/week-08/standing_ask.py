"""Buyer option 1, approved 11-09: take the seller's standing ask at the point the run would walk away from it.

The counters stay as they are, at today's cap. Only where the run would end without a deal (the last allowed rule counter not
taken, or the model gate closed) one offer goes out at the seller's clear current ask, and only when that ask is at most the
grade-adjusted floor ceiling (discount_floor, unchanged) and at most the order budget, which is the market's payment mandate
limit, and the quote passes the accept checks. The deal's limit then moves to exactly this ask, so the offer and the receipt
checks hold it to that price. Once per conversation. An order-hash A/B (its own salt) chooses the orders; the setting is read
on every run, and anything missing, unreadable or malformed means off.
"""

import first_message_ab as ab

SECTION = "standing_ask_at_floor"
REASON_CODE = "rule_standing_ask"
DECLINED_CODE = "rule_standing_ask_declined"
REASON = "Seller's clear current ask is within the grade-adjusted floor and the order budget; taken as asked after the coded counters, no model used."
MIN_SECONDS_LEFT = 20


def load(path=ab.CONFIG):
    """{"enabled", "share", "salt"} of the A/B; off unless valid."""
    return ab._load(path, SECTION)


def arm(order_id, settings=None):
    """"standing_ask", "control", or None when the test is off."""
    chosen = ab.arm(order_id, load() if settings is None else settings)
    return {"conditions": "standing_ask", "control": "control"}.get(chosen)
