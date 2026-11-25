"""GPT Pro's seller counter formulas, for pricing.json "negotiate": {"formula": "negotiate_pro:<function>"}.

Source: Pro run mw5a2y3g-9915d513 (11/07 02:10, 메인 지시), review/W-20261107-0135-AXPro주기/negotiation-formula.md.
Pro's verdict was to keep the logged shares (0.62, 0.53, 0.46, 0.25). Its one candidate, fitted_counter, is the median
of the share the harness actually kept, (counter - floor) / (ask - floor), fit on 18:30-01:33 seller turns, split by the
buyer's move in this talk: "rising" = the offer is above the buyer's previous offer. Not a proven improvement.
negotiate.counter_price holds whatever this returns inside [max(floor, reserve), ask].
"""
# (group, round) -> median kept share; round 4 stands for 4 and later. Rows Pro fit with n >= 133.
KEEP = {("all", 1): 0.6285, ("all", 2): 0.5124, ("all", 3): 0.4610, ("all", 4): 0.7600,
        ("rising", 4): 0.8752}


def _previous_offer(state):
    offers = [p for who, p in state.get("history") or [] if who == "buyer offered"]
    if offers and offers[-1] == state["offer"]:
        offers = offers[:-1]  # the current offer may already be in the history
    return offers[-1] if offers else None


def fitted_counter(state):
    r = min(max(state["round"], 1), 4)
    prev = _previous_offer(state)
    group = "rising" if prev is not None and state["offer"] > prev else "all"
    share = KEEP.get((group, r), KEEP[("all", r)])
    return state["floor"] + share * (state["ask"] - state["floor"])
