"""Seller turns where Jev reads the buyer and rules answer (NEGOTIATE_ENGINE=jevrule; 대표님 11/07 01:59).

On 2,772 seller-harness turns (11/06 17:18 - 11/07 02:00, market-kit.log) the code after the harness already fixed the
action: an offer at or above the round's floor is accepted, one under it is countered, and the counter is held inside
[floor, ask]. The harness only chose where in that range to counter and wrote one line. 91% of the messages were a bare
number. Its counters sat at floor + share x (ask - floor), the share falling by round (median 0.62, 0.53, 0.46, then
0.25); that rule gives its price within 1% on 84% and within 3% on 95% of 1,755 counters. Replayed over all 2,772 turns
this engine took 2,745 (2,507 by rule, 238 read by Jev at confidence 0.9 or more, all 238 the offer the harness read),
the same action on all but 3, the price within 3% on 93% (mean +0.3%), at about 0.07 s a turn against 3.7 s.
How long the shop took did not change whether buyers stayed (left after a counter: 12% under 3 s, 10% at 4-6 s).

Everything doubtful still goes to the harness: no number read, Jev under 0.9, a number that is not in the message, one
of our older asks, above our ask (FAIR in seller.py), or a message that talks like an instruction. seller.py runs the
same price guards after this as after the harness.

Switch: NEGOTIATE_ENGINE=jevrule|llm in the environment, else pricing.json "negotiate": {"engine": ...}, read at every
turn so it goes off without a restart.

The counter price formula is swappable, also read at every turn (메인 11/07 02:10: GPT Pro writes one from the logs):
  "negotiate": {"formula": "share", "counter_share": [0.62, 0.53, 0.46, 0.25]}   the logged formula (default)
  "negotiate": {"formula": "module:function"}   function(state) -> price; state is the deal plus floor, offer, round
Whatever a formula returns is held inside [max(floor, reserve), ask]; a formula that fails, or shares that are not
numbers in [0, 1], fall back to the logged one. Both keys are a person's settings (hard_limits.FIXED).
"""
import importlib
import os
import random
import re

import fast_classify
import shop_memory

# share of (ask - floor) kept by the counter in round 1, 2, 3; the last repeats (medians of the harness, see above).
# deal["rounds"] counts every buyer message, as concession_floor does, so the two stay in step (the replay used it too)
COUNTER_SHARE = [0.62, 0.53, 0.46, 0.25]
# A keyword screen only, easy to get around: the real protection is seller.py's guards after this (never under the
# reserve, never over our ask without FAIR). Do not relax those on the strength of this list.
SUSPECT = re.compile(r"\b(?:system|admin|assistant|instruction|ignore|override|prompt|already agreed|you agreed|"
                     r"the shop agreed|market rule|developer)\b", re.I)
LINES = ["Thanks for your offer of {offer}. I can come down a little.",
         "I appreciate the offer of {offer}. Here is a better price.",
         "Thank you for {offer}. I can meet you partway."]
# the buyer names our own current ask: taken by Jev only with words of agreement ("OK, 30000 it is. Deal at 30000.");
# "Your 30000 is too much, I offer 22000" read wrong would close at a price the buyer did not mean (review 11/07, Low)
AGREE = re.compile(r"\b(?:deal|ok|okay|accept(?:ed)?|it is|fine|works|yes|agreed? to buy)\b", re.I)
# no "lower": seller.py's at-reserve rewrite reads "go lower" as a promise (test_negotiate checks every line)
HOLD = ["Thanks for your offer of {offer}. This is the least I can take.",
        "I appreciate the offer of {offer}, but this price stays."]
LAST = "Thank you for your offer of {offer}."
FINAL = ["Thanks for your offer of {offer}. This is already the lowest price we can sell it for.",
         "I appreciate the offer of {offer}, but we are already at our floor price for this item.",
         "Thank you for {offer}. Our price is already as low as it goes."]


def seller_turn_if_on(text, deal, floor, jev=None, max_rounds=None):
    """seller_turn when the switch is on, else None; one call for seller.py's thread (pricing.json is read off the loop)."""
    return seller_turn(text, deal, floor, jev, max_rounds) if engine_on() else None


def engine_on():
    env = (os.environ.get("NEGOTIATE_ENGINE") or "").strip().lower()
    if env:
        return env == "jevrule"
    cfg = shop_memory.load_pricing().get("negotiate")
    return isinstance(cfg, dict) and str(cfg.get("engine", "")).lower() == "jevrule"


def share_price(state, shares=COUNTER_SHARE):
    """floor + share[round] x (ask - floor), the last share repeating."""
    share = shares[min(max(state["round"], 1), len(shares)) - 1]
    return state["floor"] + share * (state["ask"] - state["floor"])


def _shares(v):
    ok = isinstance(v, list) and v and all(isinstance(s, (int, float)) and not isinstance(s, bool) and 0 <= s <= 1 for s in v)
    return v if ok else COUNTER_SHARE


def formula():
    """The counter price function named in pricing.json negotiate.formula (see the module docstring)."""
    cfg = shop_memory.load_pricing().get("negotiate")
    cfg = cfg if isinstance(cfg, dict) else {}
    name = str(cfg.get("formula") or "share")
    if ":" in name:
        mod, _, fn = name.partition(":")
        try:
            f = getattr(importlib.import_module(mod), fn)
            if callable(f):
                return f
        except Exception:  # noqa: BLE001 - a broken formula falls back to the logged one
            pass
        return share_price
    shares = _shares(cfg.get("counter_share"))
    return lambda state: share_price(state, shares)


def counter_price(deal, floor, offer=0):
    """The counter for this round from the configured formula, held inside [max(floor, reserve), ask]."""
    least = max(floor, deal["reserve"])
    state = dict(deal, floor=floor, offer=offer, round=deal["rounds"])
    try:
        p = float(formula()(state))
        if p != p or p in (float("inf"), float("-inf")):
            raise ValueError(p)
    except Exception:  # noqa: BLE001
        p = share_price(state)
    return int(min(max(round(p), least), deal["ask"]))


HOLD_TURN = "hold"   # read_offer: Jev gave no answer in time, so hold our ask and ask for a number


def read_offer(text, deal, jev):
    """The offer in the message, HOLD_TURN when Jev did not answer, or None when it is not certain."""
    offer = fast_classify.bare_offer(text)
    if offer is not None:
        return offer
    if jev is None or SUSPECT.search(text or ""):
        return None
    try:
        offer, conf = jev(text)
    except Exception:  # noqa: BLE001 - any failure goes to the harness
        return None
    if (offer, conf) == fast_classify.JEV_DOWN:
        return HOLD_TURN
    if offer is None or not conf >= fast_classify.JEV_MIN_CONFIDENCE or offer not in fast_classify.written(text):
        return None
    older = {p for who, p in deal.get("history") or [] if who == "you asked"} - {deal["ask"]}
    if offer in older or (offer == deal["ask"] and not AGREE.search(text)):
        return None
    return offer


def seller_turn(text, deal, floor, jev=None, max_rounds=None):
    """The turn as seller.py takes it from the harness ({buyer_offer, action, price, message, why}), or None.
    max_rounds: seller.MAX_ROUNDS; at it seller.py turns a counter into a reject that starts with this message."""
    if SUSPECT.search(text or ""):
        return None
    offer = read_offer(text, deal, jev)
    if offer == HOLD_TURN:   # seller.py answers "Please tell me the price you offer. My price: <ask>."
        # once a talk: a longer Jev outage, or the round limit, goes to the harness instead of asking again and again
        # (independent review 11/07, Low/Medium)
        if deal.get("jev_held") or (max_rounds is not None and deal["rounds"] >= max_rounds):
            return None
        deal["jev_held"] = True
        return {"buyer_offer": 0, "action": "counter", "price": deal["ask"], "message": "",
                "why": f"Jev gave no answer in {fast_classify.JEV_SECONDS} s: hold the ask {deal['ask']}"}
    if not offer or offer > deal["ask"]:
        return None
    least = max(floor, deal["reserve"])
    if offer >= least:
        return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "",
                "why": f"jevrule offer {offer} >= floor {least}"}
    price = counter_price(deal, floor, offer)
    if max_rounds is not None and deal["rounds"] >= max_rounds:
        line = LAST
    elif price <= deal["reserve"]:
        line = random.choice(FINAL)
    elif price >= deal["ask"]:
        line = random.choice(HOLD)
    else:
        line = random.choice(LINES)
    return {"buyer_offer": offer, "action": "counter", "price": price, "message": line.format(offer=offer),
            "why": f"jevrule counter {price} to {offer} (round {deal['rounds']}, floor {least})"}
