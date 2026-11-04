"""Accept only one unambiguous affordable asking price; checkout remains authoritative."""

import re


def decision(text, cap, last_offer=None):
    matches = re.findall(r"(?<![\w])(?:asking|counteroffer|asking price|offer price|가격|제안가)\s*[:：]?\s*(\d[\d,]*)(?![\d.])", text, re.I)
    if any(not re.fullmatch(r"(?:[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)", p) for p in matches):
        return None
    prices = {int(p.replace(",", "")) for p in matches}
    if len(prices) != 1:
        return None
    price = prices.pop()
    if not 0 < price <= cap:
        return None
    if last_offer == price:
        return {"action": "walk_away", "price": None, "reason": "Same offer repeated without settlement; no blind retry."}
    return {"action": "offer", "price": price, "reason": "Unambiguous current ask within coded value/quality cap; checkout will verify terms."}
