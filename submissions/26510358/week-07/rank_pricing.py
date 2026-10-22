"""Stable approved quote limits; no market lookup on the negotiation path.

The class name remains compatible with existing seller configuration/test helpers.
"""
from copy import deepcopy


def opening_ask(item, quote_policy):
    margin = quote_policy["opening_margin"].get(item["id"])
    if margin is None:
        margin = max(1, round(item["reserve"] * quote_policy["default_margin_rate"]))
    return int(round(item["reserve"] + margin))


class RankPricing:
    def __init__(self, policy, trace, *, state_path=None):
        self.policy = deepcopy(policy)

    def quote_policy(self):
        return deepcopy({"phase": "normal",
                         "opening_margin": self.policy["opening_margin"],
                         "default_margin_rate": self.policy["default_margin_rate"],
                         "accept_floor_bids": self.policy["accept_floor_bids"]})

    def refresh(self):
        return self.quote_policy()
