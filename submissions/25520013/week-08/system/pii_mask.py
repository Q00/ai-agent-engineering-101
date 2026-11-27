# v121c pii_mask.py: top-level (not under llm/) so the trading path (hooks/buyer_log) stays llm-free (test_isolation).
# Simple regex PII masking for LLM prompts (codex_client_v4.complete) and logs
# (hooks/buyer_log, seller request / no_stock text). Email, Korean phone, card number (13-19 digits, IIN 3-6, Luhn),
# resident registration number. Never raises (returns the input unchanged on an internal error). Stdlib only.
import re

EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
CARD = "[CARD]"
RRN = "[RRN]"
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RRN_RX = re.compile(r"(?<!\d)\d{6}\s?-\s?[1-8]\d{6}(?!\d)")
_CARD_RX = re.compile(r"(?<![\d.])[3-6]\d{3}(?:[ -]?\d){9,15}(?![\d.])")
_PHONE_RX = re.compile(r"(?<![\d.])(?:(?:\+|00)82[ -]?0?1[016789]|01[016789])[ .-]?\d{3,4}[ .-]?\d{4}(?![\d.])"
                       r"|(?<![\d.])0(?:2|[3-6][1-5])[ .-]\d{3,4}[ .-]\d{4}(?![\d.])")


def _luhn(d):
    s, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        s += n
        alt = not alt
    return s % 10 == 0


def _card(m):
    d = re.sub(r"\D", "", m.group(0))
    return CARD if 13 <= len(d) <= 19 and _luhn(d) else m.group(0)


def mask_pii(s):
    # -> (masked string, hits). Non-str -> (s, 0).
    if not isinstance(s, str):
        return s, 0
    try:
        hits = [0]

        def rep(tag):
            def f(m):
                hits[0] += 1
                return tag
            return f

        def card(m):
            r = _card(m)
            if r == CARD:
                hits[0] += 1
            return r
        out = _RRN_RX.sub(rep(RRN), s)
        out = _CARD_RX.sub(card, out)
        out = _PHONE_RX.sub(rep(PHONE), out)
        out = _EMAIL_RX.sub(rep(EMAIL), out)
        return out, hits[0]
    except Exception:  # noqa: BLE001
        return s, 0


def mask(s):
    return mask_pii(s)[0]
