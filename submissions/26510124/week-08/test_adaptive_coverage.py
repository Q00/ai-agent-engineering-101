"""Recent confirmed own demand deepens proven goods without refreshing history."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from adaptive_stock import candidates, depth_cap, depth_evidence, prepare_control, remaining_credit
from sales_performance import recent_own_counts, sku_metrics
from shop_policy import policy_at


SKU = "G00000001"
OTHER = "G00000002"
NOW = 2_000_000


def sale(seq, age=300, **changes):
    receipt = NOW - age
    return {"ledger": seq, "unit": f"unit-{seq}", "sku": SKU, "category": "bedding",
            "kind": "goods", "grade": "A", "price": 20000, "cost": 10000,
            "receipt_time": receipt, "settled_at": min(NOW, receipt + 10),
            "observed_at": min(NOW, receipt + 20), "supplied_at": receipt - 3600,
            "warranty_hours": 6, "refunds": [], **changes}


def memory(recent=0, mature=3):
    return {"schema": 1, "round": 3, "updated_at": NOW,
            "sales": [sale(i + 1, 7 * 3600 + i * 100) for i in range(mature)]
            + [sale(100 + i, 300 + i * 20) for i in range(recent)]}


class RecentOwnDemandTests(unittest.TestCase):
    def count(self, rows, **changes):
        return recent_own_counts({"sales": rows, **changes}, NOW)[SKU]

    def test_original_local_receipt_time_defines_inclusive_hour(self):
        rows = [sale(1, 3600), sale(2, 3600.001), sale(3, 0), sale(4, -1)]
        self.assertEqual(self.count(rows), 2)

    def test_late_observation_and_raw_market_time_do_not_refresh_old_sale(self):
        row = sale(1, 7200, observed_at=NOW, market_settled_at=NOW)
        self.assertEqual(self.count([row]), 0)
        row["settled_at"] = NOW - 1
        self.assertEqual(self.count([row]), 0)
        row.pop("settled_at")
        self.assertEqual(self.count([row]), 0)

    def test_receipt_can_support_clock_skew_without_market_time_conversion(self):
        row = sale(1, 30, observed_at=NOW, market_settled_at=NOW - 810)
        row.pop("settled_at")
        self.assertEqual(self.count([row]), 1)
        row["market_settled_at"] = NOW + 10000
        self.assertEqual(self.count([row]), 1)

    def test_invalid_missing_or_future_local_timestamps_never_count(self):
        variants = ({"receipt_time": None}, {"receipt_time": NOW + .1},
                    {"receipt_time": True}, {"settled_at": None},
                    {"settled_at": NOW + .1}, {"settled_at": NOW - 301})
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertEqual(self.count([sale(1, **changes)]), 0)
        row = sale(1)
        row.pop("receipt_time")
        self.assertEqual(self.count([row]), 0)

    def test_positive_confirmation_identity_and_receipt_fields_are_required(self):
        for changes in ({"ledger": 0}, {"ledger": True}, {"unit": ""},
                        {"price": 0}, {"category": ""}, {"sku": ""}):
            with self.subTest(changes=changes):
                self.assertEqual(self.count([sale(1, **changes)]), 0)

    def test_duplicate_observations_count_once_without_mutating_memory(self):
        rows = [sale(1), sale(1, observed_at=NOW), sale(2)]
        before = deepcopy(rows)
        self.assertEqual(self.count(rows), 2)
        self.assertEqual(rows, before)

    def test_conflicting_ledger_unit_or_receipt_identity_is_excluded(self):
        for second in (sale(1, unit="different"), sale(2, unit="unit-1"),
                       sale(1, sku=OTHER), sale(1, receipt_time=NOW - 100)):
            with self.subTest(second=second):
                self.assertEqual(self.count([sale(1), second]), 0)

    def test_reset_excludes_older_receipts_and_keeps_boundary(self):
        rows = [sale(1, 300), sale(2, 301)]
        self.assertEqual(self.count(rows, reset_at=NOW - 300), 1)

    def test_sku_metrics_exposes_count_without_changing_warranty_maturity(self):
        mem = memory(recent=6)
        before = deepcopy(mem)
        stats = sku_metrics(mem, NOW)[SKU]
        self.assertEqual(stats["recent_own_confirmed_1h"], 6)
        self.assertEqual(stats["recent_own_window_seconds"], 3600)
        self.assertEqual(stats["recent_own_time_basis"], "local_receipt_time")
        self.assertEqual(stats["mature"], 3)
        self.assertEqual(stats["pending"], 6)
        self.assertEqual(stats["unknown"], 0)
        self.assertEqual(mem, before)


class AdaptiveCoverageTests(unittest.TestCase):
    def setUp(self):
        self.cfg = policy_at()["restock"]
        self.cfg["max_units_per_sku"] = 4
        self.rules = self.cfg["adaptive_goods"]
        self.rules["proven_max_units_per_sku"] = 2
        self.rules["replenishment"] = {"enabled": True, "lookback_seconds": 3600,
            "cover_seconds": 2400, "max_units_per_sku": 4,
            "distinct_skus_before_depth": 4, "food_priority_units": 1}

    def stats(self, recent=6, mature=3):
        return sku_metrics(memory(recent=recent, mature=mature), NOW)[SKU]

    def test_recent_four_five_and_six_cover_three_four_and_four_units(self):
        for count, expected in ((0, 2), (1, 2), (3, 2), (4, 3), (5, 4), (6, 4), (30, 4)):
            with self.subTest(count=count):
                self.assertEqual(depth_cap(self.stats(count), self.cfg), expected)

    def test_unproven_pending_only_unknown_refunded_or_loss_cannot_grow(self):
        self.assertEqual(depth_cap(self.stats(mature=2), self.cfg), 1)
        self.assertEqual(depth_cap(self.stats(mature=0), self.cfg), 1)
        for changes in ({"unknown": 1}, {"refunded": 1}, {"net_estimate": 0}, {"speed_score": 0}):
            with self.subTest(changes=changes):
                self.assertEqual(depth_cap({**self.stats(), **changes}, self.cfg), 1)

    def test_real_missing_cost_and_observed_refund_block_growth(self):
        for changes in ({"cost": None}, {"refunds": [{"seq": 999, "amount": 20000, "ts": NOW - 10}]}):
            mem = memory(recent=6)
            mem["sales"][-1].update(changes)
            stats = sku_metrics(mem, NOW)[SKU]
            self.assertEqual(depth_cap(stats, self.cfg), 1)

    def test_food_stays_one_and_disabled_or_missing_policy_keeps_legacy_depth(self):
        stats = self.stats()
        self.assertEqual(depth_cap(stats, self.cfg, "food"), 1)
        self.rules["replenishment"]["enabled"] = False
        self.assertEqual(depth_cap(stats, self.cfg), 2)
        self.rules.pop("replenishment")
        self.assertEqual(depth_cap(stats, self.cfg), 2)

    def test_missing_mismatched_or_malformed_recent_evidence_keeps_baseline(self):
        for changes in ({"recent_own_confirmed_1h": None}, {"recent_own_confirmed_1h": True},
                        {"recent_own_confirmed_1h": -1}, {"recent_own_window_seconds": 7200},
                        {"recent_own_time_basis": "observed_at"}):
            with self.subTest(changes=changes):
                self.assertEqual(depth_cap({**self.stats(), **changes}, self.cfg), 2)
        stats = self.stats()
        stats.pop("recent_own_confirmed_1h")
        self.assertEqual(depth_cap(stats, self.cfg), 2)

    def test_global_and_replenishment_limits_both_apply(self):
        self.cfg["max_units_per_sku"] = 3
        self.assertEqual(depth_cap(self.stats(), self.cfg), 3)
        self.cfg["max_units_per_sku"] = 4
        self.rules["replenishment"]["max_units_per_sku"] = 3
        self.assertEqual(depth_cap(self.stats(), self.cfg), 3)

    def test_evidence_explains_count_coverage_and_legacy_fallback(self):
        evidence = depth_evidence(self.stats(4), self.cfg)
        self.assertEqual(evidence["recent_own_confirmed_1h"], 4)
        self.assertEqual(evidence["coverage_seconds"], 2400)
        self.assertEqual(evidence["coverage_units"], 3)
        self.assertEqual(evidence["depth_cap"], 3)
        self.assertEqual(evidence["depth_reason"], "recent_own_demand_coverage")
        self.assertEqual(depth_evidence(self.stats(0), self.cfg)["depth_reason"],
                         "proven_without_recent_demand")

    def test_outstanding_credits_survive_capacity_growth_and_decline(self):
        row = {"planned_qty": 7, "settled_qty": 4, "retry_credit": 0}
        before = deepcopy(row)
        self.assertEqual(remaining_credit(row, capacity=4), 1)
        self.assertEqual(remaining_credit(row, capacity=2), 0)
        self.assertEqual(remaining_credit(row, capacity=3), 0)
        self.assertEqual(remaining_credit(row, capacity=4), 1)
        self.assertEqual(row, before)
        for capacity in (0, 5, True, 2.0):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    remaining_credit(row, capacity=capacity)

    def test_prepare_control_preserves_unresolved_history_when_depth_declines(self):
        row = {"planned_qty": 4, "settled_qty": 0, "retry_credit": 0,
               "last_ledger": 0, "observed_at_plan": 0}
        old = {SKU: row}
        result = prepare_control(old, {"sales": []}, {}, self.rules,
                                 lifecycle_stats={SKU: {"unresolved_cost": 10000}},
                                 held={}, next_window=True, unresolved_receipts=True)
        self.assertEqual(result, old)
        self.assertEqual(remaining_credit(result[SKU], capacity=4), 0)
        self.assertEqual(remaining_credit(result[SKU], capacity=2), 0)

    def test_candidate_pool_retains_surge_cash_category_and_held_caps(self):
        product = {"name": "Fixture", "category": "bedding", "kind": "goods", "list_price": 100000}
        inv = {"round": 3, "start": 5000000, "balance": 5000000, "injected": 0,
               "units": [], "surge": []}
        profile = {"sales": 6, "buyers": 3, "named_shops": 2, "shops": 2,
                   "median_price": 75000, "sample_to_stock_ratio": .5,
                   "observed_stock": 12, "comparables": [], "evidence_id": "fixture"}
        stats = {SKU: self.stats()}
        def pool():
            return candidates(inv, {SKU: product}, {"bedding"}, stats, self.cfg, NOW, {})
        with patch("adaptive_stock.demand_profiles", return_value={SKU: profile}):
            self.assertEqual(pool()[SKU]["cap"], 4)
            self.assertEqual(pool()[SKU]["evidence"]["coverage_units"], 4)
            inv["units"] = [{"unit": f"held-{i}", "sku": SKU, "category": "bedding",
                              "kind": "goods", "cost": 10000} for i in range(3)]
            self.assertIn(SKU, pool())
            inv["units"].append({**inv["units"][0], "unit": "held-last"})
            self.assertNotIn(SKU, pool())
            inv["units"] = []
            inv["balance"] = 2000000
            self.assertEqual(pool(), {})
            inv["balance"] = 5000000
            inv["surge"] = [{"sku": SKU, "factor": 1.11, "units": 100}]
            self.assertEqual(pool(), {})
            inv["surge"] = []
            inv["units"] = [{"unit": "other", "sku": OTHER, "category": "bedding",
                              "kind": "goods", "cost": 3000000}]
            self.assertEqual(pool(), {})


if __name__ == "__main__":
    unittest.main()
