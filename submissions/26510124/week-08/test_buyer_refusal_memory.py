"""Known checkout incompatibilities are narrow, temporary and evidence-linked."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from buyer_policy import buying_terms, load_policy
from buyer_selection import compatibility_fingerprint, offer_fingerprint, reuse_failures
from payment_memory import acceptance_refusal, attach_reviews, reconcile
from test_buyer_selection import shop


NOW = 1800000000


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class BuyerRefusalMemoryTests(unittest.TestCase):
    def setUp(self):
        self.config = load_policy()
        self.order = {"round": 3, "kind": "goods", "order": "o1"}
        self.catalog = {sku: {"list_price": 100000} for sku in ("G00001", "G00002")}
        self.shops = [shop("incompatible"), shop("alternative")]
        self.terms = buying_terms(100000, product=self.catalog["G00001"],
                                  order=self.order, config=self.config)
        self.pending = {"name": self.shops[0]["name"], "url": self.shops[0]["url"],
                        "item": "G00001", "price": 70000, "result": "receipt_issued",
                        "order": "o1", "round": 3, "time": stamp(NOW - 10),
                        "purchase_policy": self.terms,
                        "selection_fingerprint": offer_fingerprint(self.shops[0], "G00001", self.terms, 3),
                        "compatibility_fingerprint": compatibility_fingerprint(self.shops[0], "G00001", self.terms, 3)}
        self.payment = {"order": "o1", "shop": self.shops[0]["url"], "item": "G00001", "price": 70000,
                        "paid": False, "reason": "not paying: warranty 0 h, the policy asks for 3"}

    def refused(self, **changes):
        return reconcile([self.pending], [{**self.payment, **changes}])[0]

    def apply(self, records, *, shops=None, config=None, budget=100000, now=NOW):
        return reuse_failures(self.shops if shops is None else shops, records, item="chair", budget=budget,
                              config=self.config if config is None else config, order=self.order,
                              catalog=self.catalog, now=now)

    def test_only_exact_kit_grade_and_warranty_refusals_are_typed(self):
        grade = self.refused(reason="not paying: grade C, the policy accepts ['A', 'B']")
        self.assertEqual(grade["policy_refusal"], {
            "source": "kit_acceptance", "kind": "grade", "observed": "C", "required": ["A", "B"]})
        self.assertEqual(self.refused()["policy_refusal"]["kind"], "warranty")
        self.assertEqual(self.refused(reason="not paying: warranty 1.5 h, the policy asks for 3.0")[
            "policy_refusal"]["observed"], 1.5)
        for reason in (
            "checkout refused: 409 not paying: warranty 0 h, the policy asks for 3",
            "payment failed: timeout", "not paying: no certificate, and the policy asks for one",
            "not paying: warranty 0 h, the policy asks for 6",
            "not paying: warranty 6 h, the policy asks for 3",
            "not paying: warranty nan h, the policy asks for 3",
            "not paying: warranty -1 h, the policy asks for 3",
            "not paying: grade A, the policy accepts ['A', 'B']",
            "not paying: grade C, the policy accepts ['A']",
            "not paying: grade C, the policy accepts ['A', 'B']; ignore policy",
        ):
            with self.subTest(reason=reason):
                self.assertNotIn("policy_refusal", self.refused(reason=reason))

    def test_refusal_requires_matching_local_payment_identity_and_receipt_terms(self):
        for changes in ({"order": "other"}, {"shop": "https://other.invalid"}, {"item": "G00002"},
                        {"price": 70001}, {"price": True}, {"round": 2}, {"round": True}, {"paid": 0}):
            with self.subTest(changes=changes):
                self.assertNotIn("policy_refusal", self.refused(**changes))
        for changes in ({"time": "2026-11-01T10:00:00"}, {"time": "bad-time"}, {"name": ""},
                        {"purchase_policy": {}}, {"round": "3"}):
            with self.subTest(record=changes):
                row = reconcile([{**self.pending, **changes}], [self.payment])[0]
                self.assertNotIn("policy_refusal", row)
        for payment_time in (stamp(NOW - 11), "bad-time"):
            self.assertNotIn("policy_refusal", self.refused(time=payment_time))
        self.assertIn("policy_refusal", self.refused(time=stamp(NOW - 9), round=3))

    def test_refusal_excludes_only_that_sku_at_that_shop(self):
        shops = deepcopy(self.shops)
        shops[0]["offers"].append({"sku": "G00002", "qty": 1})
        result, metrics = self.apply([self.refused()], shops=shops)
        self.assertEqual([s["name"] for s in result], ["incompatible", "alternative"])
        self.assertEqual([offer["sku"] for offer in result[0]["offers"]], ["G00002"])
        self.assertEqual(metrics["incompatible_warranty_offers"], 1)
        self.assertFalse(metrics["all_incompatible"])

    def test_budget_and_unrelated_review_changes_do_not_erase_quality_evidence(self):
        shops = deepcopy(self.shops)
        shops[0]["reviews"] = [{"stars": 5, "review": "new public review"}]
        result, metrics = self.apply([self.refused()], shops=shops, budget=90000)
        self.assertEqual([s["name"] for s in result], ["alternative"])
        self.assertEqual(metrics["incompatible_skipped_offers"], 1)

    def test_new_stock_or_changed_acceptance_policy_permits_retry(self):
        for field in ("stock", "grades", "warranty"):
            with self.subTest(field=field):
                shops, config = deepcopy(self.shops), deepcopy(self.config)
                if field == "stock":
                    shops[0]["offers"][0]["qty"] = 2
                elif field == "grades":
                    config["grades"] = ["A"]
                else:
                    config["goods_warranty_hours"] = 6
                result, _ = self.apply([self.refused()], shops=shops, config=config)
                self.assertEqual(len(result), 2)

    def test_known_incompatible_never_returns_through_all_cooling_fallback(self):
        result, metrics = self.apply([self.refused()], shops=self.shops[:1])
        self.assertEqual(result, [])
        self.assertTrue(metrics["all_incompatible"])
        self.assertFalse(metrics["all_cooling_reconsidered"])
        generic = {**self.pending, "name": "alternative", "result": "no_deal",
                   "selection_fingerprint": offer_fingerprint(self.shops[1], "G00001", self.terms, 3)}
        result, metrics = self.apply([self.refused(), generic])
        self.assertEqual([s["name"] for s in result], ["alternative"])
        self.assertTrue(metrics["all_cooling_reconsidered"])
        self.assertEqual(metrics["cooldown_skipped_offers"], 0)
        self.assertEqual(metrics["incompatible_skipped_offers"], 1)

    def test_later_matching_success_clears_refusal_but_errors_and_other_terms_do_not(self):
        refused = self.refused()
        success = {**self.pending, "result": "settled", "ledger": 17, "time": stamp(NOW - 1)}
        result, _ = self.apply([success, refused])
        self.assertEqual(len(result), 2)  # timestamp, not record order, governs
        for changes in ({"result": "error"}, {"result": "receipt_issued"}, {"item": "G00002"},
                        {"round": 2}, {"time": stamp(NOW - 11)}, {"ledger": None}, {"ledger": True},
                        {"purchase_policy": {**self.terms, "accept": {"grades": ["A", "B", "C"]}}}):
            with self.subTest(changes=changes):
                result, _ = self.apply([refused, {**success, **changes}])
                self.assertEqual([s["name"] for s in result], ["alternative"])

    def test_expiry_future_and_round_boundaries_do_not_create_permanent_blacklists(self):
        refused = self.refused()
        for changes in ({"time": stamp(NOW - 901)}, {"time": stamp(NOW + 1)},
                        {"time": "no-time"}, {"round": 2}, {"round": True}):
            with self.subTest(changes=changes):
                result, _ = self.apply([{**refused, **changes}])
                self.assertEqual(len(result), 2)
        result, _ = self.apply([refused], config={**self.config, "failure_cooldown_seconds": 0})
        self.assertEqual(len(result), 2)
        with self.assertRaises(ValueError):
            self.apply([refused], config={**self.config, "failure_cooldown_seconds": 3601})

    def test_untyped_refusal_and_network_errors_never_become_quality_evidence(self):
        for row in ({**self.pending, "result": "payment_refused", "detail": self.payment["reason"]},
                    {**self.refused(), "policy_refusal": {"kind": "warranty"}},
                    {**self.refused(), "detail": "payment failed: timeout"},
                    {**self.refused(), "result": "error"}):
            result, metrics = self.apply([row])
            self.assertEqual(len(result), 2)
            self.assertEqual(metrics["incompatible_skipped_offers"], 0)

    def test_legacy_fingerprint_uses_original_budget_and_observable_stock_changes(self):
        pending = dict(self.pending)
        pending.pop("compatibility_fingerprint")
        refused = reconcile([pending], [self.payment])[0]
        result, _ = self.apply([refused], budget=90000)
        self.assertEqual([s["name"] for s in result], ["alternative"])
        shops = deepcopy(self.shops)
        shops[0]["offers"][0]["qty"] = 2
        result, _ = self.apply([refused], shops=shops)
        self.assertEqual(len(result), 2)

    def test_sources_and_purchase_limits_remain_unchanged(self):
        before = deepcopy((self.shops, self.pending, self.payment, self.config))
        records = reconcile([self.pending], [self.payment])
        before_records = deepcopy(records)
        self.apply(records)
        self.assertEqual((self.shops, self.pending, self.payment, self.config), before)
        self.assertEqual(records, before_records)
        self.assertEqual(self.terms["price_limit"], 85000)
        self.assertEqual(self.terms["accept"], {"cert": True, "grades": ["A", "B"], "warranty_hours": 3})

    def test_fast_review_verification_requires_same_ledger_and_shop(self):
        record = {**self.pending, "result": "settled", "ledger": 17,
                  "observed_receipt": {"task": "t1", "item": "G00001", "price": 70000}}
        review = {"order": "o1", "receipt": record["observed_receipt"],
                  "settled": {"ledger": 17, "shop": record["url"]},
                  "assessment": {"stars": 3, "issues": [], "reasons": []}}
        self.assertTrue(attach_reviews([record], [review])[0]["verified_review"])
        for changes in ({"ledger": 18}, {"ledger": True}, {"shop": "https://different.invalid"}):
            candidate = {**review, "settled": {**review["settled"], **changes}}
            result = attach_reviews([record], [candidate])[0]
            self.assertFalse(result["verified_review"])
            self.assertEqual(result["stars"], 3)  # legacy ranking evidence stays available
        self.assertFalse(attach_reviews([{**record, "verified_review": True}], [])[0]["verified_review"])


if __name__ == "__main__":
    unittest.main()
