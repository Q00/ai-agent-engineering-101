"""Food: open lower than 0.9 of the first ask, then climb back to it (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md).

saved is half of buyer ALL. Our food deals keep less of list price than the buyers above us at the same shop and grade:
11/09 07:44 cut, last 12 h, a food deal kept .356 of list against dragonash .400, sergiobuilds .385, 11im .379, and
food grade C at olstjr .482 against choo-chu .604, at psyoung82 .300 against dragonash .550. 112 of our 442 grade C
food talks ended with the shop taking our one 0.9 offer as it was (olstjr, totato2203-r3, psyoung82 every time,
yms979 96%): room below 0.9 that the one-step talk (oneshot.STEPS) never asks for.

Arm B prices the talk with the ladder (0.80, 0.90), or (0.70, 0.80, 0.90) when the quote names grade C. The limit,
the accept band and oneshot.code_turn's rules stay as they are, so the last rung is today's 0.9 and the worst case is
today's price after one or two more turns. Arm A keeps (0.9,). Both arms are recorded in orders.jsonl.

Eligible: a food talk priced by the one-step default (steps == (0.9,)), resumed from a quote with a numeric ask whose
opening code prices (code_priced, as the opening probes require), with no opening probe of its own (one-won, opening
discount, one-won default, food quote yield), without the B3 hold rung (buyer_b3b1: it acts only after a single 0.9
offer, so it would fire in arm A and never in B), and not an order the food shelf study (buyer_food_shelf) changes by
arm (would_drop not empty), so the studies touch disjoint talks. Everything else is untouched and unrecorded.
The grade is read from "grade C" / "grade=C" / "Grade C"; a quote that names it without the word (": C (close to its
date)") gets the two-rung ladder.

Learning: arm B's talks and deals say nothing of arm A's prices. Until the goods-ladder 809 I3 follow-up reached this
study (11/09 15:2x) the buyer memory's trades carried no arm, so arm B's ladder deals (59 of them 11:41-15:17) reached the
prices rule_choose, gem_names, my_avg_price, overpriced and item_stats read for both arms, and the shop chosen for arm
A's next order could move with them (prod: arm B's 16906 for Gef4d3036 at bjeon01 against arm A's 18489 at choo-chu),
and buyer_rank_pg and buyer_shop_profile read arm B's orders.jsonl rows (laddered). From that fix the trade carries
trade_arm, priced leaves arm B's out, hide_unmarked those written before it, without writing the memory again, and the
two shop learnings skip laddered rows. The ladder itself (what arm B offers) is unchanged.

Switch: .market/buyer_food_ladder.json {"enabled": true, "study": "<id>", "salt": "<salt>", "start": <epoch>,
"end": <epoch>}: on only from start until end (the study's ON time and its registry maximum), so arm B stops at end even
if nobody turns the switch off (goods-ladder review 809 I1, same shape). Missing, unreadable, without the window or
anything else is off, and off is the current talk exactly. BUYER_FOOD_LADDER=0 in the environment is off too. Read at
every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import math
import os
import re
import time

SWITCH = "buyer_food_ladder.json"
BASE = (0.9,)
LADDER = (0.8, 0.9)
LADDER_C = (0.7, 0.8, 0.9)
GRADE = re.compile(r"\bgrade\s*[=:]?\s*([ABC])(?![\w/])", re.I)
# the first study's trades before the arm was written into them (see hide_unmarked)
UNMARKED_STUDY = "food-ladder-1033"
UNMARKED_FROM = "2026-11-09T11:41:29"     # its switch went on at 11:41:29.279; trade times are local, to the second
_HIDDEN = {"orders": frozenset()}           # order ids hide_unmarked set for this run


def _epoch(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None   # Infinity: no stop


def _switch(md):
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def config(md, env=None, now=None):
    """{"study", "salt", "start", "end"} with the switch on and now in [start, end), else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_FOOD_LADDER") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    d = _switch(md)
    if d is None or d.get("enabled") is not True:
        return None
    study, salt, start, end = d.get("study"), d.get("salt"), _epoch(d.get("start")), _epoch(d.get("end"))
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    now = time.time() if now is None else now
    if start is None or end is None or not start <= now < end:
        return None
    return {"study": study, "salt": salt, "start": start, "end": end}


def arm(cfg, order_id):
    """"A" or "B" for an order id under the study's salt; None without a study or an id."""
    if not cfg or not isinstance(order_id, str) or not order_id:
        return None
    return "B" if hashlib.sha256(f"{cfg['salt']}:{order_id}".encode("utf-8", "surrogatepass")).digest()[0] % 2 else "A"


def grade(text):
    """The one grade a quote names ("grade C"), else None (none named, or more than one)."""
    found = {g.upper() for g in GRADE.findall(text or "")}
    return found.pop() if len(found) == 1 else None


def decide(md, order, q, steps, resume, *, probing, code_priced, env=None, now=None):
    """(steps for this talk, record or None). Never raises: any failure is the current talk, unrecorded."""
    try:
        order = order or {}
        q = q or {}
        ask = q.get("ask")
        fsh = order.get("_food_shelf")
        if (order.get("kind") != "food" or steps != BASE or not resume or probing or not code_priced
                or not isinstance(ask, (int, float)) or isinstance(ask, bool)
                or order.get("_one_won_default") or order.get("_food_quote_yield") or order.get("_b3") is not None
                or (isinstance(fsh, dict) and fsh.get("would_drop"))):
            return steps, None
        cfg = config(md, env, now)
        a = arm(cfg, order.get("order"))
        if a is None:
            return steps, None
        g = grade(q.get("text"))
        out = BASE if a == "A" else LADDER_C if g == "C" else LADDER
        return out, {"study": cfg["study"], "arm": a, "grade": g, "steps": list(out)}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return steps, None


def laddered(row):
    """Whether an orders.jsonl row is an arm B talk of this study: the shop learning that both arms read leaves it out."""
    g = row.get("food_ladder") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"


def trade_arm(order):
    """The arm market_buyer writes into the order's trade: "A", "B" or None (not in the study)."""
    g = (order or {}).get("_food_ladder")
    return g.get("arm") if isinstance(g, dict) else None


def hide_unmarked(md, trades):
    """Order ids of the trades written from 11:41:29 without an arm (before trades carried it) that may be arm B: by
    the first study's salt, read from the switch even when it is off; when it does not read, every such trade. The
    memory is not written again: priced leaves them out for this run. Set once a run, after the memory loads.
    Conservative: goods and ineligible food trades of that time hash to B too and leave price learning with them
    (prod 11:41-15:17: 304 hidden of 549 for the 66 arm B trades, none missed); with the switch gone or another study's
    in it, every unmarked trade since 11:41:29 stays hidden. Keep the 1033 salt in the switch file after the study ends."""
    out = set()
    try:
        d = _switch(md) if md else None
        salt = d.get("salt") if d and d.get("study") == UNMARKED_STUDY and isinstance(d.get("salt"), str) else None
        for t in trades or []:
            if not isinstance(t, dict) or "food_ladder" in t or not isinstance(t.get("order"), str) or not t["order"] \
                    or not isinstance(t.get("time"), str) or t["time"] < UNMARKED_FROM:
                continue
            if salt is None or arm({"salt": salt}, t["order"]) == "B":
                out.add(t["order"])
    except Exception:  # noqa: BLE001 - a run that cannot tell keeps the memory as it reads
        out = set()
    _HIDDEN["orders"] = frozenset(out)
    return _HIDDEN["orders"]


def priced(trades):
    """The trades whose prices arm A may learn from: without arm B's (marked, or hidden by hide_unmarked)."""
    hidden = _HIDDEN["orders"]
    return [t for t in trades if (t["food_ladder"] != "B" if "food_ladder" in t      # a marked trade: its mark
                                  else not (hidden and t.get("order") in hidden))]
