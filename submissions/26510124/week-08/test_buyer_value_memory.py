"""Price learning uses confirmed comparable goods without loosening checkout."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from buyer_policy import buying_terms, load_policy
from buyer_value_memory import build_value_evidence
from payment_memory import attach_reviews, reconcile

SKU = "G12345678"


class BuyerValueMemoryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000000
        self.config = load_policy()
        self.order = {"order": "current", "round": 3, "kind": "goods", "acceptable": [SKU]}
        self.catalog = {SKU: {"kind": "goods", "list_price": 100000}}
        self.policy = buying_terms(100000, product=self.catalog[SKU], order=self.order, config=self.config)
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def stamp(self, seconds):
        return datetime.fromtimestamp(self.now - seconds, timezone.utc).isoformat()

    def source(self, ledger=1, *, price=60000, grade="A", warranty=6, age=30, name="shop"):
        receipt = {"item": SKU, "task": f"task-{ledger}", "unit": f"unit-{ledger}", "price": price}
        record = {"name": name, "url": f"https://{name}.invalid", "item": SKU, "order": f"order-{ledger}",
                  "round": 3, "result": "settled", "ledger": ledger, "time": self.stamp(age),
                  "price": price, "purchase_policy": deepcopy(self.policy), "observed_receipt": receipt}
        review = {"order": record["order"], "receipt": deepcopy(receipt), "settled": {
            "ledger": ledger, "sku": SKU, "unit": receipt["unit"], "shop": record["url"],
            "grade": grade, "warranty_hours": warranty, "value": 100000, "claim_honest": True},
            "assessment": {"stars": 2, "review": "Accepted below preference", "issues": [],
                "reasons": ["above_preferred_price"], "preference_shortfalls": ["above_preferred_price"]}}
        return record, review

    def learned(self, ledger=1, **kwargs):
        record, review = self.source(ledger, **kwargs)
        return attach_reviews([record], [review])[0]

    def build(self, records, **kwargs):
        return build_value_evidence(records, catalog=self.catalog, order=self.order, budget=100000,
                                    config=self.config, now=self.now, **kwargs)

    def test_actual_correlated_quality_produces_bounded_compact_median_not_lowest_price(self):
        rows = [self.learned(1, price=60000), self.learned(2, price=62001), self.learned(3, price=1000)]
        before = deepcopy(rows)
        result = self.build(rows)[("shop", SKU)]
        self.assertEqual(result, {"best_price_to_list": .6, "groups": [{"kind": "goods", "grade": "A",
            "warranty_hours": 6, "sample_count": 3, "median_price": 60000,
            "price_to_list": .6, "age_seconds": 30}]})
        self.assertEqual(rows, before)
        self.assertEqual(self.policy["price_limit"], 85000)
        self.assertEqual(self.policy["accept"], {"cert": True, "grades": ["A", "B"], "warranty_hours": 3})
        even = self.build(rows[:2])[("shop", SKU)]["groups"][0]
        self.assertEqual(even["median_price"], 61001)  # Conservative integer rounding.

    def test_official_pending_receipts_reconcile_into_actual_same_quality_price_group(self):
        pending, payments, reviews = [], [], []
        for ledger, price in ((41, 60000), (42, 62000)):
            record, review = self.source(ledger, price=price)
            record.update(result="receipt_issued", detail="")
            record.pop("ledger")
            # The kit stores a market receipt without a kind field. Product
            # kind is independently supplied by catalog.json, not our accept.
            review["settled"].update(name="Fixture chair", claim="A", expires_at=None)
            self.assertNotIn("kind", review["settled"])
            self.assertEqual(review["settled"]["value"], self.catalog[SKU]["list_price"])
            self.assertIs(review["settled"]["claim_honest"], True)
            pending.append(record)
            payments.append({"order": record["order"], "shop": record["url"], "item": SKU,
                             "price": price, "paid": True, "ledger": ledger})
            reviews.append(review)
        original = deepcopy((pending, payments, reviews))
        self.assertEqual(self.build(attach_reviews(pending, reviews)), {})
        confirmed = reconcile(pending, payments)
        self.assertEqual([r["result"] for r in confirmed], ["settled", "settled"])
        self.assertEqual([r["ledger"] for r in confirmed], [41, 42])
        enriched = attach_reviews(confirmed, reviews)
        self.assertTrue(all(r["verified_review"] is True for r in enriched))
        self.assertTrue(all(r["settled_value_evidence"]["kind"] is None for r in enriched))
        groups = self.build(enriched)[("shop", SKU)]["groups"]
        self.assertEqual(groups, [{"kind": "goods", "grade": "A", "warranty_hours": 6,
            "sample_count": 2, "median_price": 61000, "price_to_list": .61, "age_seconds": 30}])
        self.assertEqual((pending, payments, reviews), original)

    def test_singletons_different_shops_grades_and_warranties_never_pool_as_same_quality(self):
        rows = [self.learned(1), self.learned(2, name="other"), self.learned(3, grade="B"),
                self.learned(4, warranty=3)]
        self.assertEqual(self.build(rows), {})
        rows.extend([self.learned(5, price=61000), self.learned(6, grade="B", price=50000),
                     self.learned(7, warranty=3, price=55000)])
        result = self.build(rows)[("shop", SKU)]["groups"]
        self.assertEqual(len(result), 2)
        self.assertTrue(all(group["sample_count"] == 2 for group in result))
        self.assertEqual({(g["grade"], g["warranty_hours"]) for g in result}, {("B", 6), ("A", 3)})

    def test_requested_accept_or_review_prose_cannot_supply_missing_actual_quality(self):
        for field in ("grade", "warranty_hours", "value", "claim_honest", "sku"):
            record, review = self.source(2)
            review["settled"].pop(field)
            review["assessment"]["review"] = "Grade A, 6-hour warranty, perfect usable value"
            derived = attach_reviews([record], [review])[0]
            with self.subTest(field=field):
                self.assertNotIn("settled_value_evidence", derived)
                self.assertEqual(self.build([self.learned(1), derived]), {})
        record, review = self.source(2)
        record["settled_value_evidence"] = self.learned(2)["settled_value_evidence"]
        self.assertNotIn("settled_value_evidence", attach_reviews([record], [])[0])

    def test_strict_proof_rejects_wrong_receipt_settlement_and_invalid_numeric_fields(self):
        changes = (("ledger", 99), ("ledger", True), ("shop", "https://wrong.invalid"),
            ("sku", "G99999999"), ("unit", "wrong-unit"), ("order", "wrong-order"),
            ("round", 4), ("round", True), ("ok", False), ("ok", 1), ("price", 1),
            ("amount", 1), ("claim_honest", False), ("claim_honest", 1), ("value", 0),
            ("value", True), ("value", float("nan")), ("grade", "unknown"),
            ("warranty_hours", -1), ("warranty_hours", True), ("warranty_hours", float("inf")),
            ("refund", 1000), ("refunded", True), ("refunded", False), ("refunds", [{}]))
        for key, value in changes:
            record, review = self.source(2)
            review["settled"][key] = value
            with self.subTest(key=key, value=value):
                derived = attach_reviews([record], [review])[0]
                self.assertNotIn("settled_value_evidence", derived)
                self.assertEqual(self.build([self.learned(1), derived]), {})
        for side, key, value in (("receipt", "task", "wrong-task"), ("receipt", "item", "wrong-sku"),
                                 ("receipt", "price", 1), ("observed", "item", "wrong-sku")):
            record, review = self.source(2)
            (record["observed_receipt"] if side == "observed" else review["receipt"])[key] = value
            with self.subTest(side=side, key=key):
                self.assertNotIn("settled_value_evidence", attach_reviews([record], [review])[0])

    def test_review_faults_or_missing_issue_assessment_do_not_inherit_stale_clean_marker(self):
        for issues in (["claim_mismatch"], None):
            record, review = self.source(2)
            record["review_issues"] = []
            review["assessment"]["issues"] = issues
            with self.subTest(issues=issues):
                self.assertNotIn("settled_value_evidence", attach_reviews([record], [review])[0])

    def test_current_round_recent_timezone_and_two_thousand_record_limits(self):
        for changes in ({"round": 2}, {"round": True}, {"time": self.stamp(21601)},
                        {"time": self.stamp(-1)}, {"time": "bad"}, {"time": "2026-01-01T00:00:00"},
                        {"verified_review": False}, {"result": "receipt_issued"}):
            row = {**self.learned(2), **changes}
            with self.subTest(changes=changes):
                self.assertEqual(self.build([self.learned(1), row]), {})
        self.assertTrue(self.build([self.learned(1, age=21600), self.learned(2, age=21600)]))
        self.assertEqual(self.build([self.learned(1), self.learned(2)] + [{} for _ in range(2000)]), {})

    def test_duplicate_ledger_or_order_never_increases_sample_count_and_conflicts_exclude(self):
        one, two = self.learned(1), self.learned(2)
        self.assertEqual(self.build([one, deepcopy(one)]), {})
        grouped = self.build([one, deepcopy(one), two])[("shop", SKU)]["groups"][0]
        self.assertEqual(grouped["sample_count"], 2)
        conflict = self.learned(1, price=61000)
        self.assertEqual(self.build([one, conflict, two]), {})
        collision = self.learned(3)
        collision["order"] = two["order"]
        collision["settled_value_evidence"]["order"] = two["order"]
        self.assertEqual(self.build([one, two, collision]), {})
        raw, review = self.source(2)
        conflicting_review = deepcopy(review)
        conflicting_review["settled"]["grade"] = "B"
        self.assertNotIn("settled_value_evidence", attach_reviews([raw], [review, conflicting_review])[0])
        self.assertIn("settled_value_evidence", attach_reviews([raw], [review, deepcopy(review)])[0])

    def test_invalid_configuration_never_triggers_default_policy_disk_read(self):
        with patch("buyer_policy.load_policy", side_effect=AssertionError("No disk read")):
            for config in (None, {}):
                with self.subTest(config=config):
                    self.assertEqual(build_value_evidence([self.learned(1), self.learned(2)],
                        catalog=self.catalog, order=self.order, budget=100000, config=config, now=self.now), {})

    def test_later_same_sku_refusal_or_fault_invalidates_old_prices_until_new_evidence(self):
        old = [self.learned(1, age=300), self.learned(2, age=200)]
        for result in ("payment_refused", "receipt_mismatch", "settled"):
            bad = {"name": "shop", "item": SKU, "round": 3, "result": result,
                   "time": self.stamp(100), "verified_review": True, "review_issues": ["short_warranty"]}
            with self.subTest(result=result):
                self.assertEqual(self.build(old + [bad]), {})
                later = old + [bad, self.learned(3, age=50), self.learned(4, age=30)]
                self.assertEqual(self.build(later)[("shop", SKU)]["groups"][0]["sample_count"], 2)
        pending = {**bad, "result": "error", "verified_review": False}
        self.assertTrue(self.build(old + [pending]))  # Transport errors do not prove bad terms.

    def test_catalog_and_current_policy_filter_without_inventing_food_freshness(self):
        records = [self.learned(1), self.learned(2)]
        self.catalog[SKU]["list_price"] = 110000  # No longer matches verified usable value.
        self.assertEqual(self.build(records), {})
        self.catalog[SKU]["list_price"] = 100000
        self.catalog[SKU]["kind"] = "food"
        self.assertEqual(self.build(records), {})
        self.catalog[SKU]["kind"] = "goods"
        self.order["kind"] = "food"
        self.assertEqual(self.build(records), {})
        self.order["kind"] = "goods"
        self.config["goods_warranty_hours"] = 7
        self.assertEqual(self.build(records), {})
        self.config["goods_warranty_hours"] = 3
        self.assertEqual(self.build([self.learned(1, grade="C"), self.learned(2, grade="C")]), {})
        self.assertEqual(self.build([self.learned(1, price=85001), self.learned(2, price=85001)]), {})
        self.assertTrue(self.build([self.learned(1, price=85000), self.learned(2, price=85000)]))


if __name__ == "__main__":
    unittest.main()
