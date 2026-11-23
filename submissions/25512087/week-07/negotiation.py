"""Deterministic negotiation rules shared by the shop and the buyers.

No harness call sits on the critical path: every turn answers in milliseconds, so slow
harnesses (or an exhausted model quota) can no longer make a counterpart time out.
"""

from __future__ import annotations

import math
import re

from harness import Decision

# Longest/most specific aliases first: "bookshelf" must win over "shelf".
ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "bookshelf": ("bookshelf", "book shelf", "bookshelves", "bookcase", "book case", "shelf", "책장", "책꽂이"),
    "chair": ("chair", "armchair", "stool", "seat", "의자"),
    "lamp": ("lamp", "desk light", "reading light", "램프", "스탠드", "조명"),
}
ACCEPT_WORDS = (
    "accept", "deal", "agreed", "agree", "ok", "okay", "yes", "sounds good", "i'll take",
    "i will take", "take it", "좋아", "좋습니다", "수락", "콜", "그래",
)
OFFER_WORDS = ("offer", "pay", "for ", " at ", "price", "budget of", "제안", "원")


def find_item(text: str) -> str | None:
    lowered = text.lower()
    for item, aliases in ITEM_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return item
    return None


# A price token is read whole ("185,000" is judged as one number, never cut to 185). ASCII digits only, and the
# same character classes as the Worker shop, so Python and JavaScript read a message the same way.
PRICE_TOKEN = re.compile(r"(?<![A-Za-z0-9_.,#/])-?[0-9][0-9.,]*(?![A-Za-z0-9_/])")
# Numbers that are not the price: a quantity or duration right after ("1 lamp", "10 min", "1,024 chairs"),
# a reference right before ("was 200", "budget 300", "#48213").
NOT_PRICE_AFTER = re.compile(
    r"\s*(?:%|x\b|percent|min|mins|minutes?|h\b|hrs?|hours?|days?|sec|seconds?|times|rounds?|offers?|items?|"
    r"units?|pieces?|pcs|lamps?|chairs?|bookshel(?:f|ves)|shelves|sold|left|in stock|stars?)", re.IGNORECASE)
NOT_PRICE_BEFORE = re.compile(r"(?:\bwas|\bbudget(?: of| is)?|\bpreviously|\bbefore|\binstead of|\border)\s*$",
                              re.IGNORECASE)
MAX_PRICE = 100_000


def price_token(text: str) -> str | None:
    """The token that reads as the price: the last number that is not a quantity, duration or reference."""
    text = text or ""
    found = [m for m in PRICE_TOKEN.finditer(text)]
    if not found:
        return None
    for m in reversed(found):
        end = m.start() + len(m.group().rstrip(".,"))
        unit_follows = text[end:end + 1] not in (",", ".", ";", ")") and NOT_PRICE_AFTER.match(text, end)
        if unit_follows or NOT_PRICE_BEFORE.search(text[max(0, m.start() - 20):m.start()]):
            continue
        return m.group()
    return None


def parse_price(text: str, round_up: bool = False) -> tuple[int | None, str | None]:
    """The price in a message, as (whole number, None), or (None, why it was refused).

    Only plain numbers are trusted: 185, 185.0, 185.5 (rounded down for an offer, up for an ask).
    "185,000", "1.850.000", "185.000" mean different things in different locales, so they are refused
    and the other side is asked to resend; a negative or absurdly large number is refused too.
    Quantities, durations and references ("1 lamp", "valid 10 min", "order #48213", "was 200") are
    skipped. (None, None) = no price-like number at all.
    """
    token = price_token(text)
    if token is None:
        return None, None
    token = token.rstrip(".,")
    if token.startswith("-"):
        return None, f"negative price {token}"
    if "," in token:
        return None, f"ambiguous number {token} (comma)"
    if token.count(".") > 1:
        return None, f"ambiguous number {token} (several dots)"
    if "." in token and len(token.split(".")[1]) == 3:
        return None, f"ambiguous number {token} (three decimals)"
    value = float(token)
    value = math.ceil(value) if round_up else math.floor(value)
    if value > MAX_PRICE:
        return None, f"price {token} is out of range"
    return value, None


def parse_offer(text: str) -> int | None:
    """The buyer's price: the last plain number in the message, rounded down; None if absent or refused."""
    value, problem = parse_price(text)
    return None if problem else value


NEGATION = re.compile(r"(?<![a-z])(no|not|don't|won't|cannot|can't|reject|decline|walk|pass)(?![a-z])|거절|안 ")


def says_accept(text: str) -> bool:
    lowered = f" {text.lower()} "
    if NEGATION.search(lowered):
        return False
    return any(re.search(rf"(?<![a-z]){re.escape(word.strip())}(?![a-z])", lowered) for word in ACCEPT_WORDS)


def opening_offer(text: str) -> int | None:
    """A price named in the first message ("a lamp for 110"), if it reads as an offer."""
    lowered = text.lower()
    if not any(word in lowered for word in OFFER_WORDS):
        return None
    return parse_offer(text)


class FastSellerPolicy:
    """Accept anything at or above reserve at once; below it, show the final price fast and stop early.

    - offer >= reserve: accept at exactly the offer.
    - our ask already is the reserve (floor pricing): counter with the reserve at once, marked final.
    - otherwise one midpoint counter, then the reserve (final).
    `should_end` closes a haggle that cannot succeed: once the final price was shown, a below-floor offer
    that does not rise (or the FLOOR_PATIENCE-th one) ends the task politely instead of repeating
    "too low, counter 100" up to the round cap - the long dead-end haggles are what cost stars.
    """

    FLOOR_PATIENCE = 3

    def respond(self, reserve: int, ask: int, offer: int, rounds: int) -> Decision:
        if offer >= ask:
            # sign exactly the buyer's number: strict "agreed == receipt" checks never see a mismatch
            return Decision("accept", offer, "offer meets ask")
        if offer >= reserve:
            # like the profit leaders: a FIRST offer under the ask gets one counter (halfway, at least +1);
            # from the second offer on, anything at or above the floor is signed
            if rounds == 0:
                return Decision("counter", min(ask, max(offer + 1, (offer + ask) // 2)), "first offer under ask")
            return Decision("accept", offer, "offer meets reserve")
        if ask <= reserve or rounds >= 1:
            return Decision("counter", reserve, "final price (reserve)")
        return Decision("counter", max(reserve, min(ask, (offer + ask) // 2)), "counter round 1")

    def should_end(self, reserve: int, offers: list[int], floor_quotes: int) -> bool:
        """True when the latest below-floor offer, made after the final price was shown, cannot lead to a deal."""
        if floor_quotes < 1 or not offers or offers[-1] >= reserve:
            return False
        if floor_quotes >= self.FLOOR_PATIENCE:
            return True
        return len(offers) >= 2 and offers[-1] <= offers[-2]


def opening_message(item: str, ask: int, reserve: int) -> str:
    """First reply. At floor pricing say there is nothing to haggle; the ask stays the last number."""
    if ask <= reserve:
        return (f"{item}: this is our final price, the market floor for a {item}, so no haggling is needed. "
                f"Reply ok to take it at once. Asking {ask}.")
    return f"{item}: asking {ask}. Send your offer as a number."


def counter_message(offer: int, counter: int, reserve: int, item: str) -> str:
    """Counteroffer text; the price stays the last number. At the floor the reason is given in words."""
    if offer >= reserve:
        return f"{offer} is close. Counteroffer: {counter}."
    if counter <= reserve:
        return (f"{offer} is below the market floor for a {item}; no shop here may sign below it. "
                f"Reply ok and the signed receipt comes back at once. Our final price: {counter}.")
    return f"{offer} is too low. Counteroffer: {counter}."


def end_message(item: str, reserve: int) -> str:
    """Polite close of a haggle that cannot reach the floor; invites a return."""
    return (f"Sorry, we cannot sign below the market floor for a {item}. If your budget reaches it, "
            f"ask again and it is yours in one message: {reserve}.")


# Every shop keeps the same reserve (course rule) and must not sell below it, so the reserve is the
# lowest price anyone can honestly get. Shops built like ours accept any offer at or above it.
MARKET_FLOOR = {"lamp": 100, "chair": 180, "bookshelf": 260}


class LadderBuyerPolicy:
    """Lowest-price buyer: open at the market floor, climb in small steps, take the ask under the next rung.

    Known item (floor known):
      budget >= floor: rungs floor, +3%, +7%, halfway to budget, budget.
      budget <  floor: one offer at the budget (no honest shop can sell), then walk; saves the order time.
    Unknown item: the old ladder (75/86/94/100% of budget); `hint` (a price paid this shop before) can
    lower its first rung.
    """

    RUNGS = (0.75, 0.86, 0.94, 1.0)
    FLOOR_STEPS = (1.0, 1.03, 1.07)

    def __init__(self, hint: int | None = None) -> None:
        self.hint = hint
        self.last_offer = 0

    def ladder(self, item: str, budget: int) -> list[int]:
        floor = MARKET_FLOOR.get(item)
        if floor is None:
            rungs = [math.floor(budget * r) for r in self.RUNGS]
            if self.hint is not None and 0 < self.hint < rungs[0]:
                rungs[0] = self.hint
        elif budget < floor:
            rungs = [budget]
        else:
            steps = [math.ceil(floor * f) for f in self.FLOOR_STEPS]
            halfway = (steps[-1] + budget + 1) // 2
            rungs = steps + [halfway, budget]
        out: list[int] = []
        for r in rungs:                                   # within budget, never decreasing, no repeats
            r = min(budget, r)
            if not out or r > out[-1]:
                out.append(r)
        return out

    def respond(self, item: str, budget: int, ask: int, rounds: int) -> Decision:
        if ask is None:
            return Decision("walk", None, "no ask to answer")
        ladder = self.ladder(item, budget)
        if rounds >= len(ladder) and ask > budget:
            return Decision("walk", None, f"ask {ask} still above budget after {rounds} offers")
        plan = max(self.last_offer, ladder[min(rounds, len(ladder) - 1)])
        if ask <= plan or (ask <= budget and rounds >= len(ladder) - 1):
            offer, reason = ask, f"ask {ask} is within plan {plan}"
        else:
            offer, reason = plan, f"rung {rounds + 1}/{len(ladder)}: offer {plan} against ask {ask}"
        offer = max(0, min(budget, int(offer)))
        self.last_offer = max(self.last_offer, offer)
        return Decision("offer", offer, reason)
