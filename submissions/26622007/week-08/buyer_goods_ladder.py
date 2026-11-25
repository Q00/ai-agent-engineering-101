"""Goods: open lower than 0.9 of the first ask, then climb back to it (review/W-20261109-1338-AXPro주기/report.md).

saved is half of buyer ALL and counts every deal by the share of list price it keeps, goods and food alike. In the last
24 h (11/09 13:4x, .market/measure/orders.jsonl joined to payments.jsonl paid) 162 of our 490 paid goods talks ended
with the shop taking our one 0.9 offer as it was: ddolcom 79/132, 25620027 36/57, yms979 30/33, yeonsong2 10/22. Had a
0.8 offer been taken there, each of those deals would have kept .0635 more of list price. buyer_food_ladder runs the
same ladder on food; this is the goods half, on its own switch and salt so the two studies are independent.

Arm B prices the talk with the ladder (0.80, 0.90). The limit, the accept band and oneshot.code_turn's rules stay as
they are, so the last rung is today's 0.9 and the worst case is today's price after one more turn. Arm A keeps (0.9,).
Both arms are recorded in orders.jsonl (an order outside the study records null) and, from the 809 review fix on, in
the trade the buyer memory keeps ("goods_ladder": "A" | "B" | null). Arm B's talks are left out of the learning both
arms read: the shop profiles in orders.jsonl (buyer_shop_profile, buyer_rank_pg: laddered), as buyer_goods_grade.lowered
does, and the prices of the memory's trades (rule_choose, gem_names, my_avg_price, overpriced, item_stats: priced).
Until that fix (review 809 I3) the trades carried no arm, so arm B's deals moved arm A's shop choice from 14:16 on
(prod memory 14:16-14:4x: 15 arm B deals); hide_unmarked leaves those out without writing the memory again.

Eligible: a goods talk priced by the one-step default (steps == (0.9,)), resumed from a quote whose opening code
prices (code_priced: buyer_goods_first_one.first_decision, the goods opening rule), with a numeric ask, no opening
probe of its own (goods first-one, goods extra quote, quote alignment) and without the B3 hold rung (buyer_b3b1 acts
only after a single 0.9 offer). Everything else is untouched.

Switch: .market/buyer_goods_ladder.json {"enabled": true, "study": "<id>", "salt": "<salt>", "start": <epoch>,
"end": <epoch>}: on only from start until end (the study's ON time and its registry maximum, review 809 I1), so arm B
stops at end even if nobody turns the switch off. Missing, unreadable, without the window or anything else is off, and
off is the current talk exactly. BUYER_GOODS_LADDER=0 in the environment is off too. Read at every order, so no restart
either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import math
import os
import time

SWITCH = "buyer_goods_ladder.json"
BASE = (0.9,)
LADDER = (0.8, 0.9)
# the first study's trades before the arm was written into them (see hide_unmarked)
UNMARKED_STUDY = "goods-ladder-1338"
UNMARKED_FROM = "2026-11-09T14:16:00"     # its switch went on at 14:16:00.055; trade times are local, to the second
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
    if str(env.get("BUYER_GOODS_LADDER") or "").strip().lower() in ("0", "false", "no", "off"):
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


def decide(md, order, q, steps, resume, *, probing, code_priced, env=None, now=None):
    """(steps for this talk, record or None). Never raises: any failure is the current talk, unrecorded."""
    try:
        order = order or {}
        q = q or {}
        ask = q.get("ask")
        if (order.get("kind") != "goods" or steps != BASE or not resume or probing
                or not isinstance(ask, (int, float)) or isinstance(ask, bool)
                or order.get("_b3") is not None):
            return steps, None
        cfg = config(md, env, now)
        a = arm(cfg, order.get("order"))
        if a is None:
            return steps, None
        if not (code_priced() if callable(code_priced) else code_priced):   # read only with the study on
            return steps, None
        out = BASE if a == "A" else LADDER
        return out, {"study": cfg["study"], "arm": a, "steps": list(out)}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return steps, None


def laddered(row):
    """Whether an orders.jsonl row is an arm B talk of this study: the shop learning that both arms read leaves it out."""
    g = row.get("goods_ladder") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"


def trade_arm(order):
    """The arm market_buyer writes into the order's trade: "A", "B" or None (not in the study)."""
    g = (order or {}).get("_goods_ladder")
    return g.get("arm") if isinstance(g, dict) else None


def hide_unmarked(md, trades):
    """Order ids of the trades written from 14:16:00 without an arm (before trades carried it) that may be arm B: by
    the first study's salt, read from the switch even when it is off; when it does not read, every such trade. The
    memory is not written again: priced leaves them out for this run. Set once a run, after the memory loads.
    Conservative: food and ineligible goods trades of that hour hash to B too and leave price learning with them
    (prod 14:16-14:4x: 36 hidden for 21 arm B); with the switch gone or another study's in it, every unmarked trade
    since 14:16 stays hidden. Keep the 1338 salt in the switch file after the study ends."""
    out = set()
    try:
        d = _switch(md) if md else None
        salt = d.get("salt") if d and d.get("study") == UNMARKED_STUDY and isinstance(d.get("salt"), str) else None
        for t in trades or []:
            if not isinstance(t, dict) or "goods_ladder" in t or not isinstance(t.get("order"), str) or not t["order"] \
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
    return [t for t in trades if (t["goods_ladder"] != "B" if "goods_ladder" in t      # a marked trade: its mark
                                  else not (hidden and t.get("order") in hidden))]
