import unittest

from run import run_episode


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {
            "id": "test",
            "item": "bike",
            "reserve": 80,
            "budget": 120,
        }
        self.events = []

    def test_structured_deal_needs_no_reader(self):
        answers = iter([
            '{"performative":"propose","content":{"price":100}}',
            '{"performative":"accept-proposal","content":{}}',
        ])
        result = run_episode(
            self.scenario,
            "structured",
            4,
            lambda *_: next(answers),
            lambda _: self.fail("structured must not call a reader"),
            lambda event, **data: self.events.append((event, data)),
        )
        self.assertEqual((result.outcome, result.price, result.correct), ("deal", 100, 1))
        self.assertEqual((result.turns, result.reader_calls), (2, 0))

    def test_tagged_reads_only_proposal_price(self):
        answers = iter(["(propose) I offer 100.", "(accept-proposal) Agreed."])
        reader_inputs = []
        result = run_episode(
            self.scenario,
            "tagged",
            4,
            lambda *_: next(answers),
            lambda raw: reader_inputs.append(raw) or '{"performative":"propose","price":100}',
            lambda *_args, **_kwargs: None,
        )
        self.assertEqual(result.reader_calls, 1)
        self.assertEqual(reader_inputs, ["I offer 100."])

    def test_invalid_acceptance_is_a_format_error(self):
        answers = iter([
            '{"performative":"accept-proposal","content":{}}',
            '{"performative":"refuse","content":{}}',
        ])
        result = run_episode(
            self.scenario, "structured", 2, lambda *_: next(answers),
            lambda _: "", lambda *_args, **_kwargs: None,
        )
        self.assertEqual(result.outcome, "no_deal")
        self.assertEqual(result.format_errors, 1)


if __name__ == "__main__":
    unittest.main()
