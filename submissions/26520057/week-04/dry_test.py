"""Offline check of the protocol layer: a scripted fake model replaces the API.
No files are written. Run: python dry_test.py

Script per condition: buyer proposes 300, seller proposes 500, buyer sends one
unreadable message, seller proposes 450, buyer accepts -> deal at 450.
"""

import json

import negotiation as ng

SCRIPT = [("propose", 300), ("propose", 500), ("garbage", None), ("propose", 450),
          ("accept-proposal", None)]


def render(condition, act, price):
    if act == "garbage":
        return {"free": "Hmm, let me think.", "tagged": "Let me think about it.",
                "structured": "```json\n{}\n```"}[condition]
    if condition == "structured":
        return json.dumps({"performative": act, "content": {"price": price}})
    words = f"I can do ${price}." if price else "Deal, I accept."
    return f"({act}) {words}" if condition == "tagged" else words


def fake_model(condition):
    turn = {"n": 0}

    def call(system, messages, max_tokens):
        if system == ng.READER_SYSTEM:
            text = messages[0]["content"]
            if "think" in text:
                return "not json"
            if "accept" in text:
                return '{"performative": "accept-proposal", "price": null}'
            return json.dumps({"performative": "propose",
                               "price": int(text.split("$")[1].rstrip("."))})
        if system == ng.PRICE_READER_SYSTEM:
            return messages[0]["content"].split("$")[1].rstrip(".")
        act, price = SCRIPT[turn["n"]]
        turn["n"] += 1
        return render(condition, act, price)
    return call


def main():
    scenario = {"id": "t", "item": "test item", "reserve": 400, "budget": 480}
    expect_reader = {"free": 5, "tagged": 3, "structured": 0}
    for c in ng.CONDITIONS:
        ng.call_model = fake_model(c)
        r = ng.run_episode(c, scenario, log=lambda *_: None)
        assert (r["outcome"], r["price"], r["turns"], r["format_errors"], r["correct"],
                r["violation"]) == ("deal", 450, 5, 1, 1, 0), r
        assert r["reader_calls"] == expect_reader[c], r
        print(f"ok  {c}: {r}")
    print("dry test passed")


if __name__ == "__main__":
    main()
