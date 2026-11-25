import json
from pathlib import Path
import tempfile
import unittest

from procurement_cost import ceiling, estimate_unit_cost
from seller_meta_policy import DEFAULT, POLICY_VERSION, active


class GrowthSpendTests(unittest.TestCase):
    def test_v2_default_gains_headroom_without_overwriting_a_custom_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            for version, value, expected in [("capital-share-v2", 24_000_000, 32_000_000),
                                             ("capital-share-v2", 26_000_000, 26_000_000),
                                             (POLICY_VERSION, 24_000_000, 24_000_000)]:
                with self.subTest(version=version, value=value):
                    path.write_text(json.dumps({"schema_version": 2, "policy_contract_version": version,
                        "version": "saved", "at": 100, "expires_at": 150,
                        "config": {**DEFAULT, "rolling_exploration_budget": value}}))
                    self.assertEqual(active(120, path)["rolling_exploration_budget"], expected)

    def test_expected_spend_never_replaces_the_supply_ceiling(self):
        goods = {"kind": "goods", "list_price": 1000}
        self.assertEqual(estimate_unit_cost(goods, 1.2), 708)
        self.assertEqual(estimate_unit_cost({**goods, "kind": "food"}), 500)
        self.assertEqual(ceiling(goods), 1125)
