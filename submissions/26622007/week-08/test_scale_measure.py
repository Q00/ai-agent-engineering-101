import unittest

from scale_measure import summarize


class ScaleMeasureTest(unittest.TestCase):
    def measure(self, outgoing=(), ledger=(), incoming=None, now=350):
        windows = [{'ts': t + 30, 'opens_at': t, 'closes_at': t + 20} for t in (100, 200, 300, 400)]
        incoming = incoming if incoming is not None else [
            {'ts': 120, 'supplied_at': 110, 'unit': 'a', 'sku': 'G', 'first_tick': False}]
        return summarize(windows, incoming, outgoing, ledger, {'1': {'unit': 'a'}}, {'G': 1}, now)

    def test_observed_departure_is_not_payment(self):
        row = self.measure([{'ts': 250, 'unit': 'a'}])['skus']['G']
        self.assertEqual(row['paid_fraction'], 0)
        self.assertEqual(row['same_units_across_last_two_opens'], [])

    def test_paid_time_precedes_delayed_departure_and_deduplicates(self):
        sale = {'ts': 250, 'k': 'settle', 'seq': 1}
        row = self.measure([{'ts': 340, 'unit': 'a'}], [sale, sale])['skus']['G']
        self.assertEqual(row['paid_fraction'], 1)
        self.assertFalse(row['extra_retained'])

    def test_future_sale_and_window_do_not_leak(self):
        result = self.measure(ledger=[{'ts': 360, 'k': 'settle', 'seq': 1}])
        self.assertEqual(result['windows'], [100, 200, 300])
        self.assertTrue(result['skus']['G']['extra_retained'])
        self.assertEqual(result['skus']['G']['paid_fraction'], 0)

    def test_empty_cohort_and_initial_snapshot_are_unknown(self):
        row = self.measure(incoming=[{'ts': 120, 'supplied_at': 110, 'unit': 'a',
                                     'sku': 'G', 'first_tick': True}])['skus']['G']
        self.assertIsNone(row['paid_fraction'])
        self.assertIn('missing', self.measure(now=240))
