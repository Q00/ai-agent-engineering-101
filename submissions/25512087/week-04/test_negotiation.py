import unittest

from negotiation import (
    ParsedMessage,
    evaluate_outcome,
    parse_structured,
    parse_tagged,
)


class ProtocolParserTests(unittest.TestCase):
    def test_structured_message_is_parsed_without_reader(self):
        parsed = parse_structured(
            '{"performative":"propose","content":{"price":85}}'
        )
        self.assertEqual(parsed, ParsedMessage("propose", 85))

    def test_tagged_propose_uses_price_reader_once(self):
        calls = []

        def reader(text):
            calls.append(text)
            return 90

        parsed = parse_tagged("(propose) I can do that for ninety", reader)
        self.assertEqual(parsed, ParsedMessage("propose", 90))
        self.assertEqual(len(calls), 1)

    def test_tagged_refuse_does_not_call_price_reader(self):
        def reader(_):
            raise AssertionError("reader is not needed for refuse")

        self.assertEqual(parse_tagged("(refuse) I will pass", reader), ParsedMessage("refuse", None))

    def test_invalid_structured_message_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_structured('{"performative":"propose","content":{}}')


class OutcomeTests(unittest.TestCase):
    def test_possible_deal_inside_private_limits_is_correct(self):
        scenario = {"reserve": 80, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "deal", 90), (1, 0))

    def test_impossible_deal_is_incorrect_and_violation_is_reported(self):
        scenario = {"reserve": 120, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "deal", 110), (0, 1))

    def test_no_deal_is_correct_when_limits_do_not_overlap(self):
        scenario = {"reserve": 120, "budget": 100}
        self.assertEqual(evaluate_outcome(scenario, "no_deal", None), (1, 0))


if __name__ == "__main__":
    unittest.main()
