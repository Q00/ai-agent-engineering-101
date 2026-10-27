import json
from pathlib import Path
import tempfile
import unittest

from negotiation.auditing import take_audit_request
from negotiation.market import choose_shop


class IntegrityAuditTests(unittest.TestCase):
    def test_only_one_eligible_real_order_consumes_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "request.json"
            path.write_text(json.dumps({"shop": "suspected", "expires_epoch": 200,
                                        "minimum_budgets": {"lamp": 125}}))
            self.assertIsNone(take_audit_request(path, {"round": 2, "item": "lamp", "budget": 90}, now=100))
            self.assertTrue(path.exists())
            self.assertIsNone(take_audit_request(path, {"round": 1, "item": "lamp", "budget": 140}, now=100))
            self.assertIsNone(take_audit_request(path, {"round": 2, "item": "lamp", "budget": 140}, now=201))
            order = {"round": 2, "item": "lamp", "budget": 140}
            self.assertEqual(take_audit_request(path, order, now=100), "suspected")
            self.assertIsNone(take_audit_request(path, order, now=100))
            self.assertEqual(len(list(Path(folder).glob("audit-consumed-*.json"))), 1)

    def test_explicit_audit_does_not_change_normal_selection_or_fabricate_candidates(self):
        shops = [{"name": name, "url": "https://" + name + ".example", "reviews":
                  [{"stars": stars, "review": "one"}, {"stars": stars, "review": "two"}]}
                 for name, stars in [("suspected", 1), ("trusted", 5)]]
        selected, reason, _ = choose_shop(shops, [], "lamp", 140, audit_name="suspected")
        self.assertEqual(selected["name"], "suspected")
        self.assertIn("integrity audit", reason)
        self.assertEqual(choose_shop(shops, [], "lamp", 140)[0]["name"], "trusted")
        self.assertEqual(choose_shop(shops, [], "lamp", 140, audit_name="absent")[0]["name"], "trusted")


if __name__ == "__main__":
    unittest.main()
