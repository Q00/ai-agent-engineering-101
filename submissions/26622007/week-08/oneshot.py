"""The talk priced by code: one offer under the shop's first ask, then its counter taken, with no harness call.

Of the 447 talks logged 11/06 21:3x - 11/07 01:13, a shop answered my offer in a median 1.0 s (mean 3.8 s), while the
harness took 3.9 s a turn over 3.8 turns an order: the harness, not the shop, set the 22-28 s a talk took of the ~49 s an
order took. Replayed on those talks (review/W-20261107-0115-AX바이어한방제안), 0.9 of the first ask and then the counter
kept 0.364 of list price a deal against the harness's 0.379, at 5.5 s a talk instead of 29.5 s: 80 deals an hour
instead of 47. With 263 h left that is the deals percentile 0.56 -> 0.91 for at most ~0.02 of the saved percentile.

Settings (pricing.json "buyer", read at every order): oneshot (true/false), oneshot_steps (shares of the first ask, rising,
up to 5). BUYER_ONESHOT=1 / 0 (true / false, on / off) in the environment wins over pricing.json; the meta-harness may
not move buyer.oneshot (hard_limits.FIXED).

An ask in the message metadata is priced by code. Only 80 of 1,419 asks logged came that way; an ask written in text is
priced by code when the whole text is one of the shop templates in TEMPLATE ("Counteroffer: 13049.", "11300 is too low.
Counteroffer price: 13049.", "Five offers left. Final price: 108635.") or ask_read reads it by rule: one ask word and number, every other number placed (name, SKU,
hours, list price, my offer named back), no word that can change it (was, now, minus, other, each, ...). Four independent
reviews (11/07) broke readings of free text by word lists ("Was asking 20000, now just 12000", "asking 19000 minus 2000",
"Others ask 19000, I accept your offer"); ask_read leaves each of those to the harness. The first turn's text (the quote)
is priced by code only when ask_read reads the same ask market_buyer read from it and checked against the list price
(quoted); on the 2,613 harness turns logged 11/04-11/07 it read 87.9% of the quotes and 89.5% of later turns, every one
the price the harness read (review/W-20261107-0840-AX호가정규식).
"""
import json
import math
import os
import re
import time

import ask_read

STEPS = (0.9,)
MAX_STEPS = 5
# a later turn's whole text, as shops in the log write it (logs/market-kit.log 11/06 21:3x - 11/07 01:13); nothing else.
# "Five offers left. Final price: 108635. " (moss-and-oak) and "Final price: 9115. " (two food shops) from 11/10: 45 of
# the 76 buyer-harness calls 03:55-04:25 read only that, after the moss quote parser (72981aa) made moss a pick
LEFT = r"(?:[0-9]{1,2}|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten) offers? left\.\s*"
TEMPLATE = re.compile(
    r"\s*(?:(?P<mine>[0-9]{1,12}) is too low\.\s*Counteroffer price:|(?:[0-9]{1,2} offers? left\.\s*)?Counteroffer:"
    r"|(?P<final>(?:" + LEFT + r")?Final price:)|Our lowest price for this unit:)\s*(?P<p>[0-9]{1,12})\.?"
    r"(?:\s*SKU G[0-9a-f]{8}, grade [ABC]; market certificate at checkout\."
    r"(?:\s*Includes a [0-9]{1,3}-hour warranty: market-enforced full refund if the goods break during warranty; "
    r"the quote includes the warranty surcharge\.)?)?"
    r"(?:\s*Reply ok to take it\.)?\s*")


def final_text(text):
    """The whole text is a TEMPLATE "Final price" text (with any of its tails): the harness read it before this module
    read "Final price" (11/10), so market_buyer keeps the accept band off that turn and each such talk ends as before
    (independent reviews of the change, I1 and M1)."""
    m = TEMPLATE.fullmatch(text or "")
    return bool(m and m.group("final"))


FINAL_M1_SWITCH = "buyer_final_m1.json"
FINAL_M1_MAX_HOURS = 12


def _epoch(v):
    """A finite epoch-sized number (not a bool; 10**400 is not one and raises nothing here)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e12 and math.isfinite(v)


def final_m1_on(md, env=None, now=None):
    """Whether final_m1 is on now: final_m1_window is not None."""
    return final_m1_window(md, env, now) is not None


def final_m1_window(md, env=None, now=None):
    """final_m1 on: .market/buyer_final_m1.json {"enabled": true, "start": epoch, "end": epoch} with start <= now < end,
    0 < end - start <= FINAL_M1_MAX_HOURS, and the file written before its start (mtime <= start), so the window has a
    fixed end and cannot be moved once it began (ax-pro-cycle step 5, main instruction 10: the shadow had no end and
    stopped only when turned off by hand). Missing, unreadable, no window, anything else, or BUYER_FINAL_M1=0 is off
    (default off). Read at every order and again at each turn it would note, so no restart either way. Returns the
    window [start, end] when on, else None: an order keeps the window it started in and notes only while that same
    window is on (GPT Pro mwa9krfw M-1: an order from window A resumed in window B noted into B)."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("BUYER_FINAL_M1") or "").strip().lower() in ("0", "false", "no", "off") or not _epoch(now):
        return None
    try:
        with open(os.path.join(md, FINAL_M1_SWITCH), encoding="utf-8") as f:
            mtime = os.fstat(f.fileno()).st_mtime
            d = json.load(f)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    start, end = d.get("start"), d.get("end")
    if _epoch(start) and _epoch(end) and 0 < start < end <= start + FINAL_M1_MAX_HOURS * 3600 \
            and mtime <= start and start <= now < end:
        return [start, end]
    return None


def final_m1(d, meta, text, history, limit, steps):
    """code_turn walks once the steps and both last offers are spent, before it looks at an ask at or under my last offer;
    so a text-only "Final price: N" at my last offer ends the talk unbought (GPT Pro mw9q5kxg §1.5, §4.1 M1; 0 such
    talks 04:49:42 - 05:30). This returns what M1 would do: that walk alone becomes an offer of N, the same price I
    offered last, never over my limit. A shop that did not take its own ask (my last offer was that ask) still gets the
    walk, and every other turn is as code_turn left it. market_buyer only notes it (a shadow, GPT Pro mw9tn6kj: 0 such
    Final turns in 118): the turn sent is code_turn's."""
    if not d or d.get("action") != "walk_away" or meta is not None or not final_text(text):
        return d
    mine = [p for who, p in history if who == "you offered"]
    p = d.get("shop_price")
    if not mine or not isinstance(p, int) or isinstance(p, bool) or p <= 0 or p != mine[-1] or p > limit \
            or len(mine) < len(steps or ()) + 2 or history[-2:] == [("shop asked", p), ("you offered", p)]:
        return d
    return {"shop_price": p, "action": "offer", "price": p}


def enabled(cfg, env=None):
    env = os.environ if env is None else env
    v = str(env.get("BUYER_ONESHOT") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return (cfg or {}).get("oneshot") is True


def steps(cfg):
    s = (cfg or {}).get("oneshot_steps")
    if not isinstance(s, list) or not 1 <= len(s) <= MAX_STEPS:
        return STEPS
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0.5 <= x <= 1.0 for x in s):
        return STEPS
    if any(b < a for a, b in zip(s, s[1:])):
        return STEPS
    return tuple(float(x) for x in s)


def text_price(text, history, quoted=None, goods=False):
    """The shop's ask from its text, or None for the harness. A later turn's text: one TEMPLATE whole, or a text
    ask_read reads by rule; the number it names as my offer must be my last offer, and the ask not over the shop's last
    ask nor under my last offer. The first turn's text (the quote): ask_read's reading only when it is quoted, the ask
    market_buyer read from the same text and checked against the SKU's list price (text_ask_checked). Goods whose text
    says they come without a warranty are never priced from the text (ask_read.no_warranty)."""
    if goods and ask_read.no_warranty(text):
        return None
    mine = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    if not asks or not mine:
        if asks or mine or not isinstance(quoted, int) or isinstance(quoted, bool) or quoted <= 0:
            return None
        p, _ = ask_read.read(text)
        return p if p == quoted else None
    m = TEMPLATE.fullmatch(text or "")
    if m:
        if m.group("mine") and int(m.group("mine")) != mine[-1]:
            return None
        p = int(m.group("p"))
    else:
        p, yours = ask_read.read(text)
        if p is None or any(y != mine[-1] for y in yours):
            return None
    if p <= 0 or p > asks[-1] or p < mine[-1]:
        return None
    return p


def turn(meta, text, history, limit, steps=STEPS, quoted=None, goods=False):
    """code_turn on the metadata ask, else on text_price; None leaves the turn to the harness."""
    return code_turn(meta if meta is not None else text_price(text, history, quoted, goods), history, limit, steps)


def code_turn(ask, history, limit, steps=STEPS):
    """The harness's answer ({"shop_price", "action", "price"}) for this turn, or None when no ask was read (the harness
    reads the text then). history holds the turns before this ask. The offer never goes over limit or the ask."""
    if not isinstance(ask, int) or isinstance(ask, bool) or ask <= 0:
        return None
    offers = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    first, last, k = (asks[0] if asks else ask), (offers[-1] if offers else 0), len(offers)
    walk = {"shop_price": ask, "action": "walk_away", "price": 0}
    offer = lambda p: {"shop_price": ask, "action": "offer", "price": int(p)}
    if history[-2:] == [("shop asked", ask), ("you offered", ask)] or k >= len(steps) + 2:
        return walk               # the shop did not take its own ask, or the steps and both last offers are spent
    if ask <= last:
        return offer(ask)
    if k < len(steps):
        p = min(int(steps[k] * first), ask, limit)
        if p > last:
            return offer(p)
    if ask <= limit:
        return offer(ask)         # the counter is within what I pay: taken
    return offer(limit) if last < limit else walk     # one raise to my limit, then the next order
