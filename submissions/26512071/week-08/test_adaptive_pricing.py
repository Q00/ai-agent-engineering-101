"""Deterministic policy tests; no network/model calls."""
import unittest
try:
    import adaptive_pricing as p
except ModuleNotFoundError:
    p = None

class PricingTests(unittest.TestCase):
    def test_item_batches_require_minimum_and_ignore_baseline(self):
        self.assertIsNotNone(p, 'adaptive pricing feature is missing')
        rows = [{'task':str(i), 'item':'lamp', 'policy':'adaptive', 'outcome':'deal'} for i in range(9)]
        self.assertEqual(p.margin(rows, 'lamp'), .03)
        self.assertEqual(p.margin(rows + [dict(rows[0])], 'lamp'), .03)
        rows.append({'task':'9', 'item':'lamp', 'policy':'adaptive', 'outcome':'deal'})
        self.assertAlmostEqual(p.margin(rows, 'lamp'), .04)
        self.assertEqual(p.margin(rows, 'chair'), .03)
        self.assertEqual(p.margin([dict(r, policy='baseline') for r in rows], 'lamp'), .03)

    def test_bounded_response_margins_and_safe_quotes(self):
        self.assertTrue(hasattr(p, 'quote'), 'safe quote feature is missing')
        for outcome, expected in [('deal', .05), ('reject', 0)]:
            rows = [dict(task=str(i), item='lamp', policy='adaptive', outcome=outcome) for i in range(100)]
            self.assertEqual(p.margin(rows, 'lamp'), expected)
        self.assertEqual(p.quote(100, .03), 103)
        self.assertEqual(p.quote(100, .05, previous=102), 100)
        self.assertEqual(p.quote(100, -1, previous=100), 100)
        self.assertEqual(p.quote(100, 9), 105)

if __name__ == '__main__':
    unittest.main()
