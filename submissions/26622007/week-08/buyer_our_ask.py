"""Default-OFF order-level A/B: arm B ranks kimauve's "... list price L. Send the ask or say ok to buy. Our ask: A" quote by A.

The quote_alignment study (11/07, buyer_quote_alignment) reads the same text but runs 30 minutes and marks both arms
"probing", which takes its orders out of the goods-ladder, first95 and cap95 trials; it stays as it is (off). This study:
- switch .market/buyer_our_ask.json {"enabled": true, "salt": "...", "start": epoch, "end": epoch}; anything else, or a
  window that is not finite, not started, over, shorter than MIN_HOURS or longer than MAX_HOURS, is off. The end is fixed
  before the start: a file written after its start is off (written <= start), so the window cannot be cut short or moved;
- arm = sha256("buyer_our_ask:" + salt + ":" + order)[0] % 2 (A legacy, B reads); every order started in the window is assigned (ITT),
  and the assignment is journaled in JOURNAL when it is made, so an order that never writes its orders.jsonl row is still
  in the ITT list. An assignment that cannot be journaled leaves arm B untreated (journaled false in the row: assigned,
  not treated). Each arm B read is journaled ("read") before the quote lock, before it can be used: a read that cannot
  be journaled is not used, so no treated quote goes without its line (independent review e552f9c M1). Whether the
  quote then used it ("applied", after the lock) is a second line: a read line with no applied line is a read the
  choice came before, or that the quote_alignment study read first, or an applied line that could not be written,
  which the orders row lists under unjournaled. An order run again journals its assignment again (same arm): the analysis takes each order's first assign line;
- arm B reads only while the switch still says the same salt and window (read again each quote): turning it off, or any
  change, stops arm B in orders already running;
- an arm B read counts as that shop's opening ask wherever quotes are compared: the ranking (surplus, fits, rank_pg
  eligibility and score, food_shelf keep), when enough quotes fit to stop waiting or asking the next batch, and the
  TALK_OVER_SHARE skip. The talk's first turn, the harness note, the price ladder, the food arms (ask_src "text" only) and
  the price paid stay as before (market_buyer ask_src "text_rank"). A quote that comes in after the choice is not read;
- so arm B can talk to another shop than arm A: the orders row's shop, asks and price, and what buyer_rank_pg learns
  from it, differ by arm as any choice does. The quote fields keep such a quote's ask out (ask None, lost and fit as
  legacy read it) and record it under "our_ask", so recent_asks, recent_fits and the food shares never read arm B's
  number as a shop's ask;
- the arm hash is keyed "buyer_our_ask:" + salt, so a salt reused from another study does not give the same arms.
W-20261109-2225-AXPro주기 (instruction 4, 11/10 01:47): 24 h of kit log had 1,313 such quotes, all read none; 261 within
my limit after the SKU and list-price check, 56 of them in orders that passed nothing to the kit (GPT Pro 01:39).
"""
import hashlib
import json
import math
import os
import re
import time

SWITCH = "buyer_our_ask.json"
JOURNAL = os.path.join("buyer_our_ask", "events.jsonl")
MIN_HOURS = 9
MAX_HOURS = 12


def _num(v):
    """A finite epoch-sized number; 10**400 is not one (and raises no OverflowError here)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e12 and math.isfinite(v)


def _switch(md):
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            mtime = os.fstat(f.fileno()).st_mtime
            d = json.load(f)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    salt, start, end = d.get("salt"), d.get("start"), d.get("end")
    if not isinstance(salt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", salt) or not _num(start) or not _num(end) \
            or not (0 < start and start + MIN_HOURS * 3600 <= end <= start + MAX_HOURS * 3600) or not mtime <= start:
        return None
    return {"salt": salt, "start": start, "end": end, "written": mtime}


def _journal(md, event):
    """One line in JOURNAL; whether it was written. A write that fails raises nothing: the caller leaves arm B
    untreated (fail closed, GPT Pro mwa48k8z I), so no treatment goes without its line, and the orders row says
    journaled false."""
    try:
        os.makedirs(os.path.join(md, os.path.dirname(JOURNAL)), exist_ok=True)
        with open(os.path.join(md, JOURNAL), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event, at=time.time()), ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def assign(md, order, now=None):
    """{"arm", "salt", "start", "end", "written", "order", "md", "applied": []} for an order started inside the window, else None."""
    s = _switch(md)
    now = time.time() if now is None else now
    oid = order.get("order") if isinstance(order, dict) else None
    if s is None or not isinstance(oid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", oid) or not _num(now) \
            or not s["start"] <= now < s["end"]:
        return None     # a start time that is not a number: off for this order, not an error
    arm = "AB"[hashlib.sha256(("buyer_our_ask:" + s["salt"] + ":" + oid).encode()).digest()[0] % 2]
    journaled = _journal(md, {"k": "assign", "order": oid, "arm": arm, "salt": s["salt"], "start": s["start"],
                              "end": s["end"], "written": s["written"], "started_at": now})
    return dict(s, journaled=journaled, order=oid, md=md, arm=arm, applied=[], unjournaled=[])


def reads(ctx, now=None):
    """Whether this order reads "Our ask" now: arm B with its assignment journaled, and the switch still says the same
    window with now inside it."""
    now = time.time() if now is None else now
    if not isinstance(ctx, dict) or ctx.get("arm") != "B" or ctx.get("journaled") is not True \
            or not isinstance(ctx.get("md"), str) or not _num(now):
        return False
    s = _switch(ctx["md"])
    return s is not None and (s["salt"], s["start"], s["end"]) == (ctx.get("salt"), ctx.get("start"), ctx.get("end")) \
        and s["start"] <= now < s["end"]


def ready(ctx, shop, ask, now=None):
    """{"shop", "ask"} when arm B reads ask now and its "read" line is in JOURNAL, else None (market_buyer.quote calls it
    before the quote lock: it reads the switch and writes the line)."""
    if not isinstance(ask, int) or isinstance(ask, bool) or ask <= 0 or not reads(ctx, now):
        return None
    read = {"shop": shop, "ask": ask}
    ok = _journal(ctx["md"], {"k": "read", "order": ctx.get("order"), "arm": ctx.get("arm"), "salt": ctx.get("salt"), **read})
    return read if ok else None


def note(ctx, shop, ask):
    """An arm B read, kept on the order (market_buyer.quote calls it under the quote lock: no file IO here)."""
    read = {"shop": shop, "ask": ask}
    ctx["applied"].append(read)
    return read


def journal(ctx, read):
    """The read the quote used, in JOURNAL (after the quote lock is let go); whether it was written. One that was not
    goes on the order's unjournaled list (its "read" line came first); the read in applied, which other quotes'
    threads can be copying, is not touched (independent review ae84363 M1)."""
    ok = _journal(ctx.get("md"), {"k": "applied", "order": ctx.get("order"), "arm": ctx.get("arm"),
                                  "salt": ctx.get("salt"), **read})
    if not ok:
        ctx.setdefault("unjournaled", []).append(dict(read))
    return ok


def applied(ctx, shop, ask):
    """note and journal at once."""
    return journal(ctx, note(ctx, shop, ask))


def measurement(ctx):
    return None if not isinstance(ctx, dict) else {"salt": ctx["salt"], "start": ctx["start"], "end": ctx["end"],
                                                   "written": ctx["written"], "journaled": ctx.get("journaled") is True,
                                                   "arm": ctx["arm"], "applied": [dict(r) for r in ctx["applied"]],
                                                   "unjournaled": [dict(r) for r in ctx.get("unjournaled", [])]}
