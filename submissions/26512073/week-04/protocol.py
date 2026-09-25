import json
import re

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
    TAG_PATTERN = re.compile(
    r"\A\((propose|accept-proposal|reject-proposal|refuse)\)\s+(.+)\Z",
    re.DOTALL,
)


def split_tagged(text):
    """Read the leading tag and keep the remaining sentence."""
    match = TAG_PATTERN.fullmatch(text.strip())
    if match is None:
        return None

    act, sentence = match.groups()
    sentence = sentence.strip()

    if not sentence:
        return None

    # A second leading tag violates the one-tag format.
    if TAG_PATTERN.match(sentence):
        return None

    return {"performative": act, "text": sentence}
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
    assert split_tagged("(propose) I offer 35.") == {
        "performative": "propose",
        "text": "I offer 35.",
    }

    assert split_tagged("(refuse) I am leaving.") == {
        "performative": "refuse",
        "text": "I am leaving.",
    }

    assert split_tagged("[propose] I offer 35.") is None
    assert split_tagged("(propose) (refuse) I am leaving.") is None

    print("All 4 tagged-format checks passed. No API calls.")