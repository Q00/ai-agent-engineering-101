"""The buyer's brain: the harness decides each move through react.run().

Strategy (given to the model in the prompt, not coded as a rule):
open low (60% of the budget); take the seller's ask once it is within the
budget; otherwise offer the full budget once; if the seller still will not
come down, walk away.

buyer.py calls decide() once per seller reply that still waits for input.
"""
import time

import react

OPENING_SHARE = 0.6

SYSTEM = """You are a careful shopping agent buying from a seller agent.
Your goal: buy the item within your budget, as cheaply as you reasonably can, or walk away.

Strategy:
1. If you have made no offer yet: offer your opening offer.
2. Else, if the seller's ask is within your budget: offer exactly the ask (that closes the deal).
3. Else, if you have not offered your full budget yet: offer your budget.
4. Else (you already offered your full budget and the seller is still above it): walk away.
If the seller's message says the deal is off or the item is not for sale, walk away.

Most turns need no tool: use one only when you are unsure."""


def situation(deal):
    opening = round(deal["budget"] * OPENING_SHARE)
    offers = [h["price"] for h in deal["history"] if h["from"] == "buyer" and h["price"] is not None]
    asks = [h["price"] for h in deal["history"] if h["from"] == "seller" and h["price"] is not None]
    seller_said = next((h["text"] for h in reversed(deal["history"]) if h["from"] == "seller"), "")
    skills = "; ".join(f"{s['name']}: {s['description']} (examples: {s['examples']})" for s in deal["skills"])
    lines = [
        f"You are buying one {deal['item']} from {deal['seller']!r}: {deal['seller_description']}",
        f"The seller's card says it can: {skills}",
        f"Your budget (the most you may ever pay): {deal['budget']}. Your opening offer: {opening}.",
        f"Your offers so far: {offers or 'none yet'}.",
        f"Seller's asks so far: {asks or 'none yet'}.",
        f"Seller's latest message: {seller_said!r} (read as an ask of: "
        f"{deal['ask'] if deal['ask'] is not None else 'no number'}).",
    ]
    for g in deal.get("guards", []):
        lines.append(f"Earlier in this deal your safety check overrode you: {g['reason']}")
    return "\n".join(lines)


def decide(deal, wallet):
    """Return {"action": "offer", "price"} | {"action": "walk_away"}.

    deal: {"item", "budget", "seller", "seller_description", "skills", "ask", "seconds_left",
           "history": [{"from", "text", "price"}], "guards": [...]}
    Returns {"action": "none"} when the model gives no valid final action; buyer.py's guard then walks away.
    """
    tools = {
        "check_offer": {"args": "(price)", "description": "may I offer this price (is it within my budget)?",
                        "fn": lambda a: wallet.check_offer(float(a["price"]))},
    }
    finals = {
        "offer": {"args": "(price)", "description": "send this price to the seller"},
        "walk_away": {"args": "()", "description": "stop negotiating without buying"},
    }

    def validate(action, args):
        if action != "offer":
            return True, "ok"
        try:
            price = float(args["price"])
        except (KeyError, TypeError, ValueError):
            return False, "offer needs a number as price"
        ok, reason = wallet.check_offer(price)
        if not ok:
            return ok, reason
        # Stop the loop: the full budget was already offered and the seller did not come down to it.
        if (deal["budget"] in wallet.offers and deal["ask"] is not None and deal["ask"] > deal["budget"]
                and price >= deal["budget"]):
            return False, "you already offered your full budget and the seller is still above it; walk away"
        return True, "ok"

    d, trace = react.run(SYSTEM, situation(deal), tools, finals, validate,
                         max_steps=3, deadline=time.time() + deal.get("seconds_left", 230))
    deal.setdefault("thoughts", []).append(trace)
    return d or {"action": "none"}
