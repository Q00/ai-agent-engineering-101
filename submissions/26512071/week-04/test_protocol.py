"""Offline tests for the three protocol readers."""

import unittest

from protocol import parse_free, parse_structured, parse_tagged


class FakeReader:
    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def __call__(self, system_prompt: str, message: str) -> str:
        self.calls += 1
        return self.response


class ProtocolTests(unittest.TestCase):
    def test_free_reader_labels_every_message(self):
        reader = FakeReader('{"performative":"accept-proposal","price":null}')
        parsed = parse_free("That works for me.", reader)
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.performative, "accept-proposal")
        self.assertEqual(parsed.reader_calls, 1)
        self.assertEqual(reader.calls, 1)

    def test_tagged_reader_is_used_only_for_propose(self):
        accept_reader = FakeReader("should not be used")
        accepted = parse_tagged("(accept-proposal) Deal.", accept_reader)
        self.assertTrue(accepted.valid)
        self.assertEqual(accept_reader.calls, 0)

        price_reader = FakeReader('{"price":72}')
        proposed = parse_tagged("(propose) I can offer $72.", price_reader)
        self.assertTrue(proposed.valid)
        self.assertEqual(proposed.price, 72)
        self.assertEqual(price_reader.calls, 1)

    def test_tagged_rejects_a_missing_tag(self):
        reader = FakeReader('{"price":72}')
        parsed = parse_tagged("I can offer $72.", reader)
        self.assertFalse(parsed.valid)
        self.assertEqual(reader.calls, 0)

    def test_structured_needs_no_reader(self):
        parsed = parse_structured(
            '{"performative":"propose","content":{"price":88}}'
        )
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.price, 88)
        self.assertEqual(parsed.reader_calls, 0)

    def test_structured_rejects_price_on_acceptance(self):
        parsed = parse_structured(
            '{"performative":"accept-proposal","content":{"price":88}}'
        )
        self.assertFalse(parsed.valid)


if __name__ == "__main__":
    unittest.main()
