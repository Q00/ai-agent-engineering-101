"""Accept only one unambiguous affordable asking price; checkout remains authoritative."""

import json
import math
from pathlib import Path
import re

CONFIG = Path(__file__).resolve().parent / "buyer_support.json"
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
# A marker that replaces the current price, directly followed by a number. The lookbehind skips [한정특가].
PRICE_CHANGE = re.compile(r"(?<![\w])(?:now|today|only|just|new price|sale price|discount price|special price|"
                          r"price now|할인가|특가|오늘만|지금)"
                          r"(?:\s+(?:only|just|at|for|is))*\s*[:：=]?\s*[$₩]?\s*(\d[\d,.]*)", re.I)
# The same words also introduce stock counts, sizes and durations: "Only 3 left", "now 24 h warranty".
QUANTITY = re.compile(r"[\s-]*(?:%|(?:left|h|hrs?|hours?|days?|mins?|minutes?|units?|pcs?|pieces?|items?|ea|"
                      r"g|kg|ml|l|offers?|remaining|more|in)\b|개|시간|일|남)", re.I)


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


def _names_another_price(text, price):
    for match in PRICE_CHANGE.finditer(text):
        token, rest = match[1], text[match.end():]
        if QUANTITY.match(rest) or (rest[:1].isalpha() and not CURRENCY.match(rest)):
            continue
        if token.rstrip(".,").replace(",", "") != str(price):
            return True
    return False


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
    price = prices.pop()
    if _names_another_price(text, price):
        return None, "conflicting_prices"
    return price, "single_ask"


NUMBER = r"(\d[\d,.]*)"
GROUP_A = (("my_price", re.compile(r"(?<![\w])my price\s*[:：=]?\s*" + NUMBER, re.I)),
           ("final_price", re.compile(r"(?<![\w])(?:our|my) final price(?: for [^.:：\d]{1,60})?(?:\s+is)?\s*[:：=]?\s*" + NUMBER, re.I)))
GROUP_B = (("lowest_price", re.compile(r"(?<![\w])(?:our|my) lowest price(?: for this unit)?\s*[:：=]?\s*" + NUMBER, re.I)),
           ("colon_krw", re.compile(r"[:：]\s*" + NUMBER + r"\s*(?:KRW\b|won\b|원)", re.I)))
ANY_KRW = re.compile(NUMBER + r"\s*(?:KRW\b|won\b|원)", re.I)
# Words before a "name: 12000 KRW" that make it some other figure than this seller's ask.
OTHER_FIGURE = NOT_CURRENT | {"elsewhere", "other", "others", "lowest", "highest", "msrp", "rrp", "retail", "compare", "comparable", "shipping", "delivery", "fee", "deposit",
                              "warranty", "refund", "budget", "value", "worth", "cost", "savings", "save", "discount", "off", "stars", "review", "reviews", "다른", "타사", "배송", "보증", "환불", "원가"}
OFF = {"group_a": False, "group_b": False}


def load_reading(path=CONFIG):
    """Which added groups are on. Anything but an explicit true is off, and a missing or unreadable setting turns both off."""
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8")).get("price_reading")
    except (OSError, ValueError, AttributeError):
        return dict(OFF)
    if not isinstance(section, dict):
        return dict(OFF)
    return {"group_a": section.get("group_a_structured_and_labels") is True, "group_b": section.get("group_b_krw_and_lowest") is True}


def _clause_before(text, start):
    return re.split(r"[.!?\n;]", text[:start])[-1].casefold()


def _other_figure(clause):
    words = re.findall(r"[^\W\d_]+", clause)
    return any(word in OTHER_FIGURE for word in words) or any(word in clause for word in OTHER_FIGURE if not word.isascii())


def structured_ask(metadata):
    value = metadata.get("ask") if isinstance(metadata, dict) else None
    if type(value) is float and math.isfinite(value) and value.is_integer():
        value = int(value)
    return value if type(value) is int and value > 0 else None


def read_ask(text, metadata=None, reading=None):
    """(price, code, how). The current parser always goes first; added readings run only when it found no label at all, and only with the same cautions."""
    price, code = parse_ask(text)
    if code != "no_labeled_price" or not reading or not (reading.get("group_a") or reading.get("group_b")):
        return price, code, None
    found = {}
    rules = (GROUP_A if reading.get("group_a") else ()) + (GROUP_B if reading.get("group_b") else ())
    for how, pattern in rules:
        for match in pattern.finditer(text):
            if how == "colon_krw":
                if _other_figure(_clause_before(text, match.start())):
                    return None, "non_current_price", how
            elif _preceding_word(text, match.start()) in NOT_CURRENT:
                return None, "non_current_price", how
            if not _token_is_price(match[1], text[match.end():]):
                return None, "malformed_price", how
            found.setdefault(int(match[1].rstrip(".,").replace(",", "")), how)
    if reading.get("group_a"):
        ask = structured_ask(metadata)
        if ask is not None:
            found.setdefault(ask, "metadata_ask")
    if not found:
        return None, "no_labeled_price", None
    if len(found) != 1:
        return None, "conflicting_prices", None
    price, how = next(iter(found.items()))
    if how == "colon_krw" and any(int(m[1].rstrip(".,").replace(",", "") or 0) != price for m in ANY_KRW.finditer(text) if WHOLE.fullmatch(m[1].rstrip(".,"))):
        return None, "conflicting_prices", how
    if _names_another_price(text, price):
        return None, "conflicting_prices", how
    return price, "single_ask", how


def classify(text, cap, last_offer=None, metadata=None, reading=None):
    """Return (decision or None, reason code, parsed price or None) for the decision log."""
    price, code, _ = read_ask(text, metadata, reading)
    if price is None:
        return None, code, None
    if not 0 < price <= cap:
        return None, "over_budget", price
    if last_offer == price:
        return ({"action": "walk_away", "price": None,
                 "reason": "Same offer repeated without settlement; no blind retry."},
                "rule_repeated_offer", price)
    return {"action": "offer", "price": price, "reason": OFFER_REASON}, "rule_offer", price


def decision(text, cap, last_offer=None, metadata=None, reading=None):
    return classify(text, cap, last_offer, metadata, reading)[0]
