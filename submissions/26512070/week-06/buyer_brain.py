"""The buyer's brain. Write this one yourself.

buyer.py calls decide() once per seller reply that still waits for input.
"""


def decide(deal):
    """Decide the buyer's next move.

    deal: {"item": "lamp", "budget": 120, "seller": card name, "skill": card skill description,
           "ask": 130 (last number in the seller's latest message, or None),
           "history": [{"from": "buyer"|"seller", "text": ..., "price": ...}, ...]}

    Return one of:
        {"action": "offer", "price": 110}   -> sent on the same taskId
        {"action": "walk_away"}             -> CancelTask, no deal

    TODO: a ReAct loop around model.call_model with your own tools. Never offer
    above deal["budget"] -- buyer.py does not check it for you.
    """
    raise NotImplementedError("buyer_brain.decide")
