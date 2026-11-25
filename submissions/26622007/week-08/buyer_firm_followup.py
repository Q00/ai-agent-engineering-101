"""Opt-in pure fallback for a verified standing-ask follow-up.

Call only after the existing price_ladder returned None. This module does not
connect or enable a market callback; policy_ok is the caller's existing policy
verdict, including goods warranty checks, not a verdict inferred from this text.
"""
import re

import ask_read
import oneshot


def turn(meta, text, history, limit, *, arm=None, goods=False, policy_ok=False):
    """Return the existing code policy's decision, or None to retain fallback."""
    if arm != "B" or meta is not None or policy_ok is not True:
        return None
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return None
    if not isinstance(text, str) or (goods and ask_read.no_warranty(text)):
        return None
    match = re.fullmatch(r"Counteroffer: ([1-9][0-9]{0,11})\. This is my lowest price\.", text)
    if not match or not isinstance(history, list) or not history or len(history) % 2:
        return None
    for i, entry in enumerate(history):
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            return None
        who, price = entry
        if who != ("shop asked" if i % 2 == 0 else "you offered"):
            return None
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            return None
        if i % 2 and (price > history[i - 1][1] or price > limit):
            return None
    ask = int(match[1])
    if ask != history[-2][1] or ask < history[-1][1]:
        return None
    return oneshot.code_turn(ask, [tuple(entry) for entry in history], limit)
