import json


ACTS = {
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
}


def parse_structured(text):
    """Return an act and price, or None for an unreadable message."""
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(message, dict):
        return None

    act = message.get("performative")
    content = message.get("content")

    if not isinstance(act, str) or act not in ACTS:
        return None

    if not isinstance(content, dict):
        return None

    price = None
    if act == "propose":
        price = content.get("price")
        if type(price) is not int:
            return None

    return {"performative": act, "price": price}


if __name__ == "__main__":
    offer = '{"performative":"propose","content":{"price":30}}'
    assert parse_structured(offer) == {
        "performative": "propose",
        "price": 30,
    }

    missing_price = '{"performative":"propose","content":{}}'
    assert parse_structured(missing_price) is None

    assert parse_structured("I offer 30.") is None

    acceptance = '{"performative":"accept-proposal","content":{}}'
    assert parse_structured(acceptance) == {
        "performative": "accept-proposal",
        "price": None,
    }

    print("All 4 structured-parser checks passed. No API calls.")