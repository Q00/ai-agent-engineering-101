"""Reject policy mistakes before a focused purchase or customer promise."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from feedback import atomic_json
from shop_policy import policy_at

ROOT = Path(__file__).resolve().parent


class FocusedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = policy_at()
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def read(self, config):
        atomic_json(self.root / "shop-policy.json", config)
        return policy_at(self.root)

    def test_duplicate_or_invalid_skus_cannot_divert_the_purchase(self):
        for value in (["G8095ddd6", "G8095ddd6"], ["any product"], [], [True]):
            config = deepcopy(self.config)
            config["restock"]["focused_goods"]["primary_skus"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(config)
        config = deepcopy(self.config)
        config["restock"]["focused_goods"]["secondary_skus"] = ["G8095ddd6"]
        with self.assertRaises(ValueError):
            self.read(config)

    def test_invalid_growth_or_surge_limits_fail_before_planning(self):
        for key, value in (("max_surge_factor", True), ("max_surge_factor", 1.51),
                           ("max_secondary_sku_cost_percent", 61),
                           ("minimum_public_sales", 0), ("minimum_public_buyers", 1),
                           ("prepare_before_seconds", 3600)):
            config = deepcopy(self.config)
            config["restock"]["focused_goods"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.read(config)

    def test_pilot_price_cannot_invent_grade_or_unbounded_warranty(self):
        for key, value in (("target", -1), ("target", True), ("grade", "premium"),
                           ("warranty_hours", 999)):
            config = deepcopy(self.config)
            config["opening"]["pilot_targets"]["G8095ddd6"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.read(config)


if __name__ == "__main__":
    unittest.main()
