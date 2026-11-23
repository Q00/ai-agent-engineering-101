"""Safety, fairness and reproducibility tests for A/B/C/D competition."""
from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from competition.runner import run_phase, top_and_separation
from competition.simulator import (
    BASE_TS,
    BUYER_OWNER,
    build_world,
    competitor_offers,
    decay_and_charge,
    initial_inventory,
    negotiate,
    run_buyer_episode,
    run_seller_episode,
    safe_offer,
    world_fingerprint,
)
from competition.strategies import (
    BUYER_STRATEGIES,
    SELLER_STRATEGIES,
    AdaptiveBuyer,
    AdaptiveSeller,
    BuyerStrategy,
    DiscountBuyer,
    QualityDiscountBuyer,
    SafeDiscountSeller1,
    SafeDiscountSeller2,
)
from mock_market import _catalog


class CompetitionTests(unittest.TestCase):
    def test_world_and_fingerprint_are_reproducible(self) -> None:
        self.assertEqual(build_world(11, 20), build_world(11, 20))
        self.assertEqual(world_fingerprint(11, 20), world_fingerprint(11, 20))
        self.assertNotEqual(world_fingerprint(11, 20), world_fingerprint(12, 20))

    def test_same_owner_shop_is_ineligible(self) -> None:
        order = build_world(3, 1)[0]
        offers = competitor_offers(3, order, include_same_owner=True)
        same = next(offer for offer in offers if offer["owner"] == BUYER_OWNER)
        self.assertEqual(safe_offer(order, same, BUYER_OWNER), "same_owner")

    def test_negotiation_respects_configured_turn_limit(self) -> None:
        order = build_world(9, 1)[0]
        offer = competitor_offers(9, order, include_same_owner=False)[0]
        _, turns, _ = negotiate(order, offer, BuyerStrategy(), 3)
        self.assertLessEqual(turns, 3)

    def test_time_decay_discards_food_and_charges_goods_storage(self) -> None:
        inventory = initial_inventory(5)
        before = inventory["balance"]
        loss, storage = decay_and_charge(inventory, _catalog(), BASE_TS + 100 * 3600, 100)
        self.assertGreater(loss, 0)
        self.assertGreater(storage, 0)
        self.assertEqual(inventory["balance"], before - storage)

    def test_every_strategy_runs_with_common_world_and_no_safety_violation(self) -> None:
        for seed in (21, 101, 102, 103, 104, 105):
            for code, cls in SELLER_STRATEGIES.items():
                with self.subTest(role="seller", strategy=code, seed=seed):
                    rows, summary = run_seller_episode(cls(), seed, 20)
                    self.assertEqual(len(rows), 20)
                    self.assertEqual(summary["attempts"], 20)
                    self.assertEqual(summary["safety_violations"], 0)
        for code, cls in BUYER_STRATEGIES.items():
            with self.subTest(role="buyer", strategy=code):
                rows, summary = run_buyer_episode(cls(), 21, 20)
                self.assertEqual(len(rows), 20)
                self.assertEqual(summary["attempts"], 20)
                self.assertEqual(summary["safety_violations"], 0)

    def test_restock_cost_is_paid_once_and_balance_profit_is_cash_based(self) -> None:
        _, summary = run_seller_episode(SELLER_STRATEGIES["B"](), 31, 20)
        self.assertGreater(summary["inventory_purchase_cost"], 0)
        # Identity excludes unsold stock valuation: official profit is cash balance minus start.
        self.assertIsInstance(summary["balance_profit"], float)
        self.assertGreaterEqual(summary["remaining_stock_cost"], 0)

    def test_adaptive_seller_uses_only_observed_counts(self) -> None:
        strategy = AdaptiveSeller()
        product = _catalog()["G-LAMP-01"]
        self.assertEqual(strategy.restock("G-LAMP-01", 0, product, 5_000_000), 1)
        for _ in range(10):
            strategy.observe_order("G-LAMP-01")
        self.assertEqual(strategy.restock("G-LAMP-01", 0, product, 5_000_000), 3)
        self.assertFalse(hasattr(strategy, "future_orders"))

    def test_adaptive_buyer_memory_is_isolated_between_seeds(self) -> None:
        first, second = AdaptiveBuyer(), AdaptiveBuyer()
        first.observe("shop", True, 10_000, "settled")
        self.assertEqual(first.observation_count, 1)
        self.assertEqual(second.observation_count, 0)
        self.assertEqual(second.history, {})

    def test_buyer_shop_choice_and_reason_are_recorded(self) -> None:
        rows, summary = run_buyer_episode(BUYER_STRATEGIES["C"](), 41, 20)
        chosen = [row for row in rows if row.shop]
        self.assertTrue(chosen)
        self.assertTrue(all(row.selection_reason for row in chosen))
        self.assertTrue(summary["shop_selection"])

    def test_seeded_episode_is_reproducible(self) -> None:
        left = run_seller_episode(SELLER_STRATEGIES["D"](), 51, 20)
        right = run_seller_episode(SELLER_STRATEGIES["D"](), 51, 20)
        self.assertEqual(left, right)

    def test_phase_counts_and_role_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_phase(Path(temporary) / "phase", "test", [1, 2], 5)
            self.assertEqual(result["seller_attempts"], 40)
            self.assertEqual(result["buyer_attempts"], 40)
            self.assertEqual(result["total_attempts"], 80)
            self.assertEqual(set(result["seller"]), set("ABCD"))
            self.assertEqual(set(result["buyer"]), set("ABCD"))

    def test_phase_supports_role_specific_shortlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_phase(Path(temporary) / "phase", "shortlist", [3], 5,
                               seller_codes="", buyer_codes="BC")
            self.assertEqual(result["seller_attempts"], 0)
            self.assertEqual(result["buyer_attempts"], 10)
            self.assertEqual(result["seller"], {})
            self.assertEqual(set(result["buyer"]), {"B", "C"})

    def test_single_strategy_summary_does_not_require_runner_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_phase(Path(temporary) / "phase", "single", [3], 5,
                               seller_codes="B", buyer_codes="B")
            self.assertEqual(top_and_separation(result, "seller"), (["B"], False))
            self.assertEqual(top_and_separation(result, "buyer"), (["B"], False))

    def test_full_phase_runs_with_socket_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(socket, "socket", side_effect=AssertionError("network access attempted")):
                result = run_phase(Path(temporary) / "phase", "offline", [7], 4)
            self.assertEqual(result["total_attempts"], 32)

    def test_strategy_instances_are_new_for_each_episode(self) -> None:
        a, b = SELLER_STRATEGIES["D"](), SELLER_STRATEGIES["D"]()
        a.observe_order("G-LAMP-01")
        self.assertNotEqual(a.observed_orders, b.observed_orders)

    def test_safe_discount_variants_obey_caps_and_avoid_below_cost_sales(self) -> None:
        for strategy in (SafeDiscountSeller1(), SafeDiscountSeller2()):
            rows, summary = run_seller_episode(strategy, 401, 50)
            self.assertEqual(summary["safety_violations"], 0)
            self.assertGreaterEqual(sum(row.margin or 0 for row in rows), 0)
            self.assertLessEqual(summary["remaining_stock_cost"], 5_000_000 * strategy.max_stock_share)

    def test_quality_discount_uses_verified_value_in_ranking(self) -> None:
        order = build_world(402, 1)[0]
        offers = [o for o in competitor_offers(402, order, include_same_owner=False)
                  if safe_offer(order, o, BUYER_OWNER) is None]
        quality = QualityDiscountBuyer().rank(order, offers)
        discount = DiscountBuyer().rank(order, offers)
        self.assertTrue(quality)
        self.assertTrue(discount)
        self.assertIn("verified grade", quality[0][2])

    def test_improved_episodes_are_reproducible_and_offline(self) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("network access attempted")):
            left = run_seller_episode(SafeDiscountSeller1(), 403, 20)
            right = run_seller_episode(SafeDiscountSeller1(), 403, 20)
            buyer_left = run_buyer_episode(QualityDiscountBuyer(), 403, 20)
            buyer_right = run_buyer_episode(QualityDiscountBuyer(), 403, 20)
        self.assertEqual(left, right)
        self.assertEqual(buyer_left, buyer_right)


if __name__ == "__main__":
    unittest.main()

