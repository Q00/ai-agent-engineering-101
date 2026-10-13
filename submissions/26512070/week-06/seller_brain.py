"""The seller's brain. Write this one yourself.

seller.py calls decide() once per buyer message, in a worker thread.
"""


def decide(deal, buyer_text):
    """Decide the seller's next move.

    deal: {"item": "lamp", "reserve": 100, "ask": 130 or None (None = first message),
           "last_offer": 80 or None, "history": [{"from": "buyer"|"seller", "text": ..., "price": ...}, ...]}
    buyer_text: the buyer's latest message, as sent.

    Return one of:
        {"action": "counter", "price": 115, "say": "..."}   -> INPUT_REQUIRED; the price goes last in the message
        {"action": "accept",  "say": "..."}                  -> COMPLETED with a receipt at deal["last_offer"]
        {"action": "reject",  "say": "..."}                  -> REJECTED

    TODO: a ReAct loop around model.call_model with your own tools. Never ask or
    sell below deal["reserve"] -- seller.py does not check it for you.
    """
    raise NotImplementedError("seller_brain.decide")
