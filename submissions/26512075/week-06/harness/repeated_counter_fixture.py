from harness.example_decisions import decide as baseline



def decide(request: dict) -> dict:
    if request["side"] == "seller" and "incomingText" in request:
        offer = request.get("numericOffer")
        if offer is not None and offer >= request["currentAsk"] and not request.get("validationFeedback"):
            return {"action": "offer", "price": request["currentAsk"],
                    "parsed_offer": offer}
    return baseline(request)

