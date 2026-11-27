"""Offline restock regressions. All writes use a unique temporary directory."""
import contextlib
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import restock
import store


NOW = 1_790_000_000
URL = "https://own.invalid"


class RestockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="restock-offline-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.hub = store.default_hub()
        self.catalog = {
            sku: {"kind": "goods" if i > 1 else "food", "category": f"cat{i}",
                  "list_price": 20_001, "shelf_hours": 400}
            for i, sku in enumerate(("G97cd0437", "Gef4d3036", "G0be9ed07", "Ge62449c9", "G07ec17d5"))
        }
        self.catalog["G07ec17d5"]["list_price"] = 279_000
        self.inv = {"balance": 5_000_000, "units": [],
                    "restock": {"open": True, "opens_at": NOW - 20, "closes_at": NOW + 160}}
        self.enter = contextlib.ExitStack()
        self.addCleanup(self.enter.close)
        self.enter.enter_context(patch.object(restock.time, "time", return_value=NOW))
        self.enter.enter_context(patch.object(restock, "ROUND_END_TS", NOW + 10_000_000))
        self.enter.enter_context(patch.object(restock, "queue_forum_action"))
        # A missed mock must never contact the market.
        self.enter.enter_context(patch.object(store.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")))

    def stock(self, sku, n):
        self.inv["units"] += [{"sku": sku, "category": self.catalog[sku]["category"],
                               "grade": "A", "cost": 10_000} for _ in range(n)]

    def plan(self, rows=()):
        return restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, list(rows))

    def own_sales(self, by_sku):
        self.hub["own_sale_events"] = {
            str(i): {"sku": sku, "ts": ts}
            for i, (sku, ts) in enumerate((sku, ts) for sku, times in by_sku.items() for ts in times)}

    def cost(self, plan):
        return sum(math.ceil(self.catalog[b["sku"]]["list_price"] * .75) * b["qty"]
                    for b in plan["buy"])

    def seed_catalog(self):
        self.catalog["seed"] = {"kind": "food", "category": "seed", "list_price": 10_000,
                                 "shelf_hours": 400}
        self.hub["demand_radar"].setdefault("sku_inquiries", {})["seed"] = [{"ts": NOW, "miss": True}]

    def settle(self, seq, shop=URL, buyer="other", sku="G97cd0437", ts=NOW):
        return {"seq": seq, "ts": ts, "kind": "settle",
                "data": {"shop": shop, "buyer": buyer, "item": sku, "amount": 1000}}

    def test_own_identity_join_other_shop_and_own_buyer(self):
        rows = [{"seq": 1, "kind": "join", "data": {"role": "shop", "name": store.OWN_NAME, "url": URL}}]
        rows += [self.settle(2), self.settle(3, shop="https://other.invalid"),
                 self.settle(4, buyer=store.OWN_NAME), self.settle(5, ts=NOW-1201)]
        self.plan(rows)
        self.plan(rows)  # stale cursor must not count again
        radar = self.hub["demand_radar"]
        self.assertEqual(len(radar["own_sku_sold_ts"]["G97cd0437"]), 2)
        self.assertEqual(len(radar["sku_sold_ts"]["G97cd0437"]), 3)
        self.assertEqual(sum(0 <= NOW-t <= 1200 for t in radar["own_sku_sold_ts"]["G97cd0437"]), 1)

    def test_unknown_shop_never_own_and_historical_join(self):
        self.plan([self.settle(10)])
        self.assertFalse(self.hub["demand_radar"].get("own_sku_sold_ts"))
        self.plan([{"seq": 1, "kind": "join", "data": {
            "role": "shop", "name": store.OWN_NAME, "url": URL}}, self.settle(11)])
        self.assertEqual(self.hub["own_shop_urls"], [URL])
        self.assertEqual(len(self.hub["demand_radar"]["own_sku_sold_ts"]["G97cd0437"]), 1)

    def test_requested_current_stocks(self):
        for sku, n in zip(self.catalog, [0, 1, 1, 2, 4]):
            self.stock(sku, n)
        buys = {x["sku"]: x["qty"] for x in self.plan()["buy"]}
        # Whitelist alone is not sales evidence; stocked SKUs need own velocity.
        self.assertEqual([buys.get(s, 0) for s in self.catalog], [0, 0, 0, 0, 0])

    def test_sufficient_food_no_expansion_from_market(self):
        for i, name in enumerate(["kimchi", "water", "dumpling", "snack"]):
            self.catalog[name] = {"kind": "food", "category": name, "list_price": 10_000, "shelf_hours": 400}
            self.stock(name, 8)
        rows = [self.settle(i+1, shop="https://other.invalid", sku="water") for i in range(100)]
        self.assertEqual(self.plan(rows)["buy"], [])

    def test_own_turnover_caps_and_short_shelf(self):
        for sku, kind, qty in [("food", "food", 1), ("goods", "goods", 1), ("short", "food", 1)]:
            self.catalog[sku] = {"kind": kind, "category": sku, "list_price": 20_000,
                                 "shelf_hours": 12 if sku == "short" else 400}
            self.stock(sku, qty)
        self.own_sales({"food": [NOW]*30, "goods": [NOW]*30, "short": [NOW-1201]*30})
        self.hub["demand_radar"]["sku_inquiries"] = {
            s: [{"ts": NOW, "miss": False}]*8 for s in ("food", "goods")}
        buys = {b["sku"]: b["qty"] for b in self.plan()["buy"]}
        # Own velocity now targets at most eight; short-shelf still receives no top-up.
        self.assertEqual(buys, {"food": 7, "goods": 7})

    def test_high_ticket_budget_and_own_new_sku_restoration(self):
        self.stock("G97cd0437", 2)
        self.own_sales({"G07ec17d5": [NOW]*50, "new": [NOW]*50})
        self.catalog["new"] = {"kind": "goods", "category": "new", "list_price": 20_000}
        self.hub["demand_radar"]["sku_inquiries"] = {
            s.lower(): [{"ts": NOW, "miss": True}]*8 for s in ("G07ec17d5", "new")}
        plan = self.plan()
        buys = {b["sku"]: b["qty"] for b in plan["buy"]}
        # Hot targets replace own-sales velocity and fit the growth-phase equity cap.
        self.assertEqual(buys.get("G07ec17d5"), 8)
        self.assertEqual(buys.get("new"), 8)
        window = next(iter(self.hub["restock_windows"].values()))
        self.assertLessEqual(self.cost(plan), window["window_cap"])
        equity = self.inv["balance"] + sum(u["cost"] for u in self.inv["units"])
        self.assertLessEqual(window["window_cap"], restock.WINDOW_EQUITY_CAP * equity)

    def test_repeat_restart_and_next_window(self):
        self.seed_catalog()
        path = self.path / "hub.json"
        total = 0
        for _ in range(15):
            total += self.cost(self.plan())
            store.save_hub(self.hub, path)
            self.hub = store.load_hub(path)
        self.assertLessEqual(total, next(iter(self.hub["restock_windows"].values()))["window_cap"])
        self.assertTrue(total > 0)
        self.inv["restock"] = {"open": True, "opens_at": NOW-10, "closes_at": NOW+170}
        self.assertTrue(self.plan()["buy"])

    def test_midwindow_supply_backfill_and_dedup(self):
        self.hub["ledger_seq"] = 100
        rows = [{"seq": 2, "ts": NOW-5, "kind": "supply",
                 "data": {"shop": store.OWN_NAME, "total": 210_000, "qty": 10}}]
        self.seed_catalog()
        first = self.plan(rows)
        self.assertTrue(first["buy"])
        self.assertEqual(self.plan(rows)["buy"], [])
        self.assertEqual(next(iter(self.hub["restock_windows"].values()))["baseline_actual"], 210_000)
        self.assertEqual(len(self.hub["own_supplies"]), 1)

    def test_bootstrap_blocks_unknown_prior_spend_persistently_then_next_window(self):
        self.seed_catalog()
        path = self.path / "hub.json"
        store.save_hub(self.hub, path)
        # The missing prior supply may already exceed 200k; a latest-400 fetch
        # returning no supply is not evidence of a zero-spend baseline.
        with patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", side_effect=lambda: store.load_hub(path)), \
             patch.object(restock, "mutate_hub", side_effect=lambda: store.mutate_hub(path)), \
             patch.object(restock, "fetch_own_shop_urls", return_value=[URL]), \
             patch.object(restock, "fetch_ledger_rows", return_value=[]):
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": []})
            first_window = str(restock.supply_window(self.inv))
            state = store.load_hub(path)
            self.assertTrue(state["restock_windows"][first_window]["blocked"])
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": []})
            with patch.object(restock.time, "time", return_value=NOW+1200):
                self.inv["restock"] = {"open": True, "opens_at": NOW+1180, "closes_at": NOW+1360}
                total = sum(self.cost(restock.build_restock_plan(self.path)) for _ in range(15))
                self.assertGreater(total, 0)
                self.assertLessEqual(total, store.load_hub(path)["restock_windows"][str(restock.supply_window(self.inv))]["window_cap"])
            self.assertTrue(store.load_hub(path)["restock_windows"][first_window]["blocked"])

    def test_old_cursor_sales_backfilled_by_seq_without_market_duplicates(self):
        rows = [self.settle(10), self.settle(11), self.settle(12, buyer=store.OWN_NAME),
                self.settle(13, shop="https://other.invalid")]
        store.apply_ledger_rows(self.hub, rows, self.catalog)
        self.assertFalse(self.hub["demand_radar"]["own_sku_sold_ts"])
        market_before = dict(self.hub["demand_radar"]["market_settles"])
        # Identity becomes known only after the shared cursor passed these sales.
        self.hub["own_shop_urls"] = [URL]
        rows += [self.settle(1, ts=NOW-1801)]
        store.apply_ledger_rows(self.hub, rows+rows, self.catalog)
        self.assertEqual(self.hub["demand_radar"]["own_sku_sold_ts"]["G97cd0437"], [NOW, NOW])
        self.assertEqual(set(self.hub["own_sale_events"]), {"10", "11"})
        store.apply_ledger_rows(self.hub, rows, self.catalog)
        self.assertEqual(len(self.hub["own_sale_events"]), 2)
        self.assertEqual(self.hub["demand_radar"]["market_settles"], market_before)
        self.assertEqual(self.hub["ledger_seq"], 13)
        with patch.object(store.time, "time", return_value=NOW+1801):
            store.apply_ledger_rows(self.hub, [], self.catalog)
        self.assertEqual(self.hub["own_sale_events"], {})
        self.assertEqual(self.hub["demand_radar"]["own_sku_sold_ts"], {})

    def test_own_sale_seq_storage_bounded(self):
        self.hub["own_shop_urls"] = [URL]
        store.apply_ledger_rows(self.hub, [self.settle(i) for i in range(1, 2006)], self.catalog)
        self.assertEqual(len(self.hub["own_sale_events"]), 2000)
        self.assertEqual(len(self.hub["demand_radar"]["own_sku_sold_ts"]["G97cd0437"]), 2000)

    def test_success_does_not_double_charge_reservation(self):
        self.seed_catalog()
        first = self.cost(self.plan())
        row = {"seq": 1, "ts": NOW, "kind": "supply",
               "data": {"shop": store.OWN_NAME, "total": first, "qty": 1}}
        second = self.cost(self.plan([row]))
        self.assertLessEqual(first + second, next(iter(self.hub["restock_windows"].values()))["window_cap"])
        self.assertEqual(next(iter(self.hub["restock_windows"].values()))["reserved"], first+second)

    def test_closed_observes_and_missing_window_fails_closed(self):
        self.hub["own_shop_urls"] = [URL]
        self.inv["restock"]["opens_at"] = NOW + 600
        self.inv["restock"]["closes_at"] = NOW + 780
        self.assertEqual(self.plan([self.settle(1)])["buy"], [])
        self.assertTrue(self.hub["demand_radar"]["own_sku_sold_ts"])
        del self.inv["restock"]
        self.assertEqual(self.plan()["buy"], [])

    def test_cold_start_and_cash_floor(self):
        self.seed_catalog()
        plan = self.plan()
        self.assertTrue(plan["buy"])
        self.assertLessEqual(self.cost(plan), next(iter(self.hub["restock_windows"].values()))["window_cap"])
        self.assertTrue(all(1 <= b["qty"] <= 20 for b in plan["buy"]))
        self.inv["balance"] = 600_000
        self.assertEqual(self.plan()["buy"], [])

    def test_build_bootstrap_outside_lock_and_bankruptcy_endstop(self):
        locked = []
        @contextlib.contextmanager
        def mutate():
            locked.append(True)
            try:
                yield self.hub
            finally:
                locked.pop()
        def urls():
            self.assertFalse(locked)
            return [URL, "https://old.invalid"]
        def rows(*args):
            self.assertFalse(locked)
            return [self.settle(1)]
        with patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", return_value=self.hub), \
             patch.object(restock, "mutate_hub", mutate), \
             patch.object(restock, "fetch_own_shop_urls", side_effect=urls), \
             patch.object(restock, "fetch_ledger_rows", side_effect=rows):
            restock.build_restock_plan(self.path)
            self.assertEqual(self.hub["own_shop_urls"], sorted([URL, "https://old.invalid"]))
            self.assertTrue(self.hub["demand_radar"]["own_sku_sold_ts"])
            with patch.object(restock, "ROUND_END_TS", NOW):
                self.assertEqual(restock.build_restock_plan(self.path), {"buy": []})
            self.inv.update(balance=100, bankrupt_line=500_000, bankruptcies_left=1)
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": [], "bankrupt": True})

    def test_cash_rich_unlocks_high_value_revenue_leaders(self):
        self.inv["balance"] = 6_000_000
        self.catalog["G1515d951"] = {
            "kind": "goods", "category": "mattress", "list_price": 423_810
        }
        self.stock("G97cd0437", 1)
        # Market 21+ qualifies as popular; public prices must also cover costs/storage.
        rows = [self.settle(i+1, shop="https://other.invalid", sku="G1515d951") for i in range(21)]
        for row in rows:
            row["data"]["amount"] = 400_000
        plan = self.plan(rows)
        bought_skus = [b["sku"] for b in plan["buy"]]
        self.assertIn("G1515d951", bought_skus)
        self.assertLessEqual(self.cost(plan), next(iter(self.hub["restock_windows"].values()))["window_cap"])


if __name__ == "__main__":
    unittest.main()
