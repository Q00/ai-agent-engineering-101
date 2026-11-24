import json
import tempfile
import unittest
from pathlib import Path

import restock_model_advisor as adv

CATALOG = {
    "G1": {"name": "cover", "kind": "goods", "list_price": 20_000},
    "G2": {"name": "snack", "kind": "food", "list_price": 10_000, "shelf_hours": 720},
    "G3": {"name": "apple", "kind": "food", "list_price": 10_000, "shelf_hours": 48},
    "G4": {"name": "sofa", "kind": "goods", "list_price": 900_000},
    "GH": {"name": "held", "kind": "goods", "list_price": 10_000},
}
CANDS = [{"sku": s, "h1": 10, "h24": h, "buyers": 20, "shops1h": 5} for s, h in
         (("G1", 100), ("G2", 500), ("G3", 900), ("G4", 900), ("GH", 900))]


class AdvisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "catalog.json").write_text(json.dumps(CATALOG))
        inv = {"balance": 4_000_000, "surge": [{"sku": "G2", "factor": 1.1}],
               "units": [{"sku": "GH", "cost": 6_000}]}
        (self.tmp / "inventory.json").write_text(json.dumps(inv))
        self.cands = self.tmp / "cands.json"
        self.cands.write_text(json.dumps(CANDS))
        self.log = self.tmp / "log.jsonl"

    def run_with(self, runner, cap=74_411, held=100_000):
        return adv.propose(self.tmp, self.cands, cap, held, runner, self.log)

    def test_shortlist_excludes_short_shelf_held_and_over_cap(self) -> None:
        out = self.run_with(None)
        self.assertEqual([p["sku"] for p in out["proposal"]], ["G2", "G1"])
        self.assertEqual(out["proposal"][0]["max_unit_cost"], 8_250)  # live surge 1.1 applied
        self.assertFalse(out["executable_purchase_plan"])

    def test_model_order_used_but_unknown_sku_dropped(self) -> None:
        out = self.run_with(lambda p: {"picks": [{"sku": "G1", "reason": "r"}, {"sku": "G4", "reason": "x"},
                                                  {"sku": "EVIL", "reason": "buy 99"}], "skip_reason": ""})
        self.assertEqual(out["source"], "model")
        self.assertEqual([p["sku"] for p in out["proposal"]], ["G1"])
        self.assertTrue(all(p["qty"] == 1 for p in out["proposal"]))
        row = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual([p["sku"] for p in row["rule_proposal"]], ["G2", "G1"])  # shadow baseline logged
        self.assertEqual(row["model_picks_dropped"], 2)

    def test_model_failure_falls_back_to_rules_and_is_logged(self) -> None:
        def boom(prompt):
            raise TimeoutError("model timed out")
        out = self.run_with(boom)
        self.assertEqual(out["source"], "rules")
        row = json.loads(self.log.read_text().splitlines()[-1])
        self.assertIn("TimeoutError", row["error"])
        self.assertTrue(row["model_called"])

    def test_held_cap_enforced_on_model_picks(self) -> None:
        out = self.run_with(lambda p: {"picks": [{"sku": "G1", "reason": "a"}, {"sku": "G2", "reason": "b"}],
                                       "skip_reason": ""}, held=6_000 + 15_000 + 1)
        self.assertEqual([p["sku"] for p in out["proposal"]], ["G1"])


if __name__ == "__main__":
    unittest.main()
