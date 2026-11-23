"""The seller's brain: the harness decides each reply through react.run().

Strategy (given to the model in the prompt, not coded as a rule):
list price first; accept any offer at or above the reserve; otherwise counter
once at the middle price, then at the reserve.

seller.py calls decide() once per buyer message, in a worker thread.
"""
import time

import react

SYSTEM = """You are the sales agent of a small furniture shop, negotiating with a buyer agent.
Your goal: sell the item at the best price the buyer will pay, and never below the reserve.

Strategy:
1. First reply of a deal (you have made no ask yet): counter at the list price.
2. If the buyer's latest offer is at or above the reserve: accept it.
3. Otherwise, if you have not asked the middle price yet: counter at the middle price.
4. Otherwise: counter at the reserve.

Most turns need no tool: use one only when you are unsure.
In "say", write one short friendly sentence with no numbers in it (the shop adds your price)."""


def situation(deal, buyer_text):
    mid = round((deal["list_price"] + deal["reserve"]) / 2)
    asks = [h["price"] for h in deal["history"] if h["from"] == "seller" and h["price"] is not None]
    offers = [h["price"] for h in deal["history"] if h["from"] == "buyer" and h["price"] is not None]
    lines = [
        f"Item: {deal['item']}. Stock: {deal['stock']}.",
        f"Prices for this item: list price {deal['list_price']}, middle price {mid}, "
        f"reserve {deal['reserve']} (the lowest you may ever ask or sell for).",
        f"Your asks so far in this deal: {asks or 'none yet'}.",
        f"Buyer's offers so far: {offers or 'none yet'}.",
        f"Buyer's latest message: {buyer_text!r} "
        f"(read as an offer of: {deal['last_offer'] if deal['last_offer'] is not None else 'no number'}).",
    ]
    for g in deal.get("guards", []):
        lines.append(f"Earlier in this deal the shop's safety check overrode you: {g['reason']}")
    return "\n".join(lines)


def decide(deal, buyer_text, inventory):
    """Return {"action": "counter", "price", "say"} | {"action": "accept", "say"} | {"action": "reject", "say"}.

    deal: {"item", "reserve", "list_price", "stock", "ask" (None on the first message), "last_offer",
           "seconds_left", "history": [{"from", "text", "price"}], "guards": [...]}
    Returns {"action": "none"} when the model gives no valid final action; seller.py's guard then holds the price.
    """
    item = deal["item"]
    tools = {
        "get_item": {"args": "()", "description": "reserve, list price and stock of this item",
                     "fn": lambda a: inventory.get(item)},
        "check_ask": {"args": "(price)", "description": "may the shop ask this price?",
                      "fn": lambda a: inventory.check_ask(item, int(a["price"]))},
    }
    finals = {
        "counter": {"args": "(price, say)", "description": "ask this price and wait for the buyer"},
        "accept": {"args": "(say)", "description": "sell at the buyer's latest offer"},
        "reject": {"args": "(say)", "description": "end the negotiation without a deal"},
    }

    def validate(action, args):
        if deal["ask"] is None and action != "counter":
            return False, "your first reply must be a counter at the list price"
        if action == "counter":
            try:
                price = int(args["price"])
            except (KeyError, TypeError, ValueError):
                return False, "counter needs a whole-number price"
            return inventory.check_ask(item, price)
        if action == "accept":
            if deal["last_offer"] is None:
                return False, "there is no offer to accept"
            return inventory.check_sale(item, deal["last_offer"])
        return True, "ok"

    d, trace = react.run(SYSTEM, situation(deal, buyer_text), tools, finals, validate,
                         max_steps=3, deadline=time.time() + deal.get("seconds_left", 150))
    deal.setdefault("thoughts", []).append(trace)
    return d or {"action": "none"}
