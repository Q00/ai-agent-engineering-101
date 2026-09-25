import json
from types import SimpleNamespace
from unittest.mock import patch

from negotiation import run_episode


def message(act, price=None):
    content = {"price": price} if act == "propose" else {}
    return json.dumps({"performative": act, "content": content})


def check(name, replies, expected, reserve=30, budget=45):
    scenario = {
        "id": 1,
        "item": "desk lamp",
        "reserve": reserve,
        "budget": budget,
    }
    scripted = iter(replies)
    agents = []

    class FakeChat:
        def __init__(self, system, meter, tools=False):
            self.received = []
            agents.append(self)

        def add_user(self, text):
            self.received.append(text)

        def send(self):
            return SimpleNamespace(text=next(scripted))

    with patch("negotiation.Chat", FakeChat):
        result = run_episode(
            scenario, "structured", log=lambda text: None
        )

    for field, value in expected.items():
        assert result[field] == value, (
            f"{name}: {field} was {result[field]!r}, expected {value!r}"
        )

    assert result["reader_calls"] == 0

    # Buyer is agents[0]; seller is agents[1].
    # Every original message must reach the other agent.
    assert agents[1].received == replies[:result["turns"]][0::2]
    assert agents[0].received == (
        ["Begin the negotiation."]
        + replies[:result["turns"]][1::2]
    )

    print(f"PASS: {name}")


if __name__ == "__main__":
    check(
        "Acceptance uses the other side's latest offer",
        [
            message("propose", 25),
            message("propose", 40),
            message("accept-proposal"),
        ],
        {
            "outcome": "deal",
            "price": 40,
            "correct": 1,
            "violation": 0,
            "turns": 3,
        },
    )

    check(
        "A deal above budget is recorded as a violation",
        [message("propose", 60), message("accept-proposal")],
        {"outcome": "deal", "correct": 0, "violation": 1},
    )

    check(
        "A deal below reserve is recorded as a violation",
        [message("propose", 20), message("accept-proposal")],
        {"outcome": "deal", "correct": 0, "violation": 1},
    )

    check(
        "Refusal is correct when no deal is possible",
        [message("refuse")],
        {"outcome": "no_deal", "correct": 1, "violation": 0},
        reserve=50,
        budget=45,
    )

    check(
        "Refusal is incorrect when a deal is possible",
        [message("refuse")],
        {"outcome": "no_deal", "correct": 0},
    )

    check(
        "Unreadable text is delivered and counted",
        [
            "This is not JSON.",
            message("propose", 35),
            message("accept-proposal"),
        ],
        {
            "outcome": "deal",
            "price": 35,
            "correct": 1,
            "format_errors": 1,
        },
    )

    check(
        "Acceptance without an offer does not create a deal",
        [message("accept-proposal"), message("refuse")],
        {"outcome": "no_deal", "format_errors": 1, "turns": 2},
    )

    check(
        "Eight messages without agreement ends as open",
        [message("propose", 35)] * 8,
        {"outcome": "open", "correct": 0, "turns": 8},
    )

    check(
        "A price exactly at both limits is valid",
        [message("propose", 50), message("accept-proposal")],
        {"outcome": "deal", "correct": 1, "violation": 0},
        reserve=50,
        budget=50,
    )

    print("All 9 negotiation checks passed. No API calls.")