"""The margin cap take where any turn would walk away: code, FAST or the harness (W-20261115-1530).

The take (buyer_margin_cap.take_top, 3f4cab9) reached a final ask only through buyer_accept_band.turn in market_buyer's
ladder, which sees the turns code priced. A shop text no TEMPLATE nor ask_read reads goes to the harness, and the
harness knows only my limit: 13:49:04-07 usagi asked 231698 (cap 231985, limit 212055), wrote "Counteroffer: 231698.
This is my lowest price.", the harness offered 212055 and then walked away, and we cancelled the talk
(logs/market-kit.log.1 151002-151019). In the hour after the take went on, 16-20 talks ended so with the last ask over
my limit and within the cap (review/W-20261115-1430-AX근본분석, both analyses), about 20 an hour.

buyer.talk calls the hook at its one walk-away point, whoever decided the walk. The ask taken is the shop's own: the
message metadata, or a number read from the shop's text by rule (verified_ask), and it must be the number the turn
recorded; the harness's number alone is never taken. Then the same test as the band's take: my steps spent (I offered my limit),
over my limit, at or under the take ceiling (min(cap, value_limit): the budget and the deal cap), over my last offer,
and not an ask the shop already turned down (my last offer was that ask). A HOLD (buyer.talk) goes first. A harness
turn that repeats my last offer to the ask the shop holds (the talk would only run out its turns) gets the same test.
No text that can mean another unit (a grade, refurbished, box-damaged, display model: OTHER_UNIT) is read: the cap was
priced for the quoted grade and the receipt check does not check the grade. My own offers stay as they were. The receipt is checked after the talk
as for any take (market_buyer: the asked SKU up to the same ceiling, the warranty, the signature, hard_limits in the kit).
"""
import re

import ask_read
import oneshot

# a whole number in the text ("231698", "231,698"), not part of a word or a decimal
NUMBER = re.compile(r"(?<![0-9A-Za-z.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?![0-9A-Za-z]|[.,][0-9])")
_WORD_DIGITS = re.compile(r"[0-9]")
# words that can mean another unit than the one quoted (a grade swap, a refurbished or box-damaged one): not read here,
# the receipt check does not check the grade the cap was priced for
OTHER_UNIT = re.compile(r"\bgrades?\b|refurb|box|damaged|condition|\bmodel\b|sample|display|\brent|\blease|empty|used\b|등급|리퍼|박스|훼손", re.I)


def verified_ask(meta, text, history, goods=False):
    """The shop's current ask from its own message, or None. history holds the turns before this message. The metadata
    ask, else oneshot.text_price (a TEMPLATE text or ask_read), else one number left once the fields the shops write
    without a price are cut (ask_read SAFE and BENIGN: SKU, unit id, hours, offers left, list price) and my own offers
    named back are set aside, standing after an ask word (ask_read.ANCHORS), with no word left that can make it another
    price (ask_read.DANGER: was, now, each, other, minus, a number in words, ...) or another unit (OTHER_UNIT). Never over the shop's earlier asks and always over my last offer. Goods whose text
    says no warranty are never read, metadata or not (the kit pays goods only with the warranty)."""
    if goods and ask_read.no_warranty(text or ""):
        return None
    if OTHER_UNIT.search(ask_read.BENIGN_RE.sub(" ", ask_read.SAFE_RE.sub(" ", text or ""))):
        return None                       # maybe another unit than the one the cap was priced for
    mine = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    if not mine:
        return None
    if meta is not None:
        p = meta if type(meta) is int else None
    else:
        p = oneshot.text_price(text, history, None, goods) if asks else None
        if p is None:
            p = _one_number(text or "", mine)
    if p is None or p <= mine[-1] or (asks and p > min(asks)):
        return None
    return p


def _one_number(text, mine):
    if not text or len(text) > ask_read.MAX_CHARS:
        return None
    t = ask_read.SAFE_RE.sub(" ", text)
    t = ask_read.BENIGN_RE.sub(" ", t)
    if ask_read.DANGER.search(t):
        return None
    anchored = {int(m.group("p").replace(",", "")) for a in ask_read.ANCHORS for m in a.finditer(t)}
    found = {int(n.replace(",", "")) for n in NUMBER.findall(t)}
    if _WORD_DIGITS.search(NUMBER.sub(" ", t)):    # a digit inside a word (a model name, "2x"): not read
        return None
    left = found - set(mine)
    if len(left) != 1:
        return None
    p = left.pop()
    if p not in anchored:                # the number stands after an ask word ("Counteroffer: 231698.")
        return None
    return p if p > 0 else None


def decide(ask, meta, text, history, limit, top, goods=False):
    """The price to offer instead of walking away, or None to walk. ask is the turn's recorded ask, history the turns
    before it."""
    if type(top) is not int or type(limit) is not int or top <= limit or type(ask) is not int:
        return None
    if history[-2:] == [("shop asked", ask), ("you offered", ask)]:
        return None                       # the shop did not take its own ask: the talk ends as before
    mine = [p for who, p in history if who == "you offered"]
    if not mine or mine[-1] < limit:
        return None                       # my own steps first: the take comes only after I offered my limit
    v = verified_ask(meta, text, history, goods)
    if v is None or v != ask or not limit < v <= top:
        return None
    return v


def hook(limit, top, goods=False, record=None):
    """buyer.talk's walk_take: (ask, meta, text, history, turn kind) -> the price or None. buyer.talk calls
    walk_take.sent(price, turn kind) when that price goes on the wire, after its clamps and decision guard; record(note)
    then (the orders row's margin_cap.walk_take)."""
    def walk_take(ask, meta, text, history, kind=None):
        return decide(ask, meta, text, history, limit, top, goods)

    def sent(price, kind=None):
        if record:
            record({"taken": price, "limit": limit, "ceiling": top, "turn": kind})
    walk_take.sent = sent
    return walk_take
