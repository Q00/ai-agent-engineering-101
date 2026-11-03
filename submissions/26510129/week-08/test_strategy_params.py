import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import strategy_params


class StrategyParamsTest(unittest.TestCase):
    def test_validate_keeps_only_known_numbers_inside_the_range(self):
        accepted, rejected = strategy_params.validate({
            "explore_rate": 0.2, "grade_cap_B": 0.5, "budget": 999, "seller_margin_volume": "1.2", "first_offer_discount": True,
        })
        self.assertEqual({"explore_rate": 0.2}, accepted)
        self.assertEqual({"grade_cap_B", "budget", "seller_margin_volume", "first_offer_discount"}, set(rejected))

    def test_load_is_defaults_without_a_file_and_merges_a_valid_file(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}):
            self.assertEqual(strategy_params.DEFAULTS, strategy_params.load())
            Path(d, "strategy_params.json").write_text(json.dumps({"params": {"grade_cap_B": 0.8, "explore_rate": 9}}))
            loaded = strategy_params.load()
        self.assertEqual(0.8, loaded["grade_cap_B"])
        self.assertEqual(strategy_params.DEFAULTS["explore_rate"], loaded["explore_rate"])

    def test_mandate_knobs_are_not_tunable(self):
        self.assertFalse(any("budget" in name or "ceiling" in name for name in strategy_params.PARAMS))


if __name__ == "__main__":
    unittest.main()
