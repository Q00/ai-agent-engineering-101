"""Compact shop decision inputs without changing policy or making a price."""
import math
import re


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    marker = " [... omitted ...] "
    half = (limit - len(marker)) // 2
    return text[:half] + marker + text[-half:]


def price_memory(history, offer):
    """Only parse numeric bids and the shop's own terminal 'Asking price' label.

    Do not infer prices from arbitrary prose, SKU numbers or warranty hours.
    Unrecognized messages remain short, explicitly untrusted excerpts.
    """
    window = history if len(history) <= 7 else history[:1] + history[-6:]
    offers, asks, other = [], [], []
    for index, message in enumerate(window):
        role, text = message["role"], message["text"].strip()
        if role == "buyer" and re.fullmatch(r"[0-9]{1,12}", text):
            value = int(text)
            if index != len(window) - 1 or value != offer:
                offers.append(value)
        elif role == "seller" and (match := re.search(r"Asking price: ([0-9]{1,12})\.\s*$", text)):
            asks.append(int(match[1]))
        else:
            other.append({"role": role, "text": excerpt(text, 240)})
    return {"prior_offers": offers, "prior_asks": asks,
            "untrusted_excerpts": other[:1] + other[-1:] if len(other) > 1 else other}


def inventory_context(product, offer, history, last_ask, terms, negotiation, advice, *, now):
    """Project a private snapshot; full terms and validation stay in the caller."""
    facts = {k: product.get(k) for k in ("sku", "name", "category", "kind", "cost", "list_price", "grade")}
    facts["name"] = excerpt(str(facts["name"] or ""), 160)
    facts["category"] = excerpt(str(facts["category"] or ""), 100)
    supplied, expiry = product.get("supplied_at"), product.get("expires_at")
    facts["age_hours"] = (round((now - supplied) / 3600, 2)
                          if type(supplied) in (int, float) and math.isfinite(supplied) and supplied <= now else None)
    if product.get("kind") == "food":
        facts["hours_to_expiry"] = (round((expiry - now) / 3600, 3)
                                     if type(expiry) in (int, float) and math.isfinite(expiry) else None)
    plan = {k: negotiation[k] for k in ("stage", "offer_number", "discount_reason", "previous_best_offer")
            if k in negotiation}
    if negotiation.get("opening_bounds"):
        plan["opening_bounds"] = {k: negotiation["opening_bounds"][k]
                                   for k in ("minimum", "maximum", "target", "source")
                                   if k in negotiation["opening_bounds"]}
    return {
        "product": facts, "price_floor": negotiation["price_floor"],
        "sales_terms": {"price_floor": terms["price_floor"], "warranty_hours": terms["warranty_hours"]},
        "negotiation": plan, "buyer_offer": offer, "last_ask": last_ask,
        "history": price_memory(history, offer), "sales_advisor": advice,
    }
