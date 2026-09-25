from types import SimpleNamespace
from unittest.mock import patch

from reader import MessageReader
from tools_shared import Meter


def read_message(condition, text, fake_reply=None):
    conversation = [{"speaker": "buyer", "text": text}]
    reader = MessageReader(Meter(), log=lambda message: None)

    with patch("reader.Chat") as mock_chat:
        mock_chat.return_value.send.return_value = SimpleNamespace(
            text=fake_reply
        )
        result = reader.read(condition, conversation)
        actual_calls = mock_chat.return_value.send.call_count

    assert reader.reader_calls == actual_calls
    return result, actual_calls


if __name__ == "__main__":
    result, calls = read_message(
        "structured",
        '{"performative":"propose","content":{"price":35}}',
    )
    assert result == {"performative": "propose", "price": 35}
    assert calls == 0
    print("PASS: structured uses no reader call")

    result, calls = read_message(
        "tagged",
        "(reject-proposal) That price is too high.",
    )
    assert result == {
        "performative": "reject-proposal",
        "price": None,
    }
    assert calls == 0
    print("PASS: tagged rejection uses no reader call")

    result, calls = read_message(
        "tagged",
        "(propose) Your price of 50 is too high. I offer 35.",
        '{"price":35}',
    )
    assert result == {"performative": "propose", "price": 35}
    assert calls == 1
    print("PASS: tagged proposal uses one reader call")

    result, calls = read_message(
        "free",
        "Your price of 50 is too high. I offer 35.",
        '{"performative":"propose","price":35}',
    )
    assert result == {"performative": "propose", "price": 35}
    assert calls == 1
    print("PASS: free message uses one reader call")

    result, calls = read_message(
        "free",
        "What is your asking price?",
        "This is not JSON.",
    )
    assert result is None
    assert calls == 1
    print("PASS: unreadable reader reply still counts its call")

    result, calls = read_message(
        "tagged",
        "[propose] I offer 35.",
    )
    assert result is None
    assert calls == 0
    print("PASS: invalid tag needs no reader call")

    print("All 6 reader checks passed. No real API calls.")