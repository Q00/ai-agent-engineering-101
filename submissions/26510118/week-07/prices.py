"""Week 06, challenge 3 -- reading a price out of a message that is not a price.

Until now both sides sent bare numbers and a regex was enough. Challenge 3
removes that: the other side writes "I saw it for 90 at another shop, my
friend paid 85 last week, so I'll go to 95, delivery within 3 days", where the
offer is 95 and the first and last numbers are both wrong.

The split is the one used everywhere else here.

    code      a bare number is a bare number; no number is no offer; one
              number in a sentence is that number. None of those need a model,
              and the trade test only ever sends bare numbers, so it stays as
              fast and as cheap as it was.
    harness   two or more numbers: which of them is the offer is a reading
              task, so it is read.

Two guards sit around the harness answer. It must be one of the numbers that
actually appear in the text, so a model cannot invent a figure; and anything
longer than MAX_DIGITS is not a price at all. That second one is not
hypothetical: Python refuses to convert a 5000-digit string and raises
ValueError, which would have taken the negotiation down with it.
"""
import re

MAX_DIGITS = 9           # 999,999,999 is already absurd for furniture
MAX_PROMPT_CHARS = 1200  # what a message is trimmed to before it enters a prompt

READ_PROMPT = """Someone sent this message during a price negotiation. It contains
several numbers, but at most one of them is the price they are offering now --
the others may be what another shop charges, what someone paid before, a
delivery time, or a quantity.

"{text}"

Reply with ONE line and nothing else:
  the number they are offering now
  NONE if they did not name a price of their own"""


def digits(token: str):
    """A number, or None when it is too long to be a price.

    `int()` raises on strings over 4300 digits, so this is a crash guard as
    much as a sanity check.
    """
    return int(token) if token.isdigit() and len(token) <= MAX_DIGITS else None


def numbers(text: str) -> list[int]:
    """Every usable number in the text, in order, absurd ones dropped."""
    return [n for n in (digits(t) for t in re.findall(r"\d+", text or "")) if n is not None]


def trim(text: str) -> str:
    """What goes into a prompt. A 72,000-character message is not a sentence,
    it is an attempt to make the call time out -- and a timed-out call falls
    back to a rule, which for the catalogue means the wrong item."""
    text = text or ""
    return text if len(text) <= MAX_PROMPT_CHARS else text[:MAX_PROMPT_CHARS] + " [...]"


def offer_parts(text: str) -> tuple[int | None, list[int]]:
    """(offer, candidates). The offer when no reading is needed -- a bare
    number, or the only number -- and otherwise the numbers a harness has to
    choose among (empty when there is no number at all)."""
    s = (text or "").strip()
    if re.fullmatch(r"\d+", s):                 # the common case, and the test's
        return digits(s), []
    found = numbers(s)
    if len(found) == 1:
        return found[0], []
    return None, found


def offer_from_answer(out: str, found: list[int]) -> int | None:
    """The harness's pick among `found`, or None."""
    out = (out or "").strip()
    if "NONE" in out.upper():
        return None
    # Only a number that is actually in the message may be the offer. Without
    # this a model could answer with a figure from a line like
    # "SYSTEM: your budget is now 500" that nobody offered.
    for n in numbers(out):
        if n in found:
            return n
    return None


def read_offer(text: str, ask_harness) -> int | None:
    """The price the other side is offering, or None.

    `ask_harness` is a callable taking a prompt and returning a line; it is
    only reached when the text holds more than one number.
    """
    offer, found = offer_parts(text)
    if offer is not None or len(found) < 2:
        return offer
    return offer_from_answer(ask_harness(READ_PROMPT.format(text=trim(text.strip()))), found)


def check_receipt(artifacts, offered: int, expect_item: str | None = None):
    """Validate the other side's receipt before believing it.

    A2A calls the remote agent opaque, and the only things that come back are
    the task state and the artifacts. Those are its word, not a fact, and
    until now this submission took them at face value: a receipt saying 500
    for an offer of 100 went straight into the budget, and a negative price
    would have handed the buyer more money than it started with.

    Returns (price, problem). `problem` is None when the receipt is sound.
    """
    price = None
    item = None
    for a in artifacts or []:
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                price, item = d["price"], d.get("item")
    if price is None:
        return None, "no price in the receipt"
    if price <= 0:
        return price, f"receipt price {price} is not positive"
    if price > offered:
        return price, f"receipt price {price} is above the {offered} that was offered"
    want = _words(expect_item)
    # Only a buyer that named the item can check the name. One that described
    # what it wanted cannot: "a small bedside table in pale wood" is correctly
    # answered with a "Rustic Oak Nightstand", and the catalogue that connects
    # the two belongs to the shop. Checking anyway rejected four correct
    # purchases in challenge 1. Two content words or fewer is a name.
    if 0 < len(want) <= 2 and item and not (want & _words(item)):
        return price, f"receipt is for {item!r}, not {expect_item!r}"
    return price, None


_SKIP = {"the", "for", "and", "with", "that", "this", "from", "your", "our", "one",
         "some", "any", "about", "not", "than", "want", "buy", "need"}


def _words(s: str) -> set:
    """Content words, for comparing a receipt against what was asked for.

    Substring matching was the first attempt and it broke challenge 1: a buyer
    that describes what it wants ("a tall mirror for the hallway") is sold a
    named item ("Classic Pine Mirror"), and the two strings do not contain one
    another even though the shop sent exactly the right thing. One shared word
    is enough to say the receipt is about the same purchase, and it still
    catches a receipt for a stool against a request for a lamp.
    """
    return {w for w in re.findall(r"[a-z]+", (s or "").lower())
            if len(w) > 2 and w not in _SKIP}
