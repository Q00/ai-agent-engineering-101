import unittest

from protocol import (
    FORMAT_PARAGRAPHS,
    parse_free,
    parse_structured,
    parse_tagged,
    role_prompt,
    verdict,
)


class ProtocolTests(unittest.TestCase):
    def test_only_final_format_paragraph_changes(self):
        scenario = {"item": "bike", "reserve": 80, "budget": 120}
        prompts = [role_prompt("buyer", scenario, condition) for condition in FORMAT_PARAGRAPHS]
        bases = [prompt.rsplit("\n\n", 1)[0] for prompt in prompts]
        self.assertEqual(len(set(bases)), 1)

    def test_free_uses_reader_result(self):
        parsed = parse_free(
            "I can pay 100.",
            lambda _: '{"performative":"propose","price":100}',
        )
        self.assertEqual((parsed.performative, parsed.price), ("propose", 100))

    def test_tagged_uses_regex_for_act_and_reader_for_proposal_price(self):
        calls = []
        parsed = parse_tagged(
            "(propose) I can pay 100.",
            lambda text: calls.append(text) or '{"performative":"propose","price":100}',
        )
        self.assertEqual((parsed.performative, parsed.price), ("propose", 100))
        self.assertEqual(calls, ["I can pay 100."])

        parsed = parse_tagged(
            "(refuse) We cannot reach a deal.",
            lambda _: self.fail("reader must not be called for refuse"),
        )
        self.assertEqual(parsed.performative, "refuse")

    def test_structured_is_strict(self):
        good = parse_structured(
            '{"performative":"propose","content":{"price":100}}'
        )
        bad = parse_structured(
            '{"performative":"propose","content":{"price":"100"}}'
        )
        self.assertEqual(good.price, 100)
        self.assertIsNotNone(bad.error)

    def test_verdict_distinguishes_incorrect_from_violation(self):
        possible = {"reserve": 80, "budget": 120}
        impossible = {"reserve": 150, "budget": 130}
        self.assertEqual(verdict(possible, "deal", 100, 2, 0, 0).correct, 1)
        invalid = verdict(possible, "deal", 70, 2, 0, 0)
        self.assertEqual((invalid.correct, invalid.violation), (0, 1))
        no_deal = verdict(impossible, "no_deal", None, 2, 0, 0)
        self.assertEqual((no_deal.correct, no_deal.violation), (1, 0))
        self.assertEqual(verdict(possible, "open", None, 6, 0, 0).correct, 0)


if __name__ == "__main__":
    unittest.main()
