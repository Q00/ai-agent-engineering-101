"""Local unit tests for the week-04 protocol readers."""

import unittest

import protocol


class ProtocolTests(unittest.TestCase):
    def test_structured_propose_needs_no_reader(self) -> None:
        result = protocol.read_structured(
            '{"performative":"propose","content":{"price":120}}'
        )
        self.assertEqual(
            (result.performative, result.price, result.ok, result.reader_calls),
            ("propose", 120, True, 0),
        )

    def test_structured_rejects_surrounding_markdown(self) -> None:
        result = protocol.read_structured(
            '```json\n{"performative":"refuse","content":{"price":null}}\n```'
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reader_calls, 0)

    def test_tagged_non_propose_needs_no_reader(self) -> None:
        result = protocol.read_tagged("(accept-proposal) I accept.", None)
        self.assertEqual(
            (result.performative, result.price, result.ok, result.reader_calls),
            ("accept-proposal", None, True, 0),
        )

    def test_tagged_propose_calls_reader_for_price(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_reader(system: str, user: str) -> str:
            calls.append((system, user))
            return '{"price":95}'

        result = protocol.read_tagged(
            "(propose) I can offer 95.",
            fake_reader,
        )
        self.assertEqual(
            (result.performative, result.price, result.ok, result.reader_calls),
            ("propose", 95, True, 1),
        )
        self.assertEqual(len(calls), 1)

    def test_tagged_rejects_multiple_tags(self) -> None:
        result = protocol.read_tagged(
            "(propose) 95, or (refuse) I leave.",
            lambda _system, _user: '{"price":95}',
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reader_calls, 0)

    def test_free_reader_labels_final_message(self) -> None:
        def fake_reader(_system: str, user: str) -> str:
            self.assertEqual(user, "[buyer] Could we do 80?")
            return '{"performative":"propose","price":80}'

        result = protocol.read_free(
            [("buyer", "Could we do 80?")],
            fake_reader,
        )
        self.assertEqual(
            (result.performative, result.price, result.ok, result.reader_calls),
            ("propose", 80, True, 1),
        )


if __name__ == "__main__":
    unittest.main()
