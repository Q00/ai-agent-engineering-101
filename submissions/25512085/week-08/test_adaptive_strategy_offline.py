"""Adaptive policy regressions: fake observations and isolated temporary state only."""
import json
import copy
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_buyer as buyer
import adaptive_strategy as adaptive
import restock
import store
import test_buyer_retry_offline as retry_tests

NOW = 1_790_000_000


class AdaptiveRestockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="adaptive-offline-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.hub = store.default_hub()
        self.hub["adaptive_ledger_coverage"] = {"since": NOW-1201, "through": NOW, "seq": 100}
        self.hub["own_sale_events"] = {str(i): {"sku": "core", "ts": NOW-i} for i in range(3)}
        self.inv = {"balance": 8_000_000, "units": [],
                    "restock": {"opens_at": NOW-10, "closes_at": NOW+170}}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.stack.enter_context(patch.object(restock.time, "time", return_value=NOW))
        self.stack.enter_context(patch.object(restock, "ROUND_END_TS", NOW+10_000_000))
        self.stack.enter_context(patch.object(restock, "queue_forum_action"))
        self.stack.enter_context(patch.object(store.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")))

    def decide(self, now=NOW, fresh=True):
        return adaptive.decide_restock(self.hub, self.inv, now, inventory_fresh=fresh)

    def test_twenty_minute_idempotence_and_restart_persistence(self):
        first = copy.deepcopy(self.decide())
        self.assertEqual(first["mode"], "metrics-only")
        self.assertNotIn("exploration_skus", first)
        self.inv["balance"] = 0
        store.save_hub(self.hub, self.path / "hub.json")
        self.hub = store.load_hub(self.path / "hub.json")
        self.assertEqual(self.decide(NOW+1199), first)
        self.hub["adaptive_ledger_coverage"]["through"] = NOW+1200
        self.assertEqual(self.decide(NOW+1200)["mode"], "metrics-only")

    def test_incomplete_observation_keeps_current_policy(self):
        first = copy.deepcopy(self.decide())
        self.hub["adaptive_ledger_coverage"]["through"] = NOW-600
        result = self.decide(NOW+1200)
        for field in ("mode", "baseline"):
            self.assertEqual(result[field], first[field])
        self.assertIn("incomplete", result["reason"])

    def test_missing_age_or_stale_inventory_never_expands(self):
        for fresh, units in [(False, []), (True, [{"sku": "x", "cost": 100}])]:
            with self.subTest(fresh=fresh):
                self.hub.pop("adaptive_strategy", None)
                self.inv["units"] = units
                self.assertEqual(self.decide(fresh=fresh)["mode"], "metrics-only")

    def test_no_own_turnover_never_expands_even_with_rank_or_cash(self):
        self.hub["own_sale_events"] = {}
        self.hub["rank"] = 99
        self.hub["demand_radar"]["missed_ts"] = {"core": [NOW]}
        self.assertEqual(self.decide()["mode"], "metrics-only")

    def test_recent_interval_excludes_old_and_future_sales(self):
        self.hub["own_sale_events"] = {str(i): {"sku": "core", "ts": t}
                                       for i, t in enumerate([NOW-1200, NOW+1, NOW-1300])}
        self.assertEqual(self.decide()["mode"], "metrics-only")
        self.assertEqual(self.hub["adaptive_strategy"]["baseline"]["own_sales"], 0)

    def test_buying_cash_drop_alone_does_not_trigger_recovery(self):
        self.decide()
        self.inv["balance"] -= 1_200_000
        self.inv["units"] = [{"sku": "new", "cost": 1_200_000, "supplied_at": NOW}]
        self.hub["adaptive_ledger_coverage"]["through"] = NOW+1200
        self.hub["own_sale_events"] = {str(i): {"sku": "core", "ts": NOW+1200-i} for i in range(3)}
        self.assertEqual(self.decide(NOW+1200)["mode"], "metrics-only")

    def catalog(self, n=70):
        return {f"sku{i}": {"list_price": 20_000, "kind": "goods", "category": f"cat{i}"}
                for i in range(n)}

    def plan(self, catalog):
        return restock.plan_with_hub(self.hub, self.path, catalog, self.inv, [], inventory_fresh=True)

    def test_expansion_reaches_sixty_not_unbounded_and_no_stocked_topup(self):
        catalog = self.catalog()
        for info in catalog.values():
            info["list_price"] = 100_000  # Existing full-catalog filter requires demand or high goods.
        # Adaptive no longer grants any exploration allowance without popularity.
        self.inv["units"] = [{"sku": f"sku{i}", "grade": "A", "cost": 120_000,
                               "supplied_at": NOW-100} for i in range(55)]
        plan = self.plan(catalog)
        self.assertEqual(plan["buy"], [])
        self.assertEqual(self.hub["adaptive_strategy"]["mode"], "metrics-only")
        self.assertEqual(self.plan(catalog)["buy"], [])

    def test_old_stock_reduces_exploration_only_and_preserves_core(self):
        catalog = self.catalog()
        for info in catalog.values():
            info["list_price"] = 300_000
        # Keep core spend at 360k after the velocity target changes from 3 to 4,
        # so remaining budget still distinguishes recovery from normal breadth.
        catalog["core"] = {"list_price": 120_000, "kind": "goods", "category": "core"}
        catalog["old"] = {"list_price": 600_000, "kind": "goods", "category": "old"}
        self.inv["units"] = [{"sku": "old", "grade": "A", "cost": 400_000,
                               "supplied_at": NOW-21601}]
        baseline_hub = copy.deepcopy(self.hub)
        self.hub["demand_radar"]["sku_inquiries"] = {"core": [{"ts": NOW, "miss": True}]*4}
        baseline_hub = copy.deepcopy(self.hub)
        plan = self.plan(catalog)
        self.assertEqual(self.hub["adaptive_strategy"]["mode"], "metrics-only")
        # Four inquiries set the hot target; age is only a risk metric.
        self.assertIn({"sku": "core", "qty": 4}, plan["buy"])
        window = next(iter(self.hub["restock_windows"].values()))
        self.assertNotIn("exploration_reserved", window)
        recover_buys = len(plan["buy"])
        self.hub = baseline_hub
        self.inv["units"][0]["supplied_at"] = NOW-100
        normal = self.plan(catalog)
        self.assertIn({"sku": "core", "qty": 4}, normal["buy"])
        self.assertEqual(len(normal["buy"]), recover_buys)

    def test_cash_floor_and_absolute_window_cap(self):
        catalog = self.catalog()
        self.inv["balance"] = 600_000
        self.assertEqual(self.plan(catalog)["buy"], [])
        self.inv["balance"] = 50_000_000
        self.plan(catalog)
        for window in self.hub["restock_windows"].values():
            self.assertLessEqual(window["reserved"], 1_200_000)
            self.assertEqual(window["window_cap"], 0)  # Started below cash floor.

    def test_started_window_budget_does_not_increase_after_expansion(self):
        catalog = self.catalog()
        self.inv["balance"] = 5_600_000
        self.plan(catalog)
        self.inv["balance"] = 9_000_000
        self.hub["adaptive_strategy"].update(mode="expand", exploration_skus=60)
        self.plan(catalog)
        window = next(iter(self.hub["restock_windows"].values()))
        self.assertEqual(window["window_cap"], int(restock.DEFAULT_RATIO * 5_600_000 / restock.FILL_WINDOWS))
        self.assertEqual(window["reserved"], 0)  # Expansion is not demand.

    def test_stocked_food_without_own_sales_gets_zero_more(self):
        catalog = self.catalog()
        catalog["food"] = {"list_price": 20_000, "kind": "food", "category": "food", "shelf_hours": 400}
        self.inv["units"] = [{"sku": "food", "grade": "A", "cost": 10_000,
                               "supplied_at": NOW-100}]
        self.hub["demand_radar"]["sku_sold_ts"] = {"food": [NOW]*100}
        # Market 21+ is now popular, even without own sales: top up to two.
        self.assertIn({"sku": "food", "qty": 1}, self.plan(catalog)["buy"])

    def test_ledger_gap_resets_coverage_and_duplicate_rows_cannot_extend_it(self):
        hub = {}
        rows = [{"seq": 1, "ts": NOW-1300}, {"seq": 2, "ts": NOW-100}]
        adaptive.note_ledger_coverage(hub, rows, NOW)
        self.assertEqual(hub["adaptive_ledger_coverage"]["since"], NOW-1300)
        adaptive.note_ledger_coverage(hub, [{"seq": 5, "ts": NOW}], NOW)
        self.assertEqual(hub["adaptive_ledger_coverage"]["since"], NOW)
        adaptive.note_ledger_coverage(hub, rows, NOW)
        self.assertEqual(hub["adaptive_ledger_coverage"]["through"], NOW)


class AdaptiveBuyerTests(unittest.TestCase):
    def setUp(self):
        self.hub = {}
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def fail(self, reason="price", sku="a", url="https://one.invalid", count=3):
        for i in range(count):
            adaptive.record_failure(self.hub, f"order-{i}", url, sku, reason, NOW-10+i)

    def candidates(self):
        return [(100, {"url": "https://one.invalid"}, {"sku": "a"}),
                (90, {"url": "https://one.invalid"}, {"sku": "b"}),
                (80, {"url": "https://two.invalid"}, {"sku": "a"})]

    def test_price_cooldown_is_sku_local_and_needs_three_distinct_orders(self):
        for _ in range(10):
            adaptive.record_failure(self.hub, "same-order", "https://one.invalid", "a", "price", NOW-10)
        adaptive.refresh_buyer_policy(self.hub, NOW)
        self.assertEqual(self.hub["adaptive_strategy"]["buyer_cooldowns"], [])
        self.hub = {}
        self.fail()
        adaptive.refresh_buyer_policy(self.hub, NOW)
        self.assertEqual(adaptive.filter_candidates(self.candidates(), self.hub, NOW), self.candidates()[1:])

    def test_timeout_shop_scope_and_twenty_minute_expiry(self):
        self.fail("network-timeout")
        adaptive.refresh_buyer_policy(self.hub, NOW)
        self.assertEqual(adaptive.filter_candidates(self.candidates(), self.hub, NOW), self.candidates()[2:])
        self.assertEqual(adaptive.filter_candidates(self.candidates(), self.hub, NOW+1200), self.candidates())

    def test_all_soft_cooled_fallback_but_no_unsigned_or_dishonest_fallback(self):
        self.fail()
        adaptive.refresh_buyer_policy(self.hub, NOW)
        self.assertEqual(adaptive.filter_candidates(self.candidates()[:1], self.hub, NOW), self.candidates()[:1])
        self.hub = {}
        self.fail("unsigned")
        adaptive.refresh_buyer_policy(self.hub, NOW)
        self.assertEqual(adaptive.filter_candidates(self.candidates()[:2], self.hub, NOW), [])

    def test_restart_and_repeated_hooks_do_not_redecide(self):
        self.fail()
        adaptive.refresh_buyer_policy(self.hub, NOW)
        saved = json.loads(json.dumps(self.hub))
        self.fail("timeout", sku="b")
        adaptive.refresh_buyer_policy(self.hub, NOW+1199)
        self.assertEqual(self.hub["adaptive_strategy"]["buyer_cooldowns"], saved["adaptive_strategy"]["buyer_cooldowns"])

    def test_failure_history_bounded_and_old_observations_expire(self):
        for i in range(1000):
            adaptive.record_failure(self.hub, str(i), "https://one.invalid", "a", "price", NOW)
        self.assertEqual(len(self.hub["adaptive_strategy"]["buyer_failures"]), 600)
        adaptive.refresh_buyer_policy(self.hub, NOW+1200)
        self.assertEqual(self.hub["adaptive_strategy"]["buyer_failures"], [])


class MissingStarsTests(unittest.TestCase):
    def test_null_stars_do_not_crash_or_become_ratings(self):
        memory = {"shop": {"recent": [{"stars": None}, {"stars": 5}]}}
        result = buyer.summarize_shop({"name": "shop", "url": "https://offline.invalid",
                                      "reviews": [{"stars": None}]}, memory)
        self.assertEqual(result["mem_avg"], 5)
        self.assertEqual(result["mem_count"], 2)
        self.assertFalse(result["dishonest"])

    def test_null_stars_archive_without_inventing_score(self):
        memory = {"shop": {"recent": [{"stars": None}] * 5}}
        with tempfile.TemporaryDirectory(prefix="adaptive-stars-") as tmp:
            path = Path(tmp) / "memory.json"
            with patch.object(buyer, "MEMORY_FILE", path):
                buyer.update_memory(memory, "shop", {"stars": 4})
            saved = json.loads(path.read_text(encoding="utf-8"))["shop"]
        self.assertEqual(saved.get("archived_count", 0), 0)
        self.assertEqual(len(saved["recent"]), 5)


class AdaptiveBuyerHookTests(unittest.TestCase):
    setUp = retry_tests.BuyerRetryTests.setUp
    run_market = retry_tests.BuyerRetryTests.run_market

    def test_unsigned_failures_persist_then_selector_skips_shop_after_refresh(self):
        for i in range(3):
            self.order["order"] = f"unique-{i}"
            run = self.run_market(["unsigned"])
            self.assertIsNone(run.result["receipt"])
        events = self.hub["adaptive_strategy"]["buyer_failures"]
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e["reason"] == "unsigned" for e in events))
        with contextlib.redirect_stderr(io.StringIO()):
            adaptive.refresh_buyer_policy(self.hub, self.now)
        run = self.run_market(["valid", "valid"])
        self.assertEqual(run.cards, [1])
        self.assertEqual(run.result["accept"]["cert"], True)

    def test_counter_timeout_never_becomes_stale_ask_price_failure(self):
        run = self.run_market(["counter-timeout", "valid"])
        self.assertIsNotNone(run.result["receipt"])
        self.assertEqual(self.hub["adaptive_strategy"]["buyer_failures"][0]["reason"], "timeout")

    def test_only_transport_timeout_is_shop_wide(self):
        with patch.object(buyer, "rpc_call", side_effect=buyer.httpx.ReadTimeout("offline")):
            run = self.run_market(["valid"])
        self.assertIsNone(run.result["receipt"])
        self.assertEqual(self.hub["adaptive_strategy"]["buyer_failures"][0]["reason"], "network-timeout")

    def test_expired_negotiation_still_records_pair_local_timeout(self):
        run = self.run_market(["working", "valid"], elapsed=10)
        self.assertIsNotNone(run.result["receipt"])
        event = self.hub["adaptive_strategy"]["buyer_failures"][0]
        self.assertEqual(event["reason"], "timeout")
        self.assertEqual(event["sku"], self.sku)

    def test_record_buyer_order_refresh_uses_only_temp_hub(self):
        path = self.tmp / "adaptive-hub.json"
        store.save_hub(self.hub, path)
        with contextlib.redirect_stderr(io.StringIO()):
            result = store.record_buyer_order("food", path)
        saved = store.load_hub(path)
        self.assertEqual(result, saved["adaptive_strategy"])
        self.assertEqual(saved["demand_radar"]["buyer_orders"]["food"], 1)


if __name__ == "__main__":
    unittest.main()
