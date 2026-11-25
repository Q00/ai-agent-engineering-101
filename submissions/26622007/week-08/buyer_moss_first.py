"""Default-OFF order-level A/B: moss-and-oak's first turn priced by code (arm B) or by the harness (arm A, legacy).

moss-and-oak quotes "<name> (<SKU>), <grade, warranty>, market certificate attached. Also in stock in this category:
<SKUs>. Send your offer as a number, or reply 'accept' to buy now. Asking: N." market_buyer reads N (also_in_stock_only),
but ask_read reads one SKU's quotes only, so oneshot.text_price leaves that first turn to the harness: 77 of 127 moss
talks 11/09 01:33 - 11/10 05:30 opened by the harness at a median 0.673 of the ask, 26 ending in a deal; the 50 opened by
code (a metadata ask) at 0.90, 50 deals, 46 of them food (GPT Pro mw9q5kxg §2, W-20261109-2225-AXPro주기). Food against
goods and the hour differ between those groups, so what 0.90 does to goods talks is not known: this study measures it.
- switch .market/buyer_moss_first.json {"enabled": true, "salt": "...", "start": epoch, "end": epoch}; anything else, or a
  window that is not finite, not started, over, shorter than MIN_HOURS or longer than MAX_HOURS, is off. The end is fixed
  before the start (GPT Pro mw9q5kxg §4.1: 9 h minimum, 12 h maximum);
- arm = sha256("buyer_moss_first:" + salt + ":" + order)[0] % 2 (A legacy, B code); every order started in the window is
  assigned (ITT), whatever shop it picks;
- only the first turn of a talk with moss-and-oak (the shop I picked, by its name in my shop list, not by its text) on its
  whole quote that code left to the harness (no metadata ask, no code answer, the ask read is the quote's "Asking: N"),
  and not goods whose text says no warranty (as oneshot.text_price): both arms note it in "seen" with the offer code would
  make; arm B makes that offer (oneshot.code_turn at STEP, 0.90 of the ask whatever the ladder's steps, never over my
  limit). Every later turn is as before in both arms. "applied" is that decision, not what was sent or paid: the kit log
  has the send, the ledger the payment (GPT Pro mw9tn6kj code review M);
- the assignment (order, arm, started_at) and every seen turn (live, applied) go to JOURNAL as they happen, before any
  send, so an order that never writes its orders.jsonl row is still in the ITT list (GPT Pro mw9tn6kj code review I).
  Either line that cannot be written leaves arm B untreated (fail closed): an assignment so is journaled false in the
  row (assigned, never treated), a seen turn so has journaled false in the row's seen (GPT Pro mwa48k8z I, independent
  review e552f9c M2);
- arm B acts only while the switch still says the same window: turning it off (or another salt) stops arm B at the next
  turn of an order already running, not only for new orders;
- the switch file must be written before its start (mtime <= start): a window started in the past, or one edited after its
  start, is off (independent review 5445a75 M1); the mtime is recorded;
- an arm B talk the change priced (applied) is left out of what both arms learn, as every buyer A/B here: the trades'
  prices (priced, item_stats), recent_asks, buyer_shop_profile and buyer_rank_pg (independent review 5445a75 I1). An arm
  B order whose talk it did not touch is the legacy talk and stays in.
"""
import hashlib
import json
import math
import os
import re
import time

import ask_read
import oneshot

SWITCH = "buyer_moss_first.json"
JOURNAL = os.path.join("buyer_moss_first", "events.jsonl")
MIN_HOURS = 9
MAX_HOURS = 12
SHOP = "moss-and-oak"
STEP = 0.9


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
    """{"arm", "salt", "start", "end", "seen": [], "applied": []} for an order started inside the window, else None."""
    s = _switch(md)
    now = time.time() if now is None else now
    oid = order.get("order") if isinstance(order, dict) else None
    if s is None or not isinstance(oid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", oid) or not _num(now) \
            or not s["start"] <= now < s["end"]:
        return None
    arm = "AB"[hashlib.sha256(("buyer_moss_first:" + s["salt"] + ":" + oid).encode()).digest()[0] % 2]
    journaled = _journal(md, {"k": "assign", "order": oid, "arm": arm, "salt": s["salt"], "start": s["start"],
                              "end": s["end"], "written": s["written"], "started_at": now})
    return dict(s, journaled=journaled, order=oid, arm=arm, seen=[], applied=[])


def live(md, ctx, now=None):
    """Arm B still acts: its assignment was journaled, the switch says the same window and now is inside it."""
    now = time.time() if now is None else now
    s = _switch(md)
    return isinstance(ctx, dict) and ctx.get("journaled") is True and s is not None and _num(now) and \
        (s["salt"], s["start"], s["end"]) == (ctx.get("salt"), ctx.get("start"), ctx.get("end")) and s["start"] <= now < s["end"]


def turn(d, meta, text, history, limit, steps, quoted, moss, goods, ctx, md, shop, now=None):
    """The turn as the ladder priced it (d), or in arm B code's first offer on moss's whole quote; see the module text."""
    if d is not None or meta is not None or not isinstance(ctx, dict) or not moss or shop != SHOP or history:
        return d
    if not isinstance(quoted, int) or isinstance(quoted, bool) or quoted <= 0 or (goods and ask_read.no_warranty(text)):
        return d
    code = oneshot.code_turn(quoted, history, limit, (STEP,))
    if not code or code.get("action") != "offer":
        return d
    note = {"shop": shop, "ask": quoted, "offer": code["price"]}
    ctx["seen"].append(note)
    on = live(md, ctx, now)
    apply = ctx.get("arm") == "B" and on
    if not _journal(md, dict(note, k="seen", order=ctx.get("order"), arm=ctx.get("arm"), step=STEP, limit=limit,
                             goods=bool(goods), live=on, applied=apply)):
        apply = False           # a treated turn always has its line (fail closed)
        note["journaled"] = False   # in the row's seen: this turn's line is missing (arm B so went untreated)
    if not apply:
        return d
    ctx["applied"].append(dict(note))
    return code


def measurement(ctx):
    return None if not isinstance(ctx, dict) else {"salt": ctx["salt"], "start": ctx["start"], "end": ctx["end"],
                                                   "written": ctx["written"], "journaled": ctx.get("journaled") is True,
                                                   "arm": ctx["arm"], "seen": [dict(n) for n in ctx["seen"]],
                                                   "applied": [dict(n) for n in ctx["applied"]]}


def trade_mark(order):
    """What market_buyer writes into the order's trade: "B" when arm B priced its talk, else None."""
    g = (order or {}).get("_moss_first")
    return "B" if isinstance(g, dict) and g.get("arm") == "B" and g.get("applied") else None


def applied_b(row):
    """Whether an orders.jsonl row is an arm B talk this study priced: the shop learning both arms read leaves it out."""
    g = row.get("moss_first") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B" and bool(g.get("applied"))


def priced(trades):
    """The trades whose prices both arms may learn from: without the arm B talks this study priced."""
    return [t for t in trades if t.get("moss_first") != "B"]
