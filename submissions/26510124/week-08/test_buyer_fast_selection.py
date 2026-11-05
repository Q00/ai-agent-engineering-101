"""Offline fixtures: reuse evidence and handle timeouts without relaxing policy."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import time
import unittest
from unittest.mock import Mock, patch

from buyer_fast_selection import exploration_turn, recent_success_choice, timeout_choice
from buyer_policy import buying_terms, load_policy
from buyer_selection import shortlist
from harness import HarnessError
from market_buyer import Buyer, BuyerError, PolicyRefusal, choose_shop
from test_buyer_selection import shop


class FastSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.config = load_policy()
        self.seed = next(f"order-{n}" for n in range(100) if not exploration_turn(f"order-{n}"))
        self.explore = next(f"order-{n}" for n in range(100) if exploration_turn(f"order-{n}"))
        self.order = {"order": self.seed, "round": 3, "kind": "goods"}
        self.catalog = {"G00001": {"list_price": 100000}, "G00002": {"list_price": 100000}}
        self.terms = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        self.shops = [shop("proven", [4, 5]), shop("new")]
        self.record = {"name": "proven", "item": "G00001", "round": 3, "result": "settled",
                       "time": datetime.fromtimestamp(self.now - 30, timezone.utc).isoformat(),
                       "ledger": 17, "price": 75000, "stars": 2, "review_issues": [], "verified_review": True,
                       "review_reasons": ["above_preferred_price"],
                       "review_preference_shortfalls": ["above_preferred_price"],
                       "purchase_policy": deepcopy(self.terms),
                       "observed_receipt": {"item": "G00001", "price": 75000, "task": "t1"}}

    def candidates(self, *, shops=None, memory=None, seed=None):
        return shortlist(self.shops if shops is None else shops,
                         [self.record] if memory is None else memory,
                         seed=seed or self.seed, round_number=3)

    def cached(self, *, records=None, shops=None, order=None, config=None, budget=100000):
        records = [self.record] if records is None else records
        order = self.order if order is None else order
        return recent_success_choice(self.candidates(shops=shops, memory=records), records,
                                     budget=budget, config=config or self.config, order=order,
                                     catalog=self.catalog, seed=order["order"], now=self.now)

    def test_recent_confirmed_tradeoff_reuses_selection_not_authorization(self):
        before = deepcopy(self.record)
        choice = self.cached()
        self.assertEqual((choice["name"], choice["sku"]), ("proven", "G00001"))
        self.assertNotIn("price", choice)
        self.assertNotIn("receipt", choice)
        self.assertEqual(self.record, before)
        # A historical success cannot authorize a higher price in a new order.
        buyer = Buyer(Mock(), url="https://proven.invalid", item="G00001", budget=100000, policy=self.terms)
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "accept", "price": 85001, "reason": "history"}, "asking 85001")
        self.assertEqual(buyer.policy["accept"], {"cert": True, "grades": ["A", "B"], "warranty_hours": 3})

    def test_pending_unreviewed_wrong_ledger_and_bad_contract_are_never_cache_hits(self):
        for changes in ({"result": "receipt_issued"}, {"ledger": None}, {"ledger": True}, {"ledger": 0},
                        {"verified_review": False}, {"verified_review": None},
                        {"review_issues": None}, {"review_issues": ["warranty_shortfall"]},
                        {"observed_receipt": {"item": "G99999", "price": 75000, "task": "t1"}},
                        {"purchase_policy": {}}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.cached(records=[{**self.record, **changes}]))

    def test_stale_future_unknown_timezone_and_other_round_never_cache(self):
        for changes in ({"round": 2}, {"item": "G00002"}, {"time": "bad"},
                        {"time": "2026-11-05T00:00:00"},
                        {"time": datetime.fromtimestamp(self.now - 901, timezone.utc).isoformat()},
                        {"time": datetime.fromtimestamp(self.now + 1, timezone.utc).isoformat()}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.cached(records=[{**self.record, **changes}]))

    def test_newer_refusal_or_unknown_result_prevents_older_success_reuse(self):
        for status in ("no_deal", "payment_refused", "error", "receipt_issued"):
            newer = {**self.record, "result": status,
                     "time": datetime.fromtimestamp(self.now - 1, timezone.utc).isoformat()}
            with self.subTest(status=status):
                self.assertIsNone(self.cached(records=[self.record, newer]))
        self.assertIsNone(self.cached(records=[self.record, {**self.record, "time": "unknown"}]))

    def test_changed_checkout_policy_or_price_cap_cannot_reuse_past_acceptance(self):
        self.assertIsNone(self.cached(config={**self.config, "goods_warranty_hours": 6}))
        self.assertIsNone(self.cached(config={**self.config, "grades": ["A"]}))
        self.assertIsNone(self.cached(budget=70000))
        # Personal rating preferences do not change actual buying minimums.
        self.assertIsNotNone(self.cached(config={**self.config, "review_preferences": None}))

    def test_current_low_rating_or_own_receipt_mismatch_disables_shortcut(self):
        shops = deepcopy(self.shops)
        shops[0]["reviews"].insert(0, {"stars": 1, "review": "fault reported"})
        self.assertIsNone(self.cached(shops=shops))
        mismatch = {**self.record, "item": "G00002", "result": "receipt_mismatch"}
        self.assertIsNone(self.cached(records=[self.record, mismatch]))

    def test_cache_keeps_new_shop_comparison_opportunities_and_can_be_disabled(self):
        self.assertIsNone(self.cached(order={**self.order, "order": self.explore}))
        self.assertIsNone(self.cached(config={**self.config, "selection_cache_seconds": 0}))
        for invalid in (-1, True, 3601):
            with self.assertRaises(ValueError):
                self.cached(config={**self.config, "selection_cache_seconds": invalid})

    def test_changed_tunnel_does_not_drop_same_shop_selection_evidence(self):
        shops = deepcopy(self.shops)
        shops[0]["url"] = "https://new-tunnel.invalid"
        self.assertEqual(self.cached(shops=shops)["name"], "proven")

    def test_timeout_fallback_uses_evidence_not_candidate_array_position(self):
        candidates = self.candidates()
        chosen = timeout_choice(candidates, seed=self.seed)
        self.assertEqual(chosen["name"], "proven")
        self.assertEqual(chosen, timeout_choice(list(reversed(candidates)), seed=self.seed))
        explored = timeout_choice(self.candidates(seed=self.explore), seed=self.explore)
        self.assertEqual(explored["name"], "new")
        self.assertIsNone(timeout_choice([], seed=self.seed))

    def test_integrated_cache_skips_model_and_preserves_live_shop_and_sku(self):
        with patch("buyer_fast_selection.time.time", return_value=self.now), \
                patch("market_buyer.decide") as model:
            selected, reason = choose_shop(self.shops, [self.record], item="chair", budget=100000,
                                            deadline=time.monotonic() + 30, policy=self.config,
                                            order=self.order, catalog=self.catalog)
        model.assert_not_called()
        self.assertEqual(selected["selected_sku"], "G00001")
        self.assertEqual(selected["url"], self.shops[0]["url"])
        self.assertIn("ledger 17", reason)

    def test_model_timeout_has_one_bounded_attempt_then_local_stock_selection(self):
        for message in ("Harness timed out; see fixture.log", "Harness deadline expired; see fixture.log"):
            with patch("market_buyer.decide", side_effect=HarnessError(message)) as model:
                selected, reason = choose_shop(self.shops, [], item="chair", budget=100000,
                                                deadline=time.monotonic() + 30, policy=self.config,
                                                order=self.order, catalog=self.catalog)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.kwargs["timeout"], 15)
            self.assertEqual(model.call_args.kwargs["max_attempts"], 1)
            self.assertIn(selected["selected_sku"], [offer["sku"] for offer in selected["offers"]])
            self.assertIn("timed out", reason)

    def test_invalid_model_outputs_and_auth_errors_do_not_trigger_fallback(self):
        for message in ("The harness response was not JSON", "Choose one exact name from the supplied candidates",
                        "haiku harness failed; see fixture.log", "Invalid model profile"):
            with patch("market_buyer.decide", side_effect=HarnessError(message)), self.assertRaises(HarnessError):
                choose_shop(self.shops, [], item="chair", budget=100000, deadline=time.monotonic() + 30,
                            policy=self.config, order=self.order, catalog=self.catalog)

    def test_model_selected_invented_sku_is_rejected(self):
        def decide(_, context, validate, **options):
            return validate(json.dumps({"name": "proven", "sku": "G99999", "reason": "invented"}))
        with patch("market_buyer.decide", side_effect=decide), self.assertRaises(HarnessError):
            choose_shop(self.shops, [], item="chair", budget=100000, deadline=time.monotonic() + 30,
                        policy=self.config, order=self.order, catalog=self.catalog)

    def test_all_incompatible_offers_and_expired_deadline_never_call_model(self):
        with patch("market_buyer.reuse_failures", return_value=([], {"all_incompatible": True})), \
                patch("market_buyer.decide") as model, self.assertRaises(BuyerError):
            choose_shop(self.shops, [], item="chair", budget=100000, deadline=time.monotonic() + 30,
                        policy=self.config, order=self.order, catalog=self.catalog)
        model.assert_not_called()
        with patch("market_buyer.decide") as model, self.assertRaises(BuyerError):
            choose_shop(self.shops, [], item="chair", budget=100000, deadline=time.monotonic() - 1,
                        policy=self.config, order=self.order, catalog=self.catalog)
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
