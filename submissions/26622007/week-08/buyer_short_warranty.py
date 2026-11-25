"""Goods with a warranty shorter than 6 h, bought only under an expected-value cap (owner 11/08 17:55, telegram 7955
"일단 너가 찾은 수식으로 해"; review/W-20261108-1755-AX짧은보증수식/report.md).

The market scores a goods deal 0 when the unit breaks under the warranty (refunded) and -1 when it breaks outside it
(agent.md 229: "a deal that served nothing counts -1, or 0 if refunded"; 11/08 19:09 correction of -price/list).
Breaks come within 0-6 h of the sale, evenly (about 10,000 market breaks, 11 after 6 h), so with a warranty of h hours a
unit that breaks (chance b) breaks outside it with q = b * max(0, 6 - h) / 6. A deal at p = price/list keeps in
expectation E = (1 - b)(1 - p) - q, and it is bought only when E >= S, my current Saved (the board's, else 0.30):

    p_max(h) = 1 - (S + q) / (1 - b)

(none when 1 - b <= 0 or p_max <= 0). At h = 6 that is 1 - S / (1 - b), the cap a 6 h goods deal already has
(market_buyer.saved_cap). E.g. b = .072, S = .2955: 6 h 68.16%, 3 h 64.28%, 0 h 60.40%.

A safety rail on top: the expected out-of-warranty loss of the short-warranty deals passed to the kit in the last hour,
sum q * price, stays under EXPOSURE_SHARE of the cash buffer (the switch file's cash_buffer, else
hard_limits.STOP_BUYING_BELOW, the buffer's floor). Each deal reserves its loss in EXPOSURE_FILE under a lock before it
goes to the kit; a reservation that cannot be read or written pays nothing. The accept band (plan A, 105%) does not
apply to these deals: p_max(h) is the ceiling. The kit may ship another acceptable SKU at the agreed price, so p and b
are taken at the worst SKU the shop can ship for the order (the cheapest list price, the highest break chance). The
shop also picks the unit, and a C unit breaks far more often than the SKU's mean (refunded under a 6 h warranty, not
here): only the grades whose own break chance keeps p <= p_max(h) are accepted (the kit checks the unit's certificate
against accept.grades), and b is the worst of those.

No warranty at all is never bought (fixed rule: no goods without a warranty; decision ax-0h-nonfood-block, 11/09 22:44).
The switch used to price a unit expected with none at p_max(0 h) and pay a 0 h or unnamed receipt under it (9 such
receipts passed to the kit 11/08-11/09 12:09). "None" is exactly what receipt_check used to price as 0 h: 0, a
negative, missing or not a finite number (no_warranty); any finite h over 0 is a short warranty, priced as before (no
new least warranty here). Before any price: a quote that says none, or a shop whose recent short receipts to me
included one with none (zero_hist) with a quote that names no warranty hours, is refused and no talk is started
(zero_expected; market_buyer.main checks it before value_limit, saved_cap, accept_percent and short_cap, and short_cap
gives 0 for it without pricing); a talk text that says none ends the talk; and receipt_check refuses such a receipt
before pricing or reserving. p_max(h) is not touched. A quote naming only 6 h or more (buyer_warranty_said.stated_ok)
is never blocked by the history, in any arm; one naming 0 < h < 6 is priced as before, and a shop whose short receipts
all named hours over 0 is priced at its 0 h history as before.

Switch: .market/buyer_short_warranty.json {"enabled": true, "cash_buffer": <won>, "block_zero_nonfood": true}; missing,
unreadable or anything else is off, and off is the old behaviour exactly (6 h or no deal). BUYER_SHORT_WARRANTY=0 in the
environment is off too. block_zero_nonfood is on unless it is JSON false (the rollback: the 11/08 behaviour exactly).
Read at every order, so no restart either way.
"""
import fcntl
import json
import os
import time

import buyer_warranty_said
import hard_limits

SWITCH = "buyer_short_warranty.json"
BREAK_HOURS = 6                 # a unit breaks within this many hours of the sale (hard_limits.BUYER_GOODS_WARRANTY_HOURS)
FALLBACK_SAVED = 0.30           # S when the board cannot be read (market_buyer.SAVED_MIN_SHARE)
FALLBACK_BREAK = 0.0837         # b when the ledger gives none: p90 of 2,089 goods SKUs (median .0718)
BOARD_MAX_AGE = 3 * 3600        # a board snapshot older than this is not my Saved now
BOARD_TAIL = 200000             # bytes of board_history.jsonl read for the last snapshot
EXPOSURE_SHARE = 0.02           # sum q * price of the last hour's short-warranty deals <= this share of the cash buffer
EXPOSURE_SECONDS = 3600
BUFFER_MAX = 40000000           # a switch-file cash_buffer above this is cut to it (11/08 18:06 restock: 34.5M)
GRADES = ("A", "B", "C")
EXPOSURE_FILE = "buyer_short_warranty_exposure.jsonl"    # one line per short-warranty deal passed to the kit
ME = ("nocha12",)               # my buyer's name on the board, compared lowercased
BLOCK_ZERO = "block_zero_nonfood"   # switch field; only JSON false turns the no-warranty block off
ZERO_QUOTE = "no warranty expected: the quote says none"
ZERO_HIST = "no warranty expected: the shop's recent goods receipts to me had none"


def config(md, env=None):
    """The switch file as a dict when it is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_SHORT_WARRANTY") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def number(v):
    """hard_limits.number, and an int past float range is not a number here: math.isfinite raises OverflowError on it, and
    a receipt's warranty_hours of -10**400 ended the order in no_warranty outside any try (no result, no trade, no row).
    hard_limits.py itself stays as it is (seller source hashes read it)."""
    try:
        return hard_limits.number(v)
    except OverflowError:
        return False


def outside_chance(b, h):
    """q: the chance a unit breaks after a warranty of h hours."""
    h = h if number(h) and h > 0 else 0
    return b * max(0.0, BREAK_HOURS - h) / BREAK_HOURS


def p_max(h, b, S):
    """The highest price/list at which a deal with an h-hour warranty keeps S in expectation; 0 when none does."""
    b = min(max(b if number(b) else FALLBACK_BREAK, 0.0), 1.0)
    q = outside_chance(b, h)
    if 1 - b <= 0:
        return 0.0
    return max(0.0, 1 - (S + q) / (1 - b))


def expected(p, h, b):
    """E[x] = (1 - b)(1 - p) - q: the share of list a deal at p keeps in expectation (a break outside the warranty
    counts -1, agent.md 229)."""
    return (1 - b) * (1 - p) - outside_chance(b, h)


def cap(list_price, h, b, S):
    """The most I pay for a unit of list_price with an h-hour warranty (whole won, rounded down)."""
    if not number(list_price) or list_price <= 0:
        return 0
    return int(list_price * p_max(h, b, S))


def board_saved(md, now=None):
    """My Saved on the newest board snapshot (.market/board_history.jsonl, buyers: name -> [saved, forum, all]), or
    FALLBACK_SAVED when it is missing, older than BOARD_MAX_AGE or unreadable."""
    now = now or time.time()
    try:
        path = os.path.join(md, "board_history.jsonl")
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - BOARD_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not number(d.get("ts")) or not -300 <= now - d["ts"] <= BOARD_MAX_AGE:
                return FALLBACK_SAVED
            for name, row in (d.get("buyers") or {}).items():
                if str(name).lower() in ME and isinstance(row, list) and row and number(row[0]) and 0 < row[0] < 1:
                    return float(row[0])
            return FALLBACK_SAVED
    except OSError:
        pass
    return FALLBACK_SAVED


def state(md, order, now=None, env=None):
    """For a goods order with the switch on: {"S", "buffer", "hist": {shop: hours}}, kept on the order; else None."""
    if (order or {}).get("kind") != "goods":
        return None
    cfg = config(md, env)
    if cfg is None:
        return None
    buf = cfg.get("cash_buffer")
    return {"S": board_saved(md, now),
            "buffer": min(buf, BUFFER_MAX) if number(buf) and buf > 0 else hard_limits.STOP_BUYING_BELOW,
            "hist": {}, "block_zero": cfg.get(BLOCK_ZERO) is not False}


def block_zero(sw):
    """Whether goods with no warranty are refused on this order: the switch on and block_zero not False (a state without
    the key blocks: fails closed)."""
    return isinstance(sw, dict) and sw.get("block_zero", True) is not False


def no_warranty(w):
    """Whether warranty hours w are none: 0, negative, missing or not a finite number (nan, inf, a bool, a string), the
    hours receipt_check priced as 0 h before the block. Any finite w > 0 is a warranty (0.5 h too)."""
    return not (number(w) and w > 0)


def zero_expected(sw, shop, quote=None):
    """Why this shop's goods unit is expected with no warranty, so it is refused before any price, else None: the quote
    says none (no_warranty: 0 or negative), or names no warranty hours (neither short nor 6 h or more) while the shop is
    in the short history (hist, taken as 0 h) and its recent short receipts to me included one with none (zero_hist;
    a state without zero_hist reads every hist shop so: fails closed). A quote naming 0 < h < 6 is priced as before
    (hours_for), and one naming only 6 h or more is never blocked by the history (buyer_warranty_said, any arm or off)."""
    if not block_zero(sw):
        return None
    q = quote or {}
    w = q.get("warranty_h")
    if number(w):
        return ZERO_QUOTE if no_warranty(w) else None
    if q.get("warranty_said_ok") is True:
        return None
    h = (sw.get("hist") or {}).get(shop)
    if not (number(h) and h <= 0):
        return None
    zs = sw.get("zero_hist")
    if isinstance(zs, (list, tuple, set, frozenset)) and shop not in zs:
        return None
    return ZERO_HIST


def hours_for(sw, shop, quote=None):
    """The warranty hours I expect from shop on this order, when under BREAK_HOURS: the least of what its quote said and
    what its recent receipts showed (market_buyer.short_warranty_shops, taken as 0 h). None: a 6 h one, as usual.
    buyer_warranty_said arm B: a quote naming only 6 h or more is a 6 h one whatever the history (the receipt is still
    checked at its own hours)."""
    if not sw:
        return None
    if buyer_warranty_said.applies(sw, quote):
        return None
    hs = [h for h in ((quote or {}).get("warranty_h"), (sw.get("hist") or {}).get(shop)) if number(h)]
    h = min(hs) if hs else None
    return h if h is not None and h < BREAK_HOURS else None


def reserve(md, loss, room, order=None, now=None):
    """(ok, used): under a lock, the last hour's reserved loss in EXPOSURE_FILE, and when loss fits under room the
    reservation is written before the deal goes to the kit. Raises when the file cannot be read or written, so the
    caller pays nothing (fails closed); a line that does not parse is an error too."""
    now = now or time.time()
    path = os.path.join(md, EXPOSURE_FILE)
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            used = 0.0
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        d = json.loads(line)          # ValueError: unknown exposure, pay nothing
                        if not number(d.get("ts")) or not number(d.get("loss")):
                            raise ValueError("an exposure line without ts or loss")
                        if now - d["ts"] <= EXPOSURE_SECONDS:     # a line from "the future" (clock, rounding) counts
                            used += d["loss"]
            if used + loss > room:
                return False, used
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(now, 1), "loss": round(loss, 1), "order": order}) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return True, used
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def worst(prices, chances):
    """(list price, b) the check uses: the cheapest list price and the highest break chance of the SKUs the shop may
    ship. A chance of None is FALLBACK_BREAK; one over 1 is 1; under 0 is FALLBACK_BREAK."""
    lp = min((p for p in prices if number(p) and p > 0), default=None)
    bs = [min(c, 1.0) if number(c) and c >= 0 else FALLBACK_BREAK for c in chances] or [FALLBACK_BREAK]
    return lp, max(bs)


def grades_ok(price, list_price, h, S, b_by_grade):
    """(grades, b): the grades at whose break chance a deal at price keeps S with an h-hour warranty, and the highest
    break chance among them. b_by_grade: {grade: b or None}. ([], None) when none does."""
    if not number(price) or not number(list_price) or list_price <= 0:
        return [], None
    p = price / list_price
    ok = [(g, min(b, 1.0) if number(b) and b >= 0 else FALLBACK_BREAK) for g, b in sorted(b_by_grade.items())]
    ok = [(g, b) for g, b in ok if p <= p_max(h, b, S) and p_max(h, b, S) > 0]
    return [g for g, _ in ok], max((b for _, b in ok), default=None)


def receipt_check(sw, md, rec, list_price, b, order=None, now=None):
    """(ok, record, why) for a goods receipt whose warranty is short: ok when p <= p_max(h) and the hour's exposure
    leaves room for it (then reserved). record goes on the order line (short_warranty_buy) either way. list_price and
    b: worst() over the SKUs the shop may ship."""
    w = rec.get("warranty_hours")
    h = w if number(w) and w > 0 else 0
    if block_zero(sw) and no_warranty(w):     # none: never bought, before any price or reservation
        return False, {"h": 0, "ok": False, "blocked": "no_warranty"}, "no warranty: goods are not bought without one"
    price = rec.get("price")
    b = min(b, 1.0) if number(b) and b >= 0 else FALLBACK_BREAK
    S = sw["S"]
    pm = p_max(h, b, S)
    if not number(price) or not number(list_price) or list_price <= 0:
        return False, None, "no price or list price to check a short warranty against"
    p = price / list_price
    loss = outside_chance(b, h) * price
    rec_ = {"h": h, "b": round(b, 4), "S": round(S, 4), "p_max": round(pm, 4), "p": round(p, 4),
            "list": list_price, "loss": round(loss, 1)}
    if p > pm or pm <= 0:
        return False, dict(rec_, ok=False), f"warranty {h:g} h: price {p:.1%} of list is over p_max {pm:.1%}"
    room = EXPOSURE_SHARE * sw["buffer"]
    try:
        ok, used = reserve(md, loss, room, order, now)
    except Exception as e:  # noqa: BLE001 - an exposure that cannot be read pays nothing
        return False, dict(rec_, ok=False), f"warranty {h:g} h: exposure unknown ({type(e).__name__})"
    rec_["exposure"] = round(used, 1)
    if not ok:
        return False, dict(rec_, ok=False), f"warranty {h:g} h: the hour's expected loss {used + loss:.0f} would pass {room:.0f}"
    return True, dict(rec_, ok=True), None
