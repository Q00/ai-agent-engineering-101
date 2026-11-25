"""Goods quotes end SECONDS into the run (대표님 8033 "더 빠르게 많은 거래"; review/W-20261108-2135-AX거래량).

The market hands the next order 20.8 s after the last at the earliest (kit log 15:18-21:40, 541 orders that ended within
15 s), so a run that ends later costs orders. Goods quotes waited up to QUOTE_WAIT (25 s) for QUOTE_ENOUGH asks; the goods
orders that ran past the slot cost 7.3 orders/h, while the shop a goods deal was made with had answered by 14 s into the
order in 111 of 113 deals (2 lost). Replay on 920 orders: +3.1 goods deals/h net, +1.0 after halving the gain and doubling
the losses. Food keeps its wait: its late quotes made 9-12 of its deals and the cut lost more than it gained.

Switch: .market/buyer_quote_deadline.json {"enabled": true} (optional "seconds", MIN_SECONDS..MAX_SECONDS); missing,
unreadable or anything else is off, and off is the current wait exactly. BUYER_QUOTE_DEADLINE=0 in the environment is off
too. Read at every order, so no restart either way.
"""
import json
import os

SWITCH = "buyer_quote_deadline.json"
SECONDS = 13.0        # into the run (STARTED); the kit's order line is ~1 s before it, so ~14 s into the order
MIN_SECONDS, MAX_SECONDS = 8.0, 25.0
KINDS = ("goods",)


def _switch(md, env=None):
    env = os.environ if env is None else env
    if str(env.get("BUYER_QUOTE_DEADLINE") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def state(md, order, started, env=None):
    """{"at", "seconds", "cut"} for a goods order with the switch on, else None."""
    sw = _switch(md, env)
    if sw is None or (order or {}).get("kind") not in KINDS:
        return None
    s = sw.get("seconds")
    s = float(s) if type(s) in (int, float) and MIN_SECONDS <= s <= MAX_SECONDS else SECONDS
    return {"at": started + s, "seconds": s, "cut": False}


def clip(order, end):
    """The quote wait's end, no later than the order's deadline."""
    at = ((order or {}).get("_quote_deadline") or {}).get("at")
    return min(end, at) if type(at) in (int, float) else end


def passed(order, now):
    at = ((order or {}).get("_quote_deadline") or {}).get("at")
    return type(at) in (int, float) and now >= at
