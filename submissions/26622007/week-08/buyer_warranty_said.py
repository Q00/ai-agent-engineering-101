"""A goods quote that names a 6 h or longer warranty is priced as one, not at the shop's 0 h history (B-W1,
W-20261109-1925-AXPro주기; review/W-20261109-1755-AXPro주기/buyer-candidates.md).

With buyer_short_warranty on, hours_for takes the least of what the quote said and the shop's history
(market_buyer.short_warranty_shops: the last goods receipt short is enough, taken as 0 h). warranty_hours_said reads a
quote that names 72 h as None ("leaves 6 h open"), so the history alone is left and a quote saying "grade A, market
certificate attached, 72 h warranty" was priced at p_max(0 h): 434 quotes in 426 orders in the 24 h to 11/09 19:30
asked at or under the 6 h cap (105% band) and were left over the 0 h one, 301 of those orders bought nothing
(ana/bw1_counterfactual.py). The same shops' goods sales in the ledger those 24 h: thisjonghoon 72 h 234, 6 h 127, none
55; yms979 72 h 2,967, none 222; to me only 72 h and 6 h. (The 434 is from the 120-character quote text orders.jsonl
keeps; the flag itself is read from the whole quote text. Analysis scripts: review/W-20261109-1925-AXPro주기/ana/.)

Arm B: a quote whose text names only warranties of 6 h or more, and no "no warranty"/0 h/shorter one, is priced as a
6 h deal (hours_for None) whatever the history. Read conservatively: a sentence that names the warranty with a negation
or an option ("No 72 h warranty", "optional 72 h warranty", "available for +12%", "void", "보증 미적용") or a 0 h
anywhere in the whole text leaves the quote as now (independent review 08b282d I1). The receipt check is not touched: a receipt under 6 h still goes to
market_buyer.warranty_short and buyer_short_warranty.receipt_check at its own hours, so a shop that says 72 h and ships
0 h is paid only under p_max(0 h), as now. Arm A, off and every other quote: as before.

Switch: .market/buyer_warranty_said.json {"enabled": true, "study": "...", "salt": "...", "share": 0.5,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS; missing,
unreadable, a bad field or outside the window is off, and off is the old behaviour exactly. BUYER_WARRANTY_SAID=0 in the
environment is off too. Read once per order (a buyer run is one order), so no restart either way. Arms by
sha1(salt|order) < share, a hash axis of its own (not saved-floor's or goods-grade's salt).

On, it moves what other goods studies see: buyer_goods_grade.adjust runs only when hours_for is None, and the chosen
shop changes. So it is off while buyer_goods_grade is on (state), and starts after the goods studies end.

Kept apart from arm A (review 08b282d I2, as buyer_saved_floor): an arm B orders row (said_b) is not learned by
recent_asks, buyer_rank_pg or buyer_shop_profile, and an arm B trade inside its window is not read by
short_warranty_shops, so arm B's deals and its 0 h receipts change neither arm's 0 h history (each arm sees arm A's);
after stop_at the trade counts again as any other. Measure (I3): every goods order with the switch on carries
"warranty_said" (both arms): the hash arm ("assigned") and the arm used ("arm"), the shops a quote would change
("eligible", a post-quote variable: arm B may close the quote wait sooner), the chosen shop, "applied", "refused"
(the receipt fell short) and "cap0" (market_buyer.short_cap of the chosen shop as arm A would have it). The verdict is
ITT over every goods order of the window, by assigned arm, not over "eligible" orders.

Stop (I4): arm B buys at the 6 h price from shops that ship 0 h on some receipts (thisjonghoon 55 of 416, yms979 222
of 3,189 goods sales in the ledger); the receipt check refuses those as now, but more often. Each applied arm B order
that got a receipt appends one line to EVENTS (with the window's start_at, so a reused study name never counts old
lines); when TRIP_MIN or more of those receipts were refused and the refused share is over TRIP_SHARE, every later
order of the window goes to arm A ("tripped" in its row and the log): the old behaviour, recorded, not silent. Orders
already running when it trips finish in arm B, so the stop can come a few receipts late.

Read conservatively on purpose: a duration under 6 h anywhere in the text ("Delivered in 2 h", "Reply within 5 min")
also leaves the quote as now; in the 24 h to 11/09 20:05 that took no quote the old reading set aside (the 70 that
flipped were stored text cut at "if it", read whole they are set aside; ana/bw1_said_replay.py).
Undo: delete the switch (or BUYER_WARRANTY_SAID=0). Arm B trades already made stay out of short_warranty_shops until
their stop_at (at most MAX_WINDOW_HOURS), as they would have in the window.
"""
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import ask_read

SWITCH = "buyer_warranty_said.json"
EVENTS = "buyer_warranty_said_events.jsonl"
MAX_WINDOW_HOURS = 12
TRIP_MIN = 4
TRIP_SHARE = 0.25     # about twice the highest shop's 0 h share of goods sales (thisjonghoon 55/416, 13%)
VERSION = "bw1-6"
# a sentence naming the warranty with any of these is not "names only 6 h or more" (review 08b282d I1)
WARRANTY_WORD = re.compile(r"warrant|guarantee|safe-care|보증|케어|보장", re.I)
# any duration under 6 h anywhere ("1 h for this price", "0.5 h for grade C", "30 min"): review 2 m3
WORD_DURATION = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(ask_read.WORD_N, key=len, reverse=True))
                           + r")[ \t-]*(h|hrs?|hours?|mins?|minutes?)(?![a-z])", re.I)
# "within an hour", "half an hour", "a few hours" (review 4 m4)
FEW_HOURS = re.compile(r"\b(?:an?|half an?|a half|half|a few|few|several|a couple(?: of)?|couple(?: of)?)[ \t-]+"
                       r"(?:hours?|hrs?|minutes?|mins?)\b", re.I)
# "72 h warranty for grade A; this unit is grade C": the warranty may be another grade's (11/09 Pro 1930 3-1)
# lists (A/B, A and C, A-C), "grade-C", "C grade" (capital only: "a grade C unit" is an article): review 6 I1/I2
GRADE = re.compile(r"\bgrades?[ \t:-]*([abc])(?:[ \t]*(?:/|,|&|and|or|to|-)[ \t]*([abc]))?\b"
                   r"|\b(?-i:([ABC]))[ \t]*(?:/|,|&|and|or|to|-)[ \t]*(?-i:([ABC]))[ \t-]?(?=grades?\b)"
                   r"|\b(?-i:([ABC]))[ \t-]?(?=grades?\b)|\b([abc])-(?=grade\b)|(?<![a-z])([abc])[ \t]?등급", re.I)
DURATION = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)[ \t-]*(h|hrs?|hours?|min|mins|minutes?|시간|분)(?![a-z])", re.I)
# "if it breaks" is the market's own warranty wording, not a condition; a Korean hedge counts only next to the
# warranty word (product names say "먼지없는", "색상선택": 11/09 replay, ana/bw1_said_replay.py)
HEDGE = re.compile(r"\b(?:no|not|non|without|exclud\w*|optional|options?|available|upgrade\w*|extra|surcharge|request|"
                   r"void|expired?|zero|none|free|limited|unless|except|only for|waiv\w*)\b|\bif\b(?! it br)|n't|"
                   r"(?:보증|케어|보장)\S*\s*(?:미적용|미포함|불가|없|제외|별도|옵션|추가|선택|유료)", re.I)
ZERO_H = re.compile(r"(?<![\d.,])0(?:\.0+)?[ \t-]*(?:h|hrs?|hours?|시간)(?![a-z])|\bzero[ -]?hours?\b", re.I)
SENTENCE = re.compile(r"[.;!?\n]+")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _good(d):
    return (isinstance(d, dict) and d.get("enabled") is True
            and isinstance(d.get("study"), str) and d["study"] and isinstance(d.get("salt"), str) and d["salt"]
            and _num(d.get("share")) and 0 < d["share"] < 1 and _window(d.get("start_at"), d.get("stop_at")))


def config(md, env=None, now=None):
    """{"study", "salt", "share", "start_at", "stop_at"} when the switch is on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("BUYER_WARRANTY_SAID") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        d = json.loads((Path(md) / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not _good(d) or not d["start_at"] <= now < d["stop_at"]:
        return None
    return {k: d[k] for k in ("study", "salt", "share", "start_at", "stop_at")}


def arm(salt, order_id, share):
    h = int(hashlib.sha1(f"{salt}|{order_id}".encode()).hexdigest()[:8], 16) / 0x100000000
    return "B" if h < share else "A"


def state(md, order_id, env=None, now=None):
    """None when off; else {"study", "v", "arm", "start_at", "stop_at"}. Never raises into the buyer run."""
    try:
        c = config(md, env, now)
        if c is None or not order_id:
            return None
        import buyer_goods_grade
        if buyer_goods_grade.config(md, env, now) is not None:     # its adjust runs only when hours_for is None
            return None
        a = arm(c["salt"], order_id, c["share"])
        s = {"study": c["study"], "v": VERSION, "assigned": a, "arm": a,
             "start_at": c["start_at"], "stop_at": c["stop_at"]}
        trip = tripped(md, c["study"], c["start_at"])
        if trip:
            s.update(arm="A", tripped=trip)
        return s
    except Exception:  # noqa: BLE001 - the switch never ends the run: off
        return None


def _events(md, study, start_at):
    out = []
    try:
        with open(Path(md) / EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("study") == study and e.get("start_at") == start_at:
                    out.append(e)
    except OSError:
        pass
    return out


def tripped(md, study, start_at):
    """{"receipts", "refused"} once the window's applied arm B receipts were refused too often (TRIP_MIN, TRIP_SHARE),
    else None."""
    ev = _events(md, study, start_at)
    n, k = len(ev), sum(1 for e in ev if e.get("refused") is True)
    return {"receipts": n, "refused": k} if k >= TRIP_MIN and k / n > TRIP_SHARE else None


def note(md, said, refused, now=None):
    """Append one applied arm B receipt's outcome to EVENTS (one short O_APPEND write, 0600)."""
    line = json.dumps({"study": said["study"], "v": said.get("v"), "start_at": said.get("start_at"),
                       "refused": bool(refused), "ts": time.time() if now is None else now}, sort_keys=True) + "\n"
    fd = os.open(Path(md) / EVENTS, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def stated_ok(text):
    """True when the goods text names a warranty and every one it names is 6 h or more, with no "no warranty", 0 h or
    "as is" anywhere (ask_read's readings): "grade A, market certificate attached, 72 h warranty"."""
    try:
        return _stated_ok(text)
    except Exception:  # noqa: BLE001 - a reading never drops the quote: not set aside
        return False


def _stated_ok(text):
    raw = text or ""
    if ZERO_H.search(raw) or ask_read.NO_WARRANTY.search(raw):
        return False
    for m in DURATION.finditer(raw):
        v = float(m[1])
        if (v / 60 if m[2].lower().startswith("min") or m[2] == "분" else v) < ask_read.GOODS_WARRANTY_HOURS:
            return False
    for m in WORD_DURATION.finditer(raw):
        v = ask_read.WORD_N[m[1].lower()]
        if (v / 60 if m[2].lower().startswith("m") else v) < ask_read.GOODS_WARRANTY_HOURS:
            return False
    if FEW_HOURS.search(raw) or len({c.lower() for m in GRADE.finditer(raw) for c in "".join(m.groups(""))}) > 1:
        return False
    # a piece with no word in it ("-", "…") is not the next sentence (review 4 m3)
    sentences = [s for s in SENTENCE.split(raw) if re.search(r"\w", s)]
    # the warranty's sentence and the next one ("72 h warranty. This price ships without one.")
    if any(WARRANTY_WORD.search(s) and HEDGE.search(s + " " + (sentences[i + 1] if i + 1 < len(sentences) else ""))
           for i, s in enumerate(sentences)):
        return False
    t = ask_read.SAFE_RE.sub(ask_read._keep_stop, raw)
    if ask_read.NO_WARRANTY.search(t) or ask_read.short_warranty_hours(text) is not None:
        return False
    hours = []
    for m in ask_read.WARRANTY_HOURS.finditer(t):
        n, u = (m["n"], m["u"]) if m["n"] else (m["n2"], m["u2"])
        v = ask_read.WORD_N[n.lower()] if n.lower() in ask_read.WORD_N else float(n)
        hours.append(v * 24 if u.lower().startswith("day") else v)
    return bool(hours) and min(hours) >= ask_read.GOODS_WARRANTY_HOURS


def applies(sw, quote):
    """Whether this quote is priced as a 6 h deal by arm B (buyer_short_warranty.hours_for)."""
    said = (sw or {}).get("said")
    q = quote or {}
    return isinstance(said, dict) and said.get("arm") == "B" and q.get("warranty_said_ok") is True \
        and q.get("warranty_h") is None


def measure(md, sw, quotes, chosen, receipt, short, cap0):
    """The orders row's "warranty_said" for this order (record), noting an applied arm B receipt for the stop rule."""
    said = record(sw, quotes, chosen, short, cap0)
    if said and said["applied"] and receipt is not None:
        try:
            note(md, said, short)
        except OSError as e:       # the row is kept either way (review 3 m-A); the stop then counts one receipt less
            said["note_error"] = type(e).__name__
    return said


def record(sw, quotes, chosen=None, refused=None, cap0=None):
    """The measure record for an order with the switch on (both arms): the arm, the shops whose 0 h history a quote
    naming 6 h or more would set aside, the chosen shop, whether arm B priced it so ("applied"), whether its receipt
    fell short ("refused") and its cap at the 0 h history ("cap0"), else None."""
    said = (sw or {}).get("said")
    if not isinstance(said, dict):
        return None
    hist = (sw or {}).get("hist") or {}
    eligible = sorted(name for name, q in (quotes or {}).items()
                      if isinstance(q, dict) and q.get("warranty_said_ok") is True and q.get("warranty_h") is None
                      and hist.get(name) is not None)
    chosen_eligible = chosen in eligible
    return {**said, "eligible": eligible, "chosen": chosen, "chosen_eligible": chosen_eligible,
            "applied": said.get("arm") == "B" and chosen_eligible, "refused": bool(refused),
            "cap0": cap0 if chosen_eligible else None}


def said_b(row):
    """Whether an orders.jsonl row is an arm B order: the learning both arms read leaves it out."""
    g = row.get("warranty_said") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"


def trade_mark(order):
    """What market_buyer writes into the order's trade: {"arm", "stop_at"}, or None (not in the study)."""
    g = ((order or {}).get("_sw") or {}).get("said")
    return {"arm": g.get("arm"), "stop_at": g.get("stop_at")} if isinstance(g, dict) else None


def held_out(trade, now):
    """Whether short_warranty_shops leaves this trade out: an arm B trade while its window is open."""
    g = (trade or {}).get("warranty_said")
    return isinstance(g, dict) and g.get("arm") == "B" and _num(g.get("stop_at")) and now < g["stop_at"]


def switch_on(md, *, study, salt, start_at, hours, share=0.5):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "start_at": start_at,
         "stop_at": start_at + hours * 3600 if _num(hours) and _num(start_at) else None}
    if not _good(d):
        raise ValueError("buyer_warranty_said: bad switch")
    path = Path(md) / SWITCH
    tmp = path.with_name(f"{SWITCH}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return d
