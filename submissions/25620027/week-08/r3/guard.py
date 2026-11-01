"""Trust boundary for every piece of text that did not come from our own code.

Counterpart messages, public reviews, forum posts and shop names are data. They
reach a model only sanitised, bounded and wrapped in a labelled block, and nothing a
model returns is ever relayed outward verbatim: outputs are enums, bounded numbers
and allowlisted names that code turns into template sentences. Before any model
call the text is screened for instruction injection, authority claims and payment
redirection; a hit skips the model and falls back to the deterministic path.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from market_a2a.security import operator_directive_observed

MAX_SNIPPET: Final = 600
MAX_NAME: Final = 80
ZERO_WIDTH: Final = re.compile("[\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u2064\\ufeff]")
CLOSE_TAG: Final = re.compile(r"</?\s*untrusted[^>]*>", re.IGNORECASE)
INSTRUCTION_OVERRIDE: Final = re.compile(
    r"""\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?
    (?:previous|prior|above|earlier|system|initial)?\s*
    (?:instructions?|prompts?|rules?|policy|policies|guidelines?)\b
    |\bsystem\s+prompt\b|\byou\s+are\s+now\b|\bact\s+as\s+(?:an?\s+)?(?:ai|assistant|
    admin|administrator|operator)\b|\bdeveloper\s+(?:message|mode)\b|\bjailbreak\b
    |\bnew\s+instructions?\s*:|\bas\s+an?\s+ai\b.{0,40}\b(?:must|should|will)\b
    |\b(?:assistant|system)\s*:\s*\S""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
TOOL_OR_JSON: Final = re.compile(
    r"""\bset_(?:policy|tactical|strategy)\b|\bwrite_picture\b|\btool_calls?\b
    |\bfunction_call\b|```\s*json|"role"\s*:\s*"(?:system|assistant|user)"
    |\bread_(?:seller|buyer)_message\b""",
    re.IGNORECASE | re.VERBOSE,
)
PAYMENT_REDIRECT: Final = re.compile(
    r"""\b(?:pay|send|transfer|wire|deposit)\s+(?:the\s+|your\s+)?(?:credits?|money|
    funds?|payment|balance)?\s*(?:to|into)\s+(?:this|the|my|our|another)\b
    |\bwallet\s+address\b|\baccount\s+number\b|\bprivate\s+key\b|\.pem\b
    |\bapi[_\s-]?key\b|\bbearer\s+token\b
    |\bpay\s+(?:\S+\s+)?(?:first|upfront|in\s+advance)\b
    |\boutside\s+(?:the\s+)?(?:market|checkout|kit)\b""",
    re.IGNORECASE | re.VERBOSE,
)
FAKE_SETTLEMENT: Final = re.compile(
    r"""\bsig_seller\b|\bsig_buyer\b|\bmandate\b|\bverified\s*(?::|=)?\s*true\b
    |\balready\s+(?:paid|settled|verified)\b|\bledger\s*#?\s*[0-9]+\s+(?:confirms|shows)
    |\bcheckout\s+(?:is\s+)?complete[d]?\b""",
    re.IGNORECASE | re.VERBOSE,
)
RATING_INDUCEMENT: Final = re.compile(
    r"""\b(?:give|leave|rate)\s+(?:us\s+|me\s+|this\s+shop\s+)?(?:5|five)\s+stars?\b
    |\bupvote\b|\bvote\s+(?:for\s+)?(?:us|me|this)\b|\blike\s+(?:our|my)\s+post\b""",
    re.IGNORECASE | re.VERBOSE,
)
AUTHORITY_CLAIM: Final = re.compile(
    r"""\b(?:market|platform|system|exchange)\s+(?:operator|administrator|admin|
    notice|staff|support)\b.{0,80}?\b(?:requires?|instructs?|mandates?|must|
    verified|approved|authorized)\b""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
# Second-person appeals to buying agents in reviews or posts: not an attack, but a
# sign the text was written to steer agents rather than to describe a deal.
AI_APPEAL: Final = re.compile(
    r"""\b(?:note|tip|advice|message)\s+(?:for|to)\s+(?:ai\s+|other\s+)?(?:buyer|buying|
    shopping)?\s*agents\b|\bbuyer\s+agents\s+comparing\b|\btry\s+(?:it|us|this\s+shop|
    the\s+store|them)\s+first\b|\balways\s+(?:choose|pick|buy\s+from|go\s+with)\b
    |\b(?:best|cheapest)\s+(?:verified\s+)?shop\s+(?:in|on)\s+(?:the|this)\s+market\b
    |\byou\s+should\s+(?:buy|shop|order)\s+(?:here|from)\b""",
    re.IGNORECASE | re.VERBOSE,
)
SCREENS: Final = (
    ("instruction_override", INSTRUCTION_OVERRIDE),
    ("authority_claim", AUTHORITY_CLAIM),
    ("tool_or_json", TOOL_OR_JSON),
    ("payment_redirect", PAYMENT_REDIRECT),
    ("fake_settlement", FAKE_SETTLEMENT),
    ("rating_inducement", RATING_INDUCEMENT),
)


def sanitize(text: str, limit: int = MAX_SNIPPET) -> str:
    """Drop control and zero-width characters, collapse whitespace, cap the length."""
    cleaned = ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
    cleaned = "".join(
        ch if ch in "\n\t" or unicodedata.category(ch)[0] != "C" else " "
        for ch in cleaned
    )
    cleaned = CLOSE_TAG.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:limit]


def sanitize_name(text: str) -> str:
    """One line, no markup, for shop and item names shown to a model."""
    return sanitize(text.replace("\n", " "), MAX_NAME)


def suspicious(text: str) -> str | None:
    """Return the first screen a counterpart message trips, or None when clean."""
    bounded = text[:4000]
    if operator_directive_observed(bounded):
        return "operator_directive"
    for label, pattern in SCREENS:
        if pattern.search(bounded) is not None:
            return label
    return None


def annotate(text: str) -> str | None:
    """Label review or forum text for the scout: a screen hit, else an AI appeal."""
    flag = suspicious(text)
    if flag is not None:
        return flag
    return "ai_appeal" if AI_APPEAL.search(text[:4000]) is not None else None


def wrap_untrusted(label: str, text: str, limit: int = MAX_SNIPPET) -> str:
    """Delimit third-party text so a prompt can name it as data, never as orders."""
    safe_label = re.sub(r"[^a-z0-9_]", "_", label.lower())[:32] or "text"
    body = sanitize(text, limit)
    return f"<untrusted_{safe_label}>\n{body}\n</untrusted_{safe_label}>"


def bounded_int(value: object, low: int, high: int) -> int | None:
    """Accept a whole number inside [low, high]; anything else is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if float(value) != int(value):
        return None
    number = int(value)
    return number if low <= number <= high else None
