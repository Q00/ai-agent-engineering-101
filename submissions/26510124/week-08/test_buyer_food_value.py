"""Food price references require actual expiry and a correlated market clock."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import time
import unittest
from unittest.mock import patch

from buyer_policy import buying_terms, load_policy, negotiation_policy
from buyer_context import compact_dialogue
from buyer_value_memory import build_value_evidence
from market_buyer import ROOT, choose_shop, last_asking_price
from payment_memory import attach_reviews, reconcile

SKU = "F12345678"


class BuyerFoodValueTests(unittest.TestCase):
    def setUp(self):
        self.market_now = 2000000
        self.local_now = self.market_now + 780  # The production host's observed skew.
        self.config = load_policy()
        self.config["food_grades"] = ["A", "B", "C"]
        self.config["food_buffer_hours"] = 1
        self.order = {"order": "current", "round": 3, "kind": "food",
                      "acceptable": [SKU], "use_within_hours": 6}
        self.catalog = {SKU: {"kind": "food", "list_price": 10000}}
        self.policy = buying_terms(10000, product=self.catalog[SKU], order=self.order, config=self.config)
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def source(self, ledger, *, grade="B", price=5000, life=8, age=30, name="shop", direct=False):
        receipt = {"item": SKU, "task": f"task-{ledger}", "unit": f"unit-{ledger}", "price": price}
        record = {"name": name, "url": f"https://{name}.invalid", "item": SKU,
                  "order": f"order-{ledger}", "round": 3, "result": "receipt_issued",
                  "time": datetime.fromtimestamp(self.local_now - age, timezone.utc).isoformat(),
                  "price": price, "purchase_policy": deepcopy(self.policy), "observed_receipt": receipt}
        payment = {"order": record["order"], "shop": record["url"], "item": SKU,
                   "price": price, "paid": True, "ledger": ledger}
        stamp = self.market_now - age
        review = {"order": record["order"], "receipt": deepcopy(receipt), "settled": {
            "ledger": ledger, "sku": SKU, "unit": receipt["unit"], "shop": record["url"],
            "grade": grade, "warranty_hours": None, "expires_at": stamp + life * 3600,
            "claim": None, "claim_honest": None, "value": 10000}, "assessment": {
                "stars": 3, "review": "Usable food", "issues": [], "preference_shortfalls": [],
                "reviewed_at": self.local_now - age,
                "market_clock": {"status": "synced", "now": stamp, "uncertainty_seconds": 2}}}
        if direct:
            review["settled"]["ts"] = stamp
            review["assessment"].pop("market_clock")
        return record, payment, review

    def attach(self, record, payment, review):
        return attach_reviews(reconcile([record], [payment]), [review])[0]

    def learned(self, ledger, **kwargs):
        return self.attach(*self.source(ledger, **kwargs))

    def build(self, records):
        return build_value_evidence(records, catalog=self.catalog, order=self.order,
                                    budget=10000, config=self.config, now=self.local_now)

    def test_official_receipt_without_kind_or_timestamp_learns_using_captured_market_clock(self):
        sources = [self.source(1, price=5000), self.source(2, price=6000)]
        before = deepcopy(sources)
        rows = [self.attach(*source) for source in sources]
        group = self.build(rows)[("shop", SKU)]["groups"][0]
        self.assertEqual(group, {"kind": "food", "grade": "B", "warranty_hours": None,
            "sample_count": 2, "median_price": 5500, "price_to_list": .55, "age_seconds": 30,
            "freshness_bucket_hours": [6, 12], "minimum_hours_left": 8.0})
        self.assertTrue(all(row["settled_value_evidence"]["claim_absent"] for row in rows))
        self.assertTrue(all(row["settled_value_evidence"]["kind"] is None for row in rows))
        self.assertEqual(sources, before)

    def test_local_clock_ahead_or_behind_does_not_change_verified_historical_freshness(self):
        for skew in (780, -780):
            self.local_now = self.market_now + skew
            with self.subTest(skew=skew):
                rows = [self.learned(1, life=7), self.learned(2, life=7)]
                self.assertEqual(self.build(rows)[("shop", SKU)]["groups"][0]["minimum_hours_left"], 7)
                self.assertEqual(self.build([self.learned(1, life=6.99), self.learned(2, life=6.99)]), {})

    def test_direct_market_timestamp_supported_and_preferred_over_later_review(self):
        self.assertTrue(self.build([self.learned(1, direct=True), self.learned(2, direct=True)]))
        rows = []
        for ledger in (1, 2):
            record, payment, review = self.source(ledger, direct=True, life=7)
            stamp = review["settled"].pop("ts")
            review["settled"]["settled_at"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            review["assessment"]["market_clock"] = {"status": "synced", "now": stamp + 600,
                                                     "uncertainty_seconds": 2}
            rows.append(self.attach(record, payment, review))
        self.assertEqual(self.build(rows)[("shop", SKU)]["groups"][0]["minimum_hours_left"], 7)
        self.assertEqual(rows[0]["settled_value_evidence"]["freshness_reference_source"], "market_settlement")

    def test_local_reviewed_at_grade_and_shelf_life_cannot_replace_missing_market_time_or_expiry(self):
        changes = [("clock", None), ("clock", {"status": "missing", "now": self.market_now,
                    "uncertainty_seconds": 0}), ("clock", {"status": "stale", "now": self.market_now,
                    "uncertainty_seconds": 2}), ("clock", {"status": "synced", "now": self.market_now,
                    "uncertainty_seconds": 7}), ("clock", {"status": "synced", "now": True,
                    "uncertainty_seconds": 2}), ("clock", {"status": "synced", "now":
                    "2026-11-08T12:00:00+00:00", "uncertainty_seconds": 2}),
                   ("expires_at", None), ("expires_at", True), ("expires_at", 1e308),
                   ("expires_at", float("inf")), ("expires_at", "2026-11-08T12:00:00"),
                   ("expires_at", self.market_now - 100)]
        for field, value in changes:
            record, payment, review = self.source(2)
            review["settled"]["shelf_hours"] = 1000
            review["assessment"]["review"] = "Grade A, still fresh for 24 hours"
            if field == "clock":
                review["assessment"]["market_clock"] = value
            else:
                review["settled"][field] = value
            with self.subTest(field=field, value=value):
                row = self.attach(record, payment, review)
                self.assertNotIn("settled_value_evidence", row)
                self.assertEqual(self.build([self.learned(1), row]), {})

    def test_invalid_or_conflicting_direct_market_timestamps_do_not_fallback_to_clock(self):
        for updates in ({"ts": True}, {"ts": float("nan")}, {"ts": self.market_now + 1},
                        {"ts": self.market_now - 30, "settled_at": self.market_now - 90}):
            record, payment, review = self.source(2)
            review["settled"].update(updates)
            with self.subTest(updates=updates):
                self.assertNotIn("settled_value_evidence", self.attach(record, payment, review))

    def test_original_kind_catalog_and_current_acceptance_all_required(self):
        rows = [self.learned(1), self.learned(2)]
        for original_kind in ("goods", None):
            modified = deepcopy(rows)
            for row in modified:
                row["purchase_policy"]["kind"] = original_kind
            with self.subTest(original_kind=original_kind):
                self.assertEqual(self.build(modified), {})
        self.catalog[SKU]["kind"] = "goods"
        self.assertEqual(self.build(rows), {})
        self.catalog[SKU]["kind"] = "food"
        self.order["use_within_hours"] = 8
        self.assertEqual(self.build(rows), {})
        self.order["use_within_hours"] = 6
        self.config["food_grades"] = ["A"]
        self.assertEqual(self.build(rows), {})
        self.config["food_grades"] = ["A", "B", "C"]
        self.assertTrue(self.build([self.learned(1, grade="C"), self.learned(2, grade="C")]))

    def test_actual_grade_and_freshness_buckets_do_not_pool_and_only_two_groups_return(self):
        singles = [self.learned(1, grade="A", life=8), self.learned(2, grade="B", life=8),
                   self.learned(3, grade="B", life=12)]
        self.assertEqual(self.build(singles), {})
        rows = singles + [self.learned(4, grade="A", life=11), self.learned(5, grade="B", life=10),
                          self.learned(6, grade="B", life=23)]
        groups = self.build(rows)[("shop", SKU)]["groups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(group["sample_count"] == 2 for group in groups))
        self.assertTrue(all(group["freshness_bucket_hours"] in ([6, 12], [12, 24]) for group in groups))
        self.assertFalse(self.build([self.learned(1), self.learned(2, name="other")]))

    def test_neutral_absent_claim_only_and_dishonesty_never_qualifies(self):
        for source, field, value in (("settled", "claim_honest", False),
                ("settled", "claim_honest", 1), ("settled", "claim", "A"),
                ("receipt", "claim", "B"), ("observed", "claim", "A")):
            record, payment, review = self.source(2)
            target = record["observed_receipt"] if source == "observed" else review[source]
            target[field] = value
            with self.subTest(source=source, field=field, value=value):
                self.assertNotIn("settled_value_evidence", self.attach(record, payment, review))
        rows = []
        for ledger in (1, 2):
            record, payment, review = self.source(ledger)
            review["settled"].update(claim="B", claim_honest=True)
            rows.append(self.attach(record, payment, review))
        self.assertTrue(self.build(rows))

    def test_missing_quality_faults_refunds_and_receipt_mismatch_are_excluded(self):
        for field, value in (("grade", None), ("value", 0), ("value", True), ("ledger", True),
                ("ledger", 999), ("sku", "wrong"), ("shop", "https://wrong.invalid"),
                ("unit", "wrong"), ("kind", "goods"), ("refund", 1), ("refunded", False)):
            record, payment, review = self.source(2)
            review["settled"][field] = value
            with self.subTest(field=field, value=value):
                self.assertNotIn("settled_value_evidence", self.attach(record, payment, review))
        record, payment, review = self.source(2)
        review["assessment"]["issues"] = ["food_too_short"]
        self.assertNotIn("settled_value_evidence", self.attach(record, payment, review))

    def test_stale_future_round_duplicate_and_later_fault_limits_remain_active(self):
        first = self.learned(1)
        for kwargs in ({"age": 21601}, {"age": -1}):
            self.assertEqual(self.build([first, self.learned(2, **kwargs)]), {})
        self.assertEqual(self.build([first, {**self.learned(2), "round": 2}]), {})
        self.assertEqual(self.build([first, deepcopy(first)]), {})
        self.assertEqual(self.build([first, self.learned(1, price=6000), self.learned(2)]), {})
        self.assertEqual(self.build([first, self.learned(2)] + [{}] * 2000), {})
        bad = {"name": "shop", "item": SKU, "round": 3, "result": "payment_refused",
               "time": datetime.fromtimestamp(self.local_now - 10, timezone.utc).isoformat()}
        self.assertEqual(self.build([first, self.learned(2), bad]), {})
        later = [first, self.learned(2), bad, self.learned(3, age=5), self.learned(4, age=3)]
        self.assertEqual(self.build(later)[("shop", SKU)]["groups"][0]["sample_count"], 2)

    def test_duplicate_review_clock_conflict_cannot_choose_convenient_freshness(self):
        record, payment, review = self.source(1)
        changed = deepcopy(review)
        changed["assessment"]["market_clock"]["now"] -= 3600
        row = attach_reviews(reconcile([record], [payment]), [review, changed])[0]
        self.assertNotIn("settled_value_evidence", row)

    def test_food_history_and_current_lifetime_reach_one_selection_call(self):
        rows = [self.learned(1, grade="C"), self.learned(2, grade="C")]
        source = {"name": "shop", "url": "https://shop.invalid", "reviews": [],
                  "offers": [{"sku": SKU, "qty": 1, "name": "food"}]}
        def select(_, context, validate, **options):
            self.assertEqual(context["order_conditions"], {"kind": "food", "accept": {
                "cert": True, "grades": ["A", "B", "C"], "min_hours_left": 7}})
            groups = context["candidates"][0]["offers"][0]["price_history"]
            self.assertEqual(groups[0]["freshness_bucket_hours"], [6, 12])
            self.assertEqual(groups[0]["grade"], "C")
            return validate(json.dumps({"name": "shop", "sku": SKU, "reason": "confirmed food prices"}))
        with patch("market_buyer.decide", side_effect=select) as model, \
                patch("market_buyer.time.time", return_value=self.local_now), redirect_stdout(io.StringIO()):
            chosen, _ = choose_shop([source], rows, item="food", budget=10000,
                deadline=time.monotonic() + 10, policy={**self.config, "selection_cache_seconds": 0},
                order=self.order, catalog=self.catalog)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(chosen["price_reference"], self.build(rows)[("shop", SKU)]["groups"])

    def test_two_food_groups_keep_four_turn_prompt_small(self):
        rows = [self.learned(1), self.learned(2), self.learned(3, grade="C", life=16),
                self.learned(4, grade="C", life=16)]
        terms = {**self.policy, "price_reference": self.build(rows)[("shop", SKU)]["groups"]}
        self.assertEqual(len(terms["price_reference"]), 2)
        history = []
        for ask, bid in ((8000, 5000), (7500, 5200), (7000, 5400), (6500, 6500)):
            message = f"Grade C. 16 hours left. Asking price: {ask}"
            history.append({"role": "seller", "text": message})
            context = {"item": SKU, "budget": 10000, "purchase_policy": negotiation_policy(terms),
                       "current_ask": ask, **compact_dialogue(history, message, last_asking_price)}
            prompt = (ROOT / "prompts/buyer.txt").read_text() + "\n" + json.dumps(context)
            self.assertLess(len(prompt), 2800)  # App characters, not CLI/token accounting.
            history.append({"role": "buyer", "text": str(bid)})


if __name__ == "__main__":
    unittest.main()
