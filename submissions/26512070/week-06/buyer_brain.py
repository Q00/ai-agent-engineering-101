"""The buyer's brain. Write this one yourself.

buyer.py calls decide() once per seller reply that still waits for input.
"""


def decide(deal, wallet):
    """Decide the buyer's next move.

    deal: {"item": "lamp", "budget": 120, "seller": card name, "seller_description": card description,
           "skills": [{"name", "description", "examples"}, ...] from the seller's Agent Card,
           "ask": 130 (last number in the seller's latest message, or None),
           "seconds_left": 230 (until buyer.py walks away; the trade test kills it at 300 s),
           "history": [{"from": "buyer"|"seller", "text": ..., "price": ...}, ...],
           "guards": [...] (earlier times buyer.py's guard overrode you in this deal)}
    wallet: wallet.Wallet -- tools you may hand to the model:
        wallet.check_offer(price) -> (ok, reason)  not over the budget
        wallet.snapshot()         -> budget, offers so far, purchases

    Return one of:
        {"action": "offer", "price": 110}   -> sent on the same taskId
        {"action": "walk_away"}             -> CancelTask, no deal

    TODO: a ReAct loop around model.call_model with your own tools. Never offer
    above deal["budget"]. buyer.py's guard() catches it if you do, but only by
    walking away -- it is a safety net, not a negotiator.
    """
    raise NotImplementedError("buyer_brain.decide")
