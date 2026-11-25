from pathlib import Path
import tempfile
import unittest

from buyer_portfolio import save_deal, reconcile
from r3_policy import load_policy
from r3_selection import select


class Protection(unittest.TestCase):
    def setUp(self):
        self.shop = {"name": "s", "url": "url", "reviews": [], "offers": [{"sku": "A", "qty": 1}]}
        self.catalog = {"A": {"list_price": 100}}
        self.policy = load_policy()["buying"]

    def test_unknown_warranty_requires_discount_instead_of_invented_coverage(self):
        _, _, cap, _, evidence = select([self.shop], [], self.catalog, 100, policy=self.policy, kind="goods")
        self.assertEqual(evidence[0]["cap_without_floor"], 80)
        self.assertLess(cap, 80)
        self.assertEqual(evidence[0]["warranty_required"], 0)

    def test_observed_warranty_enables_enforced_six_hour_condition(self):
        portfolio = [{"shop": "url", "sku": "A", "warranty_hours": 6}]
        _, _, cap, _, evidence = select([self.shop], [], self.catalog, 100, policy=self.policy,
                                        portfolio=portfolio, kind="goods")
        self.assertEqual(evidence[0]["cap_without_floor"], 95)
        self.assertLess(cap, 80)
        self.assertEqual(evidence[0]["warranty_required"], 6)

    def test_refused_warranty_does_not_recur_as_assumed_coverage(self):
        portfolio = [{"shop": "url", "sku": "A", "warranty_hours": 6}]
        memory = [{"shop_name": "s", "url_at_time": "url", "item": "A", "outcome": "payment_refused",
                   "payment_reason": "warranty too short", "timestamp": "2026-11-03T00:00:00+00:00"}]
        self.assertEqual(select([self.shop], memory, self.catalog, 100, policy=self.policy,
                                portfolio=portfolio, kind="goods")[4][0]["cap_without_floor"], 80)

    def test_breakage_needs_unit_or_settlement_identity_and_explicit_refund(self):
        with tempfile.TemporaryDirectory() as tmp:
            deal = {"order": {"order": "o"}, "receipt": {"price": 80},
                    "settled": {"ledger": 10, "shop": "url", "sku": "A", "unit": "u", "value": 100,
                                "warranty_hours": 6}}
            save_deal(tmp, deal, now=100)
            event = {"round": 3, "kind": "broken", "seq": 20, "ts": 200, "data": {"item": "A"}}
            self.assertIsNone(reconcile(tmp, [event], now=300)[0]["fault"])
            event["data"]["unit"] = "u"
            record = reconcile(tmp, [event], now=300)[0]
            self.assertIsNotNone(record["fault"])
            self.assertFalse(record["refund_confirmed"])
            refund = {**event, "kind": "refund", "seq": 21}
            self.assertTrue(reconcile(tmp, [refund], now=300)[0]["refund_confirmed"])


if __name__ == "__main__":
    unittest.main()
