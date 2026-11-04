"""Accept only one unambiguous affordable asking price; checkout remains authoritative."""

import re

OFFER_REASON = "Unambiguous current ask within coded value/quality cap; checkout will verify terms."

# Only labels that name this seller's current ask. A bare "price" also fits list, past and median figures.
LABELED = re.compile(r"(?<![\w])(?:asking price|asking|ask|counter-?offer|offer price|가격|제안가)"
                     r"(?:\s+is\b)?\s*[:：=]?\s*(\d[\d,.]*)", re.I)
WHOLE = re.compile(r"[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+")
CURRENCY = re.compile(r"\s*(?:원|KRW\b|won\b)", re.I)
# What may follow a price: the end of the line, or punctuation closing its clause.
FOLLOWER = re.compile(r"[ \t]*(?:$|\r?\n|[.,;:!?)\]}'\"|(])", re.M)
AFTER_FINAL_MARK = re.compile(r"$|\s|[)\]}'\"|]")
RANGE = re.compile(r"[.,]?\s*(?:[-~–—>→]+|(?:or|to)\b)\s*\d|,\s*\d", re.I)
# A label directly after one of these words is a past, market or other party's figure.
NOT_CURRENT = {"list", "listed", "was", "were", "previous", "previously", "prior", "original", "originally",
               "old", "former", "last", "earlier", "market", "median", "average", "avg", "typical", "usual",
               "competitor", "rival", "your", "not", "to", "정가", "시중", "이전", "과거", "기존", "평균",
               "시장", "중앙값", "정상", "권장", "소비자"}
PAST_LABEL = re.compile(r"(?<![\w])(?:asking price|asking|ask|offer price|가격|제안가)\s+(?:was|were)\b", re.I)


def _preceding_word(text, start):
    found = re.search(r"([^\W\d_]+)(?:['’]s)?\s+$", text[:start])
    return found[1].casefold() if found else ""


def _token_is_price(token, rest):
    """Separate a clause-ending mark from a decimal or malformed thousands group."""
    if RANGE.match(token[len(token.rstrip(".,")):] + rest):
        return False
    final_mark = token[-1] in ".,"
    number = token[:-1] if final_mark else token
    if not WHOLE.fullmatch(number):
        return False
    if final_mark:
        return bool(AFTER_FINAL_MARK.match(rest))
    currency = CURRENCY.match(rest)
    return bool(FOLLOWER.match(rest[currency.end():] if currency else rest))


def parse_ask(text):
    """Return (price, code); price is set only for one unambiguous current ask."""
    if PAST_LABEL.search(text):
        return None, "non_current_price"
    prices = set()
    found = False
    for match in LABELED.finditer(text):
        found = True
        if _preceding_word(text, match.start()) in NOT_CURRENT:
            return None, "non_current_price"
        token = match[1]
        if not _token_is_price(token, text[match.end():]):
            return None, "malformed_price"
        prices.add(int(token.rstrip(".,").replace(",", "")))
    if not found:
        return None, "no_labeled_price"
    if len(prices) != 1:
        return None, "conflicting_prices"
    return prices.pop(), "single_ask"


def classify(text, cap, last_offer=None):
    """Return (decision or None, reason code, parsed price or None) for the decision log."""
    price, code = parse_ask(text)
    if price is None:
        return None, code, None
    if not 0 < price <= cap:
        return None, "over_budget", price
    if last_offer == price:
        return ({"action": "walk_away", "price": None,
                 "reason": "Same offer repeated without settlement; no blind retry."},
                "rule_repeated_offer", price)
    return {"action": "offer", "price": price, "reason": OFFER_REASON}, "rule_offer", price


def decision(text, cap, last_offer=None):
    return classify(text, cap, last_offer)[0]
