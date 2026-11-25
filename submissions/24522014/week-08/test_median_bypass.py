"""Proposal 2 (approved 11-08): a SKU with 3+ of our completed sales in 24 h and a positive Book sum skips the market median test.
Every other SKU, the limits, surge, budget and the .75 x (1 + storage) line stay as they were. Off when the setting is missing or broken."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_state import task_file
import restock_policy as rp

try:
    import median_bypass as mb
except ImportError:  # the tests are written before the module
    mb = None

RUNTIME = {"version": 1, "controls": {"restock_paused": False, "restock_budget_rate": 0.05}}
LIMITS = {"revision": "t", "max_total_units": 40, "max_proven_units_per_sku": 3, "max_units_per_run": 6, "max_unproven_units": 2,
          "min_profitable_own_sales_6h": 3, "min_public_sales_6h": 6, "min_public_buyers": 3}
# Worst cost 75 and the line 75.75: a median of 74 fails the test today.
LOW = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 74}
NOW = 1793800000.0


def product(category):
    return {"category": category, "kind": "goods", "list_price": 100}


def inventory(surge=None):
    return {"round": 3, "balance": 100000000, "start": 5000000, "storage_per_day": .01, "restock": {"open": True}, "units": [], "surge": surge or []}


class Plan(unittest.TestCase):
    catalog = {"a": product("c1"), "b": product("c2")}

    def plan(self, inv, **kw):
        with patch.object(rp, "capacity_config", return_value=LIMITS):
            return rp.plan(inv, self.catalog, [{"category": "c1"}, {"category": "c2"}], {}, 0,
                           intelligence={k: LOW for k in self.catalog}, runtime=RUNTIME, now=NOW, **kw)

    def test_today_both_skus_fail_the_median_test(self):
        self.assertEqual(self.plan(inventory())[0], [])
        self.assertEqual(self.plan(inventory(), median_bypass=set())[0], [])

    def test_a_confirmed_sku_passes_and_the_other_still_fails(self):
        buys, why = self.plan(inventory(), median_bypass={"a"})
        self.assertEqual(buys, [{"sku": "a", "qty": 3}])
        self.assertEqual(why["median_bypass_used"], ["a"])

    def test_a_confirmed_sku_still_meets_every_other_limit(self):
        buys, _ = self.plan(dict(inventory(), units=[{"sku": "a", "cost": 75, "grade": "A", "kind": "goods", "category": "c1", "supplied_at": NOW}] * 3),
                            median_bypass={"a"})
        self.assertEqual(buys, [])  # three per SKU, unchanged
        with patch.object(rp, "capacity_config", return_value=dict(LIMITS, max_units_per_run=2)):
            buys, _ = rp.plan(inventory(), self.catalog, [{"category": "c1"}], {}, 0, intelligence={"a": LOW}, runtime=RUNTIME, now=NOW, median_bypass={"a"})
        self.assertEqual(buys, [{"sku": "a", "qty": 2}])

    def test_a_surged_sku_keeps_the_median_test(self):
        # Our 24 h Book was made at the plain cost; a surged unit costs more, so the market must still clear the surged cost.
        buys, why = self.plan(inventory([{"sku": "a", "factor": 1.2}]), median_bypass={"a"})
        self.assertEqual(buys, [])
        self.assertEqual(why["median_bypass_used"], [])

    def test_a_sku_that_passes_anyway_is_not_counted_as_bypassed(self):
        with patch.object(rp, "capacity_config", return_value=LIMITS):
            _, why = rp.plan(inventory(), self.catalog, [{"category": "c1"}], {}, 0, intelligence={"a": dict(LOW, median_price=90)},
                             runtime=RUNTIME, now=NOW, median_bypass={"a"})
        self.assertEqual(why["median_bypass_used"], [])


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.md = Path(self.tmp.name)
        (self.md / "signed-offers").mkdir()
        (self.md / "checkout-events").mkdir()
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def sale(self, sku, price, cost, status="completed", at=NOW - 3600, **extra):
        self.n += 1
        task = f"t{self.n}"
        issued = (NOW - 3600 if at is None else at) - 60
        offer = dict({"task": task, "unit": f"u{self.n}", "sku": sku, "price": price, "cost": cost, "issued_at": issued, "expires_at": issued + 600}, **extra)
        task_file(self.md, "signed-offers", task).write_text(json.dumps(offer), encoding="utf8")
        if status is not None:
            task_file(self.md, "checkout-events", task).write_text(json.dumps({"task": task, "status": status, "at": at}), encoding="utf8")

    def test_three_completed_sales_with_a_positive_sum_pass(self):
        for price, cost in ((80, 75), (80, 75), (70, 75)):
            self.sale("a", price, cost)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), {"a"})

    def test_two_sales_or_a_negative_sum_do_not(self):
        for _ in range(2):
            self.sale("two", 90, 75)
        for price in (76, 76, 70):
            self.sale("neg", price, 75)
        self.sale("zero", 75, 75), self.sale("zero", 75, 75), self.sale("zero", 75, 75)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), set())

    def test_only_completed_sales_in_the_last_24_hours_count(self):
        self.sale("a", 90, 75), self.sale("a", 90, 75)
        for status in ("processing", "canceled", "refused", "uncertain", None):
            self.sale("a", 90, 75, status=status)
        self.sale("a", 90, 75, at=NOW - 86401)
        self.sale("a", 90, 75, at=NOW + 600)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), set())
        self.sale("a", 90, 75, at=NOW - 86399)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), {"a"})

    def test_a_broken_record_sends_its_sku_back_to_todays_test(self):
        for _ in range(3):
            self.sale("a", 90, 75)
            self.sale("b", 90, 75)
        for bad in ({"cost": None}, {"cost": "75"}, {"price": float("nan")}, {"cost": 0}, {"price": True}):
            with self.subTest(bad=bad):
                sku = "a"
                self.sale(sku, **dict({"price": 90, "cost": 75}, **bad))
                self.assertEqual(mb.confirmed_skus(self.md, NOW), {"b"})
                for p in self.md.glob("*/*.json"):
                    p.unlink()
                for _ in range(3):
                    self.sale("a", 90, 75)
                    self.sale("b", 90, 75)
        # A completed event with no time or a file that does not parse leaves the SKU it names, or every SKU, on today's test.
        self.sale("a", 90, 75, at=None)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), {"b"})
        (self.md / "signed-offers" / "broken.json").write_text("{", encoding="utf8")
        self.assertEqual(mb.confirmed_skus(self.md, NOW), set())

    def test_an_offer_that_expired_before_the_window_is_not_read_as_evidence(self):
        for _ in range(3):
            self.sale("a", 90, 75)
        self.sale("a", None, None, at=None, issued_at=NOW - 90000, expires_at=NOW - 89400)
        self.assertEqual(mb.confirmed_skus(self.md, NOW), {"a"})

    def test_no_records_mean_no_bypass(self):
        self.assertEqual(mb.confirmed_skus(self.md, NOW), set())
        self.assertEqual(mb.confirmed_skus(self.md / "missing", NOW), set())


class Switch(unittest.TestCase):
    def test_off_unless_the_setting_says_true(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "median_bypass.json"
            self.assertFalse(mb.enabled(path))
            for text, expected in (("{", False), ("[]", False), ('{"enabled": "true"}', False), ('{"enabled": 1}', False),
                                   ('{"enabled": false}', False), ('{"enabled": true}', True)):
                path.write_text(text, encoding="utf8")
                self.assertIs(mb.enabled(path), expected, text)

    def test_for_run_is_empty_when_off_or_on_any_error(self):
        with TemporaryDirectory() as root:
            md = Path(root)
            with patch.object(mb, "enabled", return_value=False), patch.object(mb, "confirmed_skus", return_value={"a"}):
                self.assertEqual(mb.for_run(md, NOW), set())
            with patch.object(mb, "enabled", return_value=True), patch.object(mb, "confirmed_skus", return_value={"a"}):
                self.assertEqual(mb.for_run(md, NOW), {"a"})
            with patch.object(mb, "enabled", return_value=True), patch.object(mb, "confirmed_skus", side_effect=OSError("x")):
                self.assertEqual(mb.for_run(md, NOW), set())

    def test_the_shipped_setting_parses_and_holds_a_boolean(self):
        value = json.loads(mb.CONFIG.read_text(encoding="utf-8"))
        self.assertIsInstance(value["enabled"], bool)


class Wiring(unittest.TestCase):
    def test_the_hook_passes_the_bypass_to_the_plan(self):
        source = Path(rp.__file__).read_text(encoding="utf-8")
        self.assertTrue("median_bypass.for_run(" in source and "median_bypass=bypass" in source)


if __name__ == "__main__":
    unittest.main()
