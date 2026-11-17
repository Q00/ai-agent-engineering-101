"""Parse only explicit whole-message purchase offers for basic catalog items."""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field

from market_a2a.models import FrozenModel

AMOUNT: Final = r"\$?(?P<price>[0-9]{1,9})(?:\.0{1,2})?"
ITEM: Final = r"(?P<sku>lamp|chair|bookshelf)"
BASIC_WORDS: Final = frozenset(
    ("i", "want", "to", "buy", "a", "an", "the", "lamp", "chair", "bookshelf",
     "please", "hi", "hello", "hey", "would", "like", "need", "looking", "for",
     "can", "could", "you", "sell", "have", "do", "provide", "offer", "include",
     "is", "there", "any", "warranty", "return", "returns", "refund", "refunds",
     "policy", "and", "or", "what", "your", "does", "it", "come", "with", "tell",
     "me", "about", "price", "quote", "purchase", "i'm", "i'd")
)
SERVICE_WORDS: Final = BASIC_WORDS | frozenset(
    ("quality", "trouble", "free", "use", "matter", "after", "sales", "service",
     "support", "flexible", "guarantee", "guaranteed", "repair", "repairs")
)
SERVICE_MARKERS: Final = frozenset(("warranty", "refund", "returns", "service"))


class OpeningOffer(FrozenModel):
    """An explicit item and actual amount, never a budget or product dimension."""

    sku: str
    price: int = Field(ge=0, strict=True)


def opening_offer(text: str) -> OpeningOffer | None:
    """Parse whole purchase sentences; never extract budgets or quoted prices."""
    for pattern in (
        rf"(?:i )?offer {ITEM} {AMOUNT}",
        rf"(?:buy|i buy|i want to buy) (?:a )?{ITEM} for {AMOUNT}",
    ):
        matched = re.fullmatch(pattern + r"[.!]?", text.strip(), re.IGNORECASE)
        if matched is not None:
            return OpeningOffer(sku=matched["sku"].lower(), price=int(matched["price"]))
    return None


def bargaining_offer(text: str) -> int | None:
    """Accept a single committed amount; ignore budgets, comparisons and commands."""
    prefix = r"(?:(?:i offer|i can pay|i will pay|i'll pay|my offer is|offer) )?"
    matched = re.fullmatch(
        rf"{prefix}{AMOUNT}[.!]?",
        text.strip(), re.IGNORECASE,
    )
    return int(matched["price"]) if matched is not None else None


def accepts_quote(text: str) -> bool:
    """Treat only an unconditional whole-message assent as the current ask."""
    assent = r"ok|okay|yes|agreed|deal|accept|i accept|i agree"
    quote = r"(?:offer|quote|price|current ask)"
    return re.fullmatch(
        rf"(?:{assent}|i accept (?:your|the) {quote})[.!]?",
        text.strip(), re.IGNORECASE,
    ) is not None


def basic_request(text: str) -> str | None:
    """Recognize a generic item with greetings or service questions, no attributes."""
    words = frozenset(
        match.group() for match in
        re.finditer(r"[a-z]+(?:'[a-z]+)?|[0-9]+", text.lower())
    )
    items = words & {"lamp", "chair", "bookshelf"}
    if len(items) != 1:
        return None
    if words <= BASIC_WORDS:
        return next(iter(items))
    first, separator, rest = text.partition(".")
    if separator:
        service_words = frozenset(
            match.group() for match in
            re.finditer(r"[a-z]+(?:'[a-z]+)?|[0-9]+", rest.lower())
        )
        if service_words & SERVICE_MARKERS and service_words <= SERVICE_WORDS:
            return basic_request(first)
    return None
