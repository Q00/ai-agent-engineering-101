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

Review c55c66e (W-20261116-1105): OTHER_UNIT is a deny list, and every word left off it got through ("demo unit", "Floor
unit", "Pre-owned", "B-stock", "중고입니다"; m1), as ask_read's header says such lists always did. A text read for its
number is now read only when every word left is one the shops write around an ask (ask_read.VOCAB, plus counteroffer,
my and lowest of usagi's "Counteroffer: N. This is my lowest price."; EXTRA_WORDS). The metadata ask is the shop's own
number and keeps OTHER_UNIT only, a product name there is no price, with the review's words added to it. A shop that
writes its quote again each turn names the quote's grade ("grade A", "grade=A", "Grade A: normal.", "Inventory
condition: A."): that grade, when it is the one main priced the cap for (hook's grade), is no other unit (m2); any
other grade, or none known, still blocks. An ask the shop already let pass after I offered it, anywhere in the talk,
is not offered again (m3).
"""
import re

import ask_read
import oneshot

# a whole number in the text ("231698", "231,698"), not part of a word or a decimal
NUMBER = re.compile(r"(?<![0-9A-Za-z.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?![0-9A-Za-z]|[.,][0-9])")
_WORD_DIGITS = re.compile(r"[0-9]")
# words that can mean another unit than the one quoted (a grade swap, a refurbished or box-damaged one): not read here,
# the receipt check does not check the grade the cap was priced for
OTHER_UNIT = re.compile(r"\bgrades?\b|refurb|box|damaged|condition|\bmodel\b|sample|display|\brent|\blease|empty|used\b|"
                        r"\bdemo|\bfloor\b|pre-?owned|owned\b|b-?stock|\breturn|\balone\b|\bopen-?box|"
                        r"등급|리퍼|박스|훼손|전시|중고|반품|단품", re.I)
# the words a text read for its number may hold besides ask_read.VOCAB (the usagi tail; review c55c66e m1)
EXTRA_WORDS = frozenset(["counteroffer", "my", "lowest"])


def _same_grade(grade):
    """The fields that name the quoted grade again (review c55c66e m2), or None when the grade is not known: "grade A",
    "grade=A", "Grade A: normal", "Inventory condition: A", "A등급". The letter must be the grade's own, upper case (so
    "grade a little damaged" is no grade A), and a word after it ends the field."""
    if not isinstance(grade, str) or grade not in ("A", "B", "C"):
        return None
    return re.compile(rf"(?:(?i:\bgrade)[ \t]*[:=]?[ \t]*{grade}(?:[ \t]*:[ \t]*(?i:normal))?|"
                      rf"(?i:\binventory[ \t]+condition)[ \t]*:[ \t]*{grade})(?![\w-])|{grade}등급(?:이며|으로)?")


def _cut(text, grade=None):
    """The text with the fields the shops write without a price cut (ask_read SAFE, BENIGN, my offer named back) and
    the quoted grade named again (m2), for the word checks only; the number is read from the text as written."""
    t = ask_read.YOURS.sub(" ", ask_read.BENIGN_RE.sub(" ", ask_read.SAFE_RE.sub(" ", text or "")))
    same = _same_grade(grade)
    return same.sub(" ", t) if same else t


def _known_words(t):
    """Every word is one the shops write around an ask (ask_read.VOCAB, EXTRA_WORDS); numbers and ids are no words
    (ask_read.WORD), and the digits are checked by _one_number."""
    return all(w.lower() in ask_read.VOCAB or w.lower() in EXTRA_WORDS for w in ask_read.WORD.findall(t))


def verified_ask(meta, text, history, goods=False, grade=None):
    """The shop's current ask from its own message, or None. history holds the turns before this message. The metadata
    ask, else oneshot.text_price (a TEMPLATE text or ask_read), else one number left once the fields the shops write
    without a price are cut (ask_read SAFE and BENIGN: SKU, unit id, hours, offers left, list price) and my own offers
    named back are set aside, standing after an ask word (ask_read.ANCHORS), with no word left that can make it another
    price (ask_read.DANGER: was, now, each, other, minus, a number in words, ...) or another unit (OTHER_UNIT). Never over the shop's earlier asks and always over my last offer. Goods whose text
    says no warranty are never read, metadata or not (the kit pays goods only with the warranty). grade is the quote's
    (main's margin cap): that grade named again is no other unit. A text with no metadata is read only when every word
    left is a known one (_known_words, review c55c66e m1)."""
    if goods and ask_read.no_warranty(text or ""):
        return None
    cut = _cut(text, grade)
    if OTHER_UNIT.search(cut):
        return None                       # maybe another unit than the one the cap was priced for
    if meta is None and not _known_words(cut):
        return None                       # a word no shop wrote around an ask: the number is not read from this text
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


def decide(ask, meta, text, history, limit, top, goods=False, grade=None):
    """The price to offer instead of walking away, or None to walk. ask is the turn's recorded ask, history the turns
    before it. grade: the quote's grade (verified_ask)."""
    if type(top) is not int or type(limit) is not int or top <= limit or type(ask) is not int:
        return None
    if any(history[i:i + 2] == [("shop asked", ask), ("you offered", ask)] for i in range(len(history) - 1)):
        # the shop did not take its own ask when I offered it, then or any turn since (a limit offer between, review
        # c55c66e m3): the talk ends as before, the take is never sent twice
        return None
    mine = [p for who, p in history if who == "you offered"]
    if not mine or mine[-1] < limit:
        return None                       # my own steps first: the take comes only after I offered my limit
    v = verified_ask(meta, text, history, goods, grade)
    if v is None or v != ask or not limit < v <= top:
        return None
    return v


def hook(limit, top, goods=False, record=None, grade=None):
    """buyer.talk's walk_take: (ask, meta, text, history, turn kind) -> the price or None. buyer.talk calls
    walk_take.sent(price, turn kind) when that price goes on the wire, after its clamps and decision guard; record(note)
    then (the orders row's margin_cap.walk_take). grade: the quote's grade main priced the cap for (margin_cap's), or
    None (then any grade named blocks, as before)."""
    def walk_take(ask, meta, text, history, kind=None):
        return decide(ask, meta, text, history, limit, top, goods, grade)

    def sent(price, kind=None):
        if record:
            record({"taken": price, "limit": limit, "ceiling": top, "turn": kind})
    walk_take.sent = sent
    return walk_take
