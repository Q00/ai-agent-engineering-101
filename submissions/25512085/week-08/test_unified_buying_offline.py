"""POLICY §9 contract: fake time, unique temporary files, network forbidden."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import restock
import store

NOW = 1_790_000_000


class BuyingFixture:
    def setUp(self):
        approved = Path("C:/Users/clair/AppData/Local/Temp/opencode")
        self.tmp = tempfile.TemporaryDirectory(prefix="unified-buying-", dir=approved if approved.exists() else None)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.hub = store.default_hub()
        self.hub["no_rebuy_detection_since"] = NOW - restock.NO_REBUY_AGE_S
        self.catalog = {}
        self.inv = {"balance": 6_000_000, "units": [], "storage_per_day": .01,
                    "restock": {"opens_at": NOW-20, "closes_at": NOW+160}}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stderr = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stack.enter_context(patch.object(restock.time, "time", return_value=NOW))
        self.stack.enter_context(patch.object(restock, "ROUND_END_TS", NOW+10_000_000))
        self.stack.enter_context(patch.object(restock, "queue_forum_action"))
        self.stack.enter_context(patch.object(store.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")))

    def item(self, sku="G12345678", category="goods", price=20_000, kind="goods", shelf=400):
        self.catalog[sku] = {"category": category, "list_price": price, "kind": kind, "shelf_hours": shelf}
        return sku

    def stock(self, sku, n=1, cost=15_000, age=0, grade="A"):
        self.inv["units"].extend({"sku": sku, "grade": grade, "cost": cost, "supplied_at": NOW-age}
                                 for _ in range(n))

    def inquiry(self, sku, n=1, miss=True, age=0):
        self.hub["demand_radar"].setdefault("sku_inquiries", {})[sku.lower()] = [
            {"ts": NOW-age, "miss": miss} for _ in range(n)]

    def market(self, sku, n=21, age=0):
        self.hub["demand_radar"].setdefault("sku_sold_ts", {})[sku] = [NOW-age]*n

    def plan(self, rows=()):
        return restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, list(rows))

    def buys(self):
        return {b["sku"]: b["qty"] for b in self.plan()["buy"]}

    def state(self):
        return self.hub["restock_windows"][str(restock.supply_window(self.inv))]

    def diag(self):
        return json.loads(self.stderr.getvalue().splitlines()[-1])

    def cost(self, plan):
        return sum(restock.estimated_cost(self.hub, b["sku"], self.catalog[b["sku"]])*b["qty"] for b in plan["buy"])


class UnifiedBuyingTests(BuyingFixture, unittest.TestCase):
    def test_grade_inquiry_boundaries(self):
        for n, expected in [(0, ("none", 0)), (1, ("observe", 1)), (2, ("popular", 2)),
                            (3, ("popular", 2)), (4, ("hot", 4)), (8, ("hot", 8)), (50, ("hot", 8))]:
            with self.subTest(n=n):
                self.assertEqual(restock.popularity(n, 0, 0), expected)

    def test_grade_market_boundaries(self):
        for n, expected in [(10, ("none", 0)), (11, ("observe", 1)), (20, ("observe", 1)), (21, ("popular", 2))]:
            self.assertEqual(restock.popularity(0, 0, n), expected)
        self.assertEqual(restock.popularity(0, 2, 0), ("popular", 2))

    def public_shops(self, costs, revenues=None):
        self.item(price=4_000_000)
        shops = [{"name": str(i), "stock": {"G12345678": cost/3_000_000}}
                 for i, cost in enumerate(costs)]
        metrics = [{"name": str(i), "revenue": (revenues or list(range(len(costs))))[i], "profit": 0}
                   for i in range(len(costs))]
        (self.path / "shops.json").write_text(json.dumps({"shops": shops}), encoding="utf-8")
        (self.path / "board.json").write_text(json.dumps({"shops": metrics}), encoding="utf-8")
        self.public_data = shops, metrics

    def test_ratio_median_top_five(self):
        self.public_shops([5_000_000]*5+[0], [10, 9, 8, 7, 6, 0])
        r, source = restock.market_ratio(*self.public_data, self.catalog)
        self.assertEqual(r, .5)
        self.assertFalse(source["default"])
        self.assertEqual(source["shops"], 5)

    def test_ratio_clamp_both_bounds(self):
        for cost, expected in [(0, restock.RATIO_MIN), (50_000_000, restock.RATIO_MAX)]:
            self.public_shops([cost]*3)
            self.assertEqual(restock.market_ratio(*self.public_data, self.catalog)[0], expected)

    def test_ratio_defaults_missing_and_under_three(self):
        self.assertEqual(restock.market_ratio([], [], {})[0], .4)
        self.public_shops([5_000_000]*2)
        r, source = restock.market_ratio(*self.public_data, self.catalog)
        self.assertEqual(r, .4)
        self.assertTrue(source["default"])
        (self.path / "board.json").unlink()
        self.assertTrue(restock.market_ratio(self.public_data[0], [], self.catalog)[1]["default"])

    def build_with_public_api(self, payloads=None, now=NOW):
        path = self.path / "api-hub.json"
        locked = []

        @contextlib.contextmanager
        def mutate():
            with store.mutate_hub(path) as hub:
                locked.append(True)
                try:
                    yield hub
                finally:
                    locked.pop()

        def urlopen(req, timeout):
            self.assertFalse(locked, "public API I/O inside hub lock")
            self.assertLessEqual(timeout, 3)
            self.assertEqual(req.get_method(), "GET")
            if payloads is None:
                raise OSError("offline failure")
            endpoint = req.full_url.rsplit("/", 1)[-1]
            return io.BytesIO(json.dumps(payloads[endpoint]).encode())

        with patch.object(restock.time, "time", return_value=now), \
             patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", side_effect=lambda: store.load_hub(path)), \
             patch.object(restock, "mutate_hub", side_effect=mutate), \
             patch.object(restock, "fetch_ledger_rows", return_value=[]), \
             patch.object(restock, "fetch_own_shop_urls", return_value=[]), \
             patch.object(store.urllib.request, "urlopen", side_effect=urlopen) as mocked:
            restock.build_restock_plan(self.path)
        return store.load_hub(path), mocked

    def test_public_api_full_stock_outside_lock_and_cache_persistence(self):
        self.public_shops([5_000_000]*3)
        shops, board = self.public_data
        # Poison both local files: neither belongs to the production R input contract.
        (self.path / "shops.json").write_text(json.dumps([{"name": "0", "offers": []}]), encoding="utf-8")
        (self.path / "board.json").write_text("{}", encoding="utf-8")
        hub, mocked = self.build_with_public_api({"shops": {"shops": shops}, "board": {"shops": board}})
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(hub["market_ratio_cache"], {"ratio": .5, "ts": NOW, "shops": 3,
                         "source": "/api/shops+/api/board: top revenue median"})
        self.assertEqual(self.diag()["R"], .5)
        self.assertFalse(self.diag()["R_source"]["cached"])

    def test_public_api_failure_uses_fresh_cache_then_expires(self):
        self.public_shops([5_000_000]*3)
        shops, board = self.public_data
        self.build_with_public_api({"shops": shops, "board": board})
        hub, _ = self.build_with_public_api(now=NOW+1800)
        self.assertEqual(self.diag()["R"], .5)
        self.assertTrue(self.diag()["R_source"]["cached"])
        self.assertEqual(hub["market_ratio_cache"]["ts"], NOW)  # failures cannot renew cache
        self.build_with_public_api(now=NOW+1801)
        self.assertEqual(self.diag()["R"], .4)
        self.assertTrue(self.diag()["R_source"]["default"])

    def test_public_api_failure_without_cache_ignores_local_files(self):
        self.public_shops([5_000_000]*3)
        hub, _ = self.build_with_public_api()
        self.assertEqual(self.diag()["R"], .4)
        self.assertNotIn("market_ratio_cache", hub)

    def test_insufficient_public_api_shops_does_not_replace_cache(self):
        self.public_shops([5_000_000]*3)
        shops, board = self.public_data
        self.build_with_public_api({"shops": shops, "board": board})
        hub, _ = self.build_with_public_api({"shops": shops[:2], "board": board}, now=NOW+1)
        self.assertEqual(self.diag()["R"], .5)
        self.assertTrue(self.diag()["R_source"]["cached"])
        self.assertEqual(hub["market_ratio_cache"]["ts"], NOW)

    def test_equity_target_deficit_and_window_formula(self):
        sku = self.item()
        self.stock(sku, cost=100_000)
        self.plan()
        d = self.diag()
        self.assertEqual(d["E"], 6_100_000)
        self.assertEqual(d["T"], .4*6_100_000)
        self.assertEqual(d["deficit"], 2_340_000)
        self.assertEqual(d["W"], int(d["deficit"] / restock.FILL_WINDOWS))
        self.assertEqual(d["cash_floor"], 1_501_000)

    def test_restore_cost_and_quarter_equity_cap(self):
        sku = self.item(price=2_000_000)
        self.inquiry(sku, 4)
        self.plan()
        d = self.diag()
        self.assertEqual(d["restore_cost"], 6_000_000)
        self.assertEqual(d["W"], restock.WINDOW_EQUITY_CAP * d["E"])
        self.assertEqual(self.state()["reserved"], d["W"])

    def test_cash_floor_limits_window_and_storage_reserved(self):
        sku = self.item(price=1_500_000)
        self.stock(sku, cost=1_000_000)
        self.inquiry(self.item("restore", price=1_000_000), 8)
        self.inv["balance"] = 800_000
        self.plan()
        self.assertEqual(self.diag()["cash_floor"], 710_000)
        self.assertEqual(self.diag()["W"], 90_000)
        self.assertEqual(self.state()["reserved"], 0)

    def test_no_popularity_leaves_budget_unused(self):
        self.item()
        self.assertEqual(self.buys(), {})
        self.assertGreater(self.diag()["W"], 0)
        self.assertEqual(self.state()["reserved"], 0)

    def test_hot_partial_stock_and_target_cap(self):
        a, b = self.item(), self.item("G87654321")
        self.stock(a, 2)
        self.inquiry(a, 4, miss=False)
        self.inquiry(b, 100)
        self.assertEqual(self.buys(), {a: 2, b: 8})

    def test_unheld_ignores_quotes_but_uses_misses_and_market(self):
        sku = self.item()
        self.inquiry(sku, 4, miss=False)
        self.assertEqual(self.buys(), {})
        self.inquiry(sku, 2)
        self.assertEqual(self.buys(), {sku: 2})

    def test_market_can_top_up_to_popular_not_velocity_depth(self):
        sku = self.item()
        self.stock(sku)
        self.market(sku)
        self.assertEqual(self.buys(), {sku: 1})

    def test_40_minute_boundary_and_future_events(self):
        for age, expected in [(2400, 1), (2401, 0), (-1, 0)]:
            self.hub = store.default_hub()
            sku = self.item()
            self.inquiry(sku, age=age)
            self.assertEqual(self.buys().get(sku, 0), expected)

    def test_priority_observe_half_cap_and_unused_remainder(self):
        hot, popular, watch = self.item("hot", price=150_000), self.item("popular", price=150_000), self.item("watch", price=400_000)
        self.inquiry(hot, 4)
        self.inquiry(popular, 2)
        self.inquiry(watch, 1)
        orders = self.plan()["buy"]
        self.assertEqual([b["sku"] for b in orders], [hot, popular, watch])
        state = self.state()
        self.assertGreaterEqual(state["grade_reserved"]["hot"] + state["grade_reserved"]["popular"], state["window_cap"]*.5)
        self.assertLessEqual(state["grade_reserved"]["observe"], state["window_cap"]*.5)
        self.assertLess(state["reserved"], state["window_cap"])

    def test_observe_cannot_take_idle_core_budget(self):
        for i in range(10):
            self.inquiry(self.item(f"watch{i}", price=100_000))
        self.plan()
        unit_cost = restock.estimated_cost(self.hub, "watch0", self.catalog["watch0"])
        half_budget = self.state()["window_cap"] // 2
        self.assertEqual(self.state()["grade_reserved"]["observe"], half_budget // unit_cost * unit_cost)
        self.assertLessEqual(self.state()["reserved"], self.state()["window_cap"]//2)

    def test_same_grade_sorted_expected_margin_not_price(self):
        a, b = self.item("a", price=100_000), self.item("b", price=80_000)
        self.hub["purchase_costs"] = {a: {"cost": 90_000, "ts": NOW}}
        self.inquiry(a, 2)
        self.inquiry(b, 2)
        self.assertEqual([b["sku"] for b in self.plan()["buy"]], [b, a])

    def test_actual_cost_fallback_and_supply_history(self):
        sku = self.item()
        self.assertEqual(restock.estimated_cost(self.hub, sku, self.catalog[sku]), 15_000)
        self.inquiry(sku, 2)
        row = {"seq": 1, "ts": NOW-1, "kind": "supply", "data": {
            "shop": store.OWN_NAME, "sku": sku, "qty": 2, "total": 20_002}}
        self.plan([row])
        self.assertEqual(restock.estimated_cost(self.hub, sku, self.catalog[sku]), 10_001)

    def test_storage_margin_filter(self):
        sku = self.item()
        self.inquiry(sku, 4)
        self.hub["demand_radar"]["sku_settles"] = {sku: [15_500]}
        self.assertEqual(self.buys(), {})
        self.assertEqual(self.diag()["excluded"]["storage_margin"], 1)

    def test_dead_exact_four_hours_goods_and_food(self):
        for kind in ("goods", "food"):
            sku = self.item(kind, kind=kind)
            self.stock(sku, age=14_400)
            self.inquiry(sku, 4)
        self.assertEqual(self.buys(), {})
        self.assertEqual(set(self.hub["no_rebuy"]), {"goods", "food"})
        self.assertEqual(self.diag()["no_rebuy_new"], 2)

    def test_deployment_old_inventory_does_not_bootstrap_mass_dead(self):
        self.hub.pop("no_rebuy_detection_since")
        for i in range(100):
            self.stock(self.item(str(i), kind="food" if i % 2 else "goods"), age=24*3600)
        self.plan()
        self.assertEqual(self.hub["no_rebuy_detection_since"], NOW)
        self.assertEqual(self.hub["no_rebuy"], {})
        self.assertEqual(self.diag()["no_rebuy_new"], 0)

    def test_bootstrap_since_persists_and_dead_requires_full_four_hours(self):
        self.hub.pop("no_rebuy_detection_since")
        sku = self.item()
        self.stock(sku, age=24*3600)
        self.plan()
        path = self.path / "bootstrap.json"
        store.save_hub(self.hub, path)
        self.hub = store.load_hub(path)
        with patch.object(restock.time, "time", return_value=NOW+14_399):
            self.plan()
            self.assertEqual(self.hub["no_rebuy"], {})
        with patch.object(restock.time, "time", return_value=NOW+14_400):
            self.plan()
            self.assertIn(sku, self.hub["no_rebuy"])
        self.assertEqual(self.hub["no_rebuy_detection_since"], NOW)

    def test_sale_during_bootstrap_survives_short_tail_and_prevents_dead(self):
        self.hub.pop("no_rebuy_detection_since")
        sku = self.item()
        self.stock(sku, age=24*3600)
        self.hub["own_shop_urls"] = ["https://own.invalid"]
        self.plan()
        with patch.object(restock.time, "time", return_value=NOW+3600):
            self.plan([{"seq": 1, "ts": NOW+3600, "kind": "settle", "data": {
                "shop": "https://own.invalid", "buyer": "other", "item": sku, "amount": 19_000}}])
        with patch.object(restock.time, "time", return_value=NOW+14_400):
            self.plan()
            self.assertNotIn(sku, self.hub["no_rebuy"])
            self.assertEqual(self.hub["own_sale_events"], {})
            self.assertEqual(self.hub["own_last_sale_ts"][sku], NOW+3600)

    def test_new_unit_after_bootstrap_must_itself_age_four_hours(self):
        self.hub["no_rebuy_detection_since"] = NOW-24*3600
        sku = self.item()
        self.stock(sku, age=14_399)
        self.plan()
        self.assertNotIn(sku, self.hub["no_rebuy"])

    def test_adaptive_old_cost_has_same_bootstrap_guard(self):
        self.hub.pop("no_rebuy_detection_since")
        a, b = self.item("unsold"), self.item("sold")
        self.stock(a, age=24*3600)
        self.stock(b, age=24*3600)
        self.hub["adaptive_ledger_coverage"] = {"since": NOW-1201, "through": NOW}
        state = restock.decide_restock(self.hub, self.inv, NOW, inventory_fresh=True)
        self.assertEqual(state["baseline"]["old_cost"], 0)
        self.assertEqual(self.hub["no_rebuy_detection_since"], NOW)
        self.hub["own_last_sale_ts"] = {b: NOW+3600}
        self.hub["adaptive_ledger_coverage"]["through"] = NOW+14_400
        state = restock.decide_restock(self.hub, self.inv, NOW+14_400, inventory_fresh=True)
        self.assertEqual(state["baseline"]["old_cost"], 15_000)

    def test_dead_not_before_four_hours_and_sale_after_arrival(self):
        a, b = self.item("young"), self.item("sold")
        self.stock(a, age=14_399)
        self.stock(b, age=14_400)
        self.hub["own_last_sale_ts"] = {b: NOW-10_000}
        self.inquiry(a, 4)
        self.inquiry(b, 4)
        self.assertEqual(self.buys(), {a: 3, b: 3})
        self.assertFalse(self.hub["no_rebuy"])

    def test_sale_before_unit_arrival_does_not_clear_dead(self):
        sku = self.item()
        self.stock(sku, age=14_400)
        self.hub["own_last_sale_ts"] = {sku: NOW-14_401}
        self.inquiry(sku, 8)
        self.assertEqual(self.buys(), {})

    def test_dead_persists_restart_after_unit_removed(self):
        sku = self.item()
        self.stock(sku, age=14_400)
        self.plan()
        path = self.path / "hub.json"
        store.save_hub(self.hub, path)
        self.hub = store.load_hub(path)
        self.inv["units"] = []
        self.inquiry(sku, 8)
        self.assertEqual(self.buys(), {})
        self.assertEqual(self.diag()["no_rebuy_total"], 1)

    def test_dead_history_bounded_without_eviction(self):
        self.hub["no_rebuy"] = {str(i): NOW-1 for i in range(500)}
        sku = self.item("G12345678")
        other = self.item("G12345679")
        self.stock(sku, age=14_400)
        self.inquiry(other, 8)
        plan = self.plan()
        buys = {b["sku"]: b["qty"] for b in plan["buy"]}
        self.assertEqual(len(self.hub["no_rebuy"]), 500)
        self.assertTrue(self.hub["no_rebuy_overflow"])
        self.assertNotIn("no_rebuy_history_full", self.diag()["excluded"])
        self.assertGreater(buys.get(other, 0), 0)

    def test_no_demand_two_hour_gate_with_coverage(self):
        sku = self.item()
        self.hub["adaptive_ledger_coverage"] = {"since": NOW-7200, "through": NOW}
        self.plan()
        self.assertEqual(self.diag()["excluded"]["no_demand_2h"], 1)
        self.assertFalse(self.diag()["no_demand_deferred"])
        self.inquiry(sku, age=7200)
        self.plan()
        self.assertNotIn("no_demand_2h", self.diag()["excluded"])
        self.inquiry(sku, age=7201)
        self.plan()
        self.assertEqual(self.diag()["excluded"]["no_demand_2h"], 1)

    def test_no_demand_deferred_incomplete_or_stale_coverage(self):
        self.item()
        for coverage in ({}, {"since": NOW-7199, "through": NOW}, {"since": NOW-8000, "through": NOW-121}):
            self.hub["adaptive_ledger_coverage"] = coverage
            self.plan()
            self.assertTrue(self.diag()["no_demand_deferred"])
            self.assertNotIn("no_demand_2h", self.diag()["excluded"])

    def test_window_cap_never_increases_and_no_duplicate_after_restart(self):
        sku = self.item()
        self.inquiry(sku, 4)
        first = self.plan()
        cap, reserved = self.state()["window_cap"], self.state()["reserved"]
        self.hub = json.loads(json.dumps(self.hub))
        self.inv["balance"] *= 10
        self.assertEqual(self.plan()["buy"], [])
        self.assertEqual(self.state()["window_cap"], cap)
        self.assertEqual(self.state()["reserved"], reserved)
        self.assertGreater(self.cost(first), 0)

    def test_food_price_and_shelf_safety_hot_exception(self):
        for price, shelf, hot, expected in [(9999, 400, False, False), (10000, 180, False, True),
                (120000, 180, False, True), (120001, 180, False, False), (20000, 179, False, False),
                (9999, 12, True, True), (120001, 12, True, True)]:
            with self.subTest(price=price, shelf=shelf, hot=hot):
                self.hub = store.default_hub()
                self.catalog = {}
                sku = self.item(price=price, kind="food", shelf=shelf)
                self.inquiry(sku, 4 if hot else 2)
                self.assertEqual(bool(self.plan()["buy"]), expected)

    def test_window_endstop_and_single_line_diagnostic(self):
        sku = self.item()
        self.inquiry(sku, 4)
        self.inv.pop("restock")
        self.assertEqual(self.plan(), {"buy": []})
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)
        self.assertEqual(self.diag()["stop"], "outside_window")
        self.inv["restock"] = {"opens_at": NOW-10, "closes_at": NOW+100}
        with patch.object(restock, "ROUND_END_TS", NOW+36*3600-1):
            self.assertEqual(self.plan(), {"buy": []})
            self.assertEqual(self.diag()["stop"], "round_end")

    def test_bounded_inquiry_storage_and_canonical_sku(self):
        path = self.path / "inquiries.json"
        for i in range(105):
            store.record_inbound_inquiry("category", True, path, sku="G12345678")
        state = store.load_hub(path)
        self.assertEqual(len(state["demand_radar"]["sku_inquiries"]["g12345678"]), 100)
        self.assertFalse(state["demand_radar"]["sku_inquiries"]["g12345678"][0]["miss"])
        # Seed a large old state, then one write enforces global bounds.
        state["demand_radar"]["sku_inquiries"] = {f"g{i:08x}": [{"ts": NOW, "miss": True}]*20 for i in range(600)}
        store.save_hub(state, path)
        store.record_inbound_inquiry("gffffffff", False, path, raw_text="gffffffff gffffffff")
        events = store.load_hub(path)["demand_radar"]["sku_inquiries"]
        self.assertLessEqual(len(events), 500)
        self.assertLessEqual(sum(map(len, events.values())), 5000)

    def test_quote_records_only_actual_offered_sku_not_requested_substitute(self):
        path = self.path / "quote.json"
        store.record_inbound_inquiry("G87654321", True, path, raw_text="G87654321", sku="G12345678")
        inquiries = store.load_hub(path)["demand_radar"]["sku_inquiries"]
        self.assertEqual(set(inquiries), {"g12345678"})
        self.assertEqual(inquiries["g12345678"], [{"ts": NOW, "miss": False}])

    def test_build_early_stops_emit_one_json_diagnostic_without_network(self):
        sku = self.item()
        with patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", return_value=self.hub), \
             patch.object(restock, "fetch_ledger_rows", side_effect=AssertionError("fetch forbidden")):
            self.inv.update(balance=100, bankrupt_line=500_000, bankruptcies_left=1)
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": [], "bankrupt": True})
            self.assertEqual(self.diag()["stop"], "bankruptcy")
            self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)
            self.catalog.clear()
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": []})
            self.assertEqual(self.diag()["stop"], "missing_input")

    def test_long_lived_last_sale_from_old_cursor_survives_short_tail(self):
        sku = self.item()
        self.stock(sku, age=14_400)
        self.inquiry(sku, 4)
        self.hub["own_shop_urls"] = ["https://own.invalid"]
        self.hub["ledger_seq"] = 100
        row = {"seq": 1, "ts": NOW-10_000, "kind": "settle", "data": {
            "shop": "https://own.invalid", "buyer": "other", "item": sku, "amount": 19_000}}
        self.plan([row])
        self.assertNotIn(sku, self.hub["no_rebuy"])
        self.assertEqual(self.hub["own_last_sale_ts"][sku], NOW-10_000)


if __name__ == "__main__":
    unittest.main()
