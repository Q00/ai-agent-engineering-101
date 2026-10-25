"""Offline regression tests for the corrected negotiation readers."""
import unittest

from negotiate import run_episode
from protocol import ReaderMeter, read_structured, read_tagged


class ProtocolTests(unittest.TestCase):
    def test_acceptance_with_price_is_valid(self):
        self.assertEqual(read_structured('{"performative":"accept-proposal","content":{"price":70}}'),
                         ("accept-proposal", None, True))

    def test_proposal_requires_integer(self):
        self.assertEqual(read_structured('{"performative":"propose","content":{"price":null}}'),
                         (None, None, False))

    def test_tagged_reader_extracts_only_price(self):
        meter = ReaderMeter()
        def caller(system, user):
            self.assertIn('do not classify the act again', system)
            return '{"price":85}'
        self.assertEqual(read_tagged('(propose) I accept 85.', [], caller, meter),
                         ('propose', 85, True))
        self.assertEqual(meter.calls, 1)

    def test_acceptance_uses_opponents_recorded_price(self):
        outputs = iter(['{"performative":"propose","content":{"price":70}}',
                        '{"performative":"accept-proposal","content":{"price":999}}'])
        result = run_episode({'id':'test', 'item':'keyboard', 'reserve':60, 'budget':80},
                             'structured', lambda system, user: next(outputs), lambda text: None)
        self.assertEqual((result.outcome, result.price, result.correct, result.turns),
                         ('deal', 70, 1, 2))


if __name__ == '__main__':
    unittest.main()
