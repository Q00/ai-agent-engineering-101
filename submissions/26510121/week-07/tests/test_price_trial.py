import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("price_trial", Path(__file__).parents[1] / "experiments/price_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class PriceTrialMetricsTests(unittest.TestCase):
    def snapshot(self, epoch, deals, revenue, other):
        return {"epoch":epoch,"board":{"shops":[{"name":"seochanit","deals":deals,"revenue":revenue,"alive":True},
                                                  {"name":"other","deals":other}]}}

    def test_counts_are_differenced_and_zero_sales_is_not_zero_price(self):
        start = self.snapshot(0, 1, 260, 99)
        empty = trial.metrics(start, self.snapshot(180, 1, 260, 119))
        self.assertEqual(empty["window_share_percent"], 0)
        self.assertIsNone(empty["window_mean_price"])
        self.assertEqual(empty["end"]["cumulative_mean_price"], 260)
        sold = trial.metrics(start, self.snapshot(180, 3, 560, 117))
        self.assertEqual(sold["window_mean_price"], 150)
        self.assertEqual(sold["window_share_percent"], 10)

    def test_counter_reset_is_not_reported_as_negative_sales(self):
        with self.assertRaises(ValueError):
            trial.metrics(self.snapshot(0, 5, 1000, 100), self.snapshot(180, 1, 260, 90))


if __name__ == "__main__":
    unittest.main()
