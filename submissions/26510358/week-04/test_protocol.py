"""Offline checks for protocol semantics; no key or model calls required."""
import unittest

from negotiation import _read_reader_json, _read_structured, episode, read_message, score


class FakeChat:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def complete(self, system, messages, log):
        self.calls += 1
        return next(self.replies)


class ProtocolTests(unittest.TestCase):
    def test_reader_keeps_acceptance_with_stated_price(self):
        self.assertEqual(_read_reader_json('{"performative":"accept-proposal","price":40}'),
                         ("accept-proposal", 40))
        self.assertEqual(_read_reader_json('{"performative":"propose","price":null}'), None)

    def test_tag_is_authoritative_and_reader_supplies_price_only(self):
        chat = FakeChat(['{"performative":"reject-proposal","price":42}'])
        parsed = read_message("tagged", "(propose) I offer 42.",
                              [("buyer", "(propose) I offer 42.")], chat, lambda _: None)
        self.assertEqual(parsed, ("propose", 42))
        self.assertEqual(chat.calls, 1)

    def test_structured_rejects_extra_object_and_invalid_price(self):
        self.assertIsNone(_read_structured('{"performative":"propose","content":{"price":40}} '
                                            '{"performative":"refuse","content":{"price":null}}'))
        self.assertIsNone(_read_structured('{"performative":"propose","content":{"price":true}}'))

    def test_acceptance_uses_other_sides_last_proposal(self):
        chat = FakeChat(['{"performative":"propose","content":{"price":120}}',
                         '{"performative":"accept-proposal","content":{"price":null}}'])
        result = episode({"item": "a bike", "reserve": 100, "budget": 150},
                         "structured", chat, lambda _: None)
        self.assertEqual((result["outcome"], result["price"], result["correct"]),
                         ("deal", 120, 1))
        self.assertEqual((result["turns"], result["reader_calls"]), (2, 0))

    def test_open_is_not_a_correct_no_deal(self):
        scenario = {"reserve": 90, "budget": 70}
        self.assertEqual(score(scenario, "open", None), (0, 0, 0))
        self.assertEqual(score(scenario, "no_deal", None), (0, 1, 0))


if __name__ == "__main__":
    unittest.main()
