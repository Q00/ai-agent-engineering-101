import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import price_cap
import r3_policy
import sale_order
import seller_r3
from market_state import read, task_file, write


class Shipped(unittest.TestCase):
    def test_the_value_is_the_approved_one(self):
        value = json.loads(price_cap.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual((value["ratio"], value["max_list_price"]), (.75, 1000000))
        self.assertIs(type(value["enabled"]), bool)


class Settings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "cap.json"

    def write(self, value):
        self.path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")

    def test_only_a_valid_switched_on_setting_counts(self):
        self.assertIsNone(price_cap.settings(self.path))
        for bad in ("broken", [], {"enabled": False, "ratio": .79}, {"enabled": "true", "ratio": .79}, {"enabled": 1, "ratio": .79}, {"enabled": True}, {"enabled": True, "ratio": True},
                    {"enabled": True, "ratio": .4}, {"enabled": True, "ratio": 1.1}, {"enabled": True, "ratio": .79, "max_list_price": 0}, {"enabled": True, "ratio": .79, "max_list_price": "x"}):
            self.write(bad)
            self.assertIsNone(price_cap.settings(self.path), bad)
        self.write({"enabled": True, "ratio": .79, "max_list_price": 1000000})
        self.assertEqual(price_cap.settings(self.path), {"ratio": .79, "max_list_price": 1000000})

    def test_a_changed_setting_is_read_on_the_next_deal(self):
        unit = {"unit": "u", "list_price": 1000}
        self.write({"enabled": True, "ratio": .79})
        self.assertEqual(price_cap.capped({"quote": 1000, "floor": 500}, unit, self.path)["quote"], 790)
        self.write({"enabled": True, "ratio": .85})
        self.assertEqual(price_cap.capped({"quote": 1000, "floor": 500}, unit, self.path)["quote"], 850)
        self.write({"enabled": False, "ratio": .79})
        self.assertEqual(price_cap.capped({"quote": 1000, "floor": 500}, unit, self.path), {"quote": 1000, "floor": 500})


class Quote(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "cap.json"
        self.path.write_text(json.dumps({"enabled": True, "ratio": .79, "max_list_price": 1000000}), encoding="utf-8")

    def cap(self, quote, floor, listed=1000, unit="u"):
        return price_cap.capped({"quote": quote, "floor": floor}, {"unit": unit, "list_price": listed}, self.path)

    def test_above_the_ceiling_comes_down_to_it(self):
        value = self.cap(1000, 500)
        self.assertEqual((value["quote"], value["floor"], value["price_cap"]["state"], value["price_cap"]["before"], value["price_cap"]["limit"]), (790, 500, "capped", 1000, 790))

    def test_the_ceiling_rounds_down_so_the_ratio_is_never_passed(self):
        value = self.cap(1000, 1, listed=999)
        self.assertEqual(value["quote"], 789)
        self.assertLessEqual(value["quote"]/999, .79)

    def test_at_or_under_the_ceiling_is_untouched(self):
        for quote in (790, 700):
            value = self.cap(quote, 500)
            self.assertEqual((value["quote"], value["price_cap"]["state"]), (quote, "under"))

    def test_the_floor_wins_and_is_counted(self):
        value = self.cap(1000, 900)
        self.assertEqual((value["quote"], value["floor"], value["price_cap"]["state"]), (900, 900, "floor_bound"))

    def test_the_quote_is_never_raised_to_the_floor_by_the_cap(self):
        self.assertEqual(self.cap(800, 900)["quote"], 800)

    def test_list_price_at_the_top_or_more_is_left_alone(self):
        for listed in (1000000, 2000000):
            value = self.cap(listed, 1, listed=listed)
            self.assertEqual((value["quote"], value["price_cap"]["state"]), (listed, "above_max_list_price"))
        self.assertEqual(self.cap(999999, 1, listed=999999)["price_cap"]["state"], "capped")

    def test_no_top_caps_every_price(self):
        self.path.write_text(json.dumps({"enabled": True, "ratio": .79}), encoding="utf-8")
        self.assertEqual(self.cap(2000000, 1, listed=2000000)["quote"], 1580000)

    def test_a_unit_without_a_usable_list_price_is_left_alone(self):
        for unit in ({"unit": "u"}, {"unit": "u", "list_price": 0}, {"unit": "u", "list_price": "x"}, {"unit": None, "list_price": 1000}):
            self.assertEqual(price_cap.capped({"quote": 1000, "floor": 1}, unit, self.path), {"quote": 1000, "floor": 1})


class WithRealTerms(unittest.TestCase):
    """The real selling_terms with a multiplier under the cap: the floor, which covers cost, storage and margin, is never crossed."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "cap.json"
        self.path.write_text(json.dumps({"enabled": True, "ratio": .79, "max_list_price": 1000000}), encoding="utf-8")
        self.now = 1793900000

    def unit(self, cost, listed, grade="A", age=0, kind="goods"):
        return {"unit": "u", "sku": "G1", "kind": kind, "grade": grade, "cost": cost, "list_price": listed, "supplied_at": self.now-age*3600, "expires_at": None}

    def terms(self, unit, multiplier=1):
        runtime = {"version": 1, "experiment": None, "controls": {"quote_multipliers": {"G1": multiplier}}}
        return price_cap.capped(r3_policy.selling_terms(unit, {}, now=self.now, signals={}, runtime=runtime), unit, self.path)

    def test_a_cheap_unit_gets_the_ceiling(self):
        value = self.terms(self.unit(300, 1000))
        self.assertEqual((value["quote"], value["price_cap"]["state"]), (790, "capped"))

    def test_multiplier_and_cap_together_stay_above_the_floor(self):
        for multiplier in (1, .97, .5):
            for cost, listed in ((300, 1000), (780, 1000), (900, 1000), (990, 1000)):
                value = self.terms(self.unit(cost, listed), multiplier)
                self.assertGreaterEqual(value["quote"], value["floor"])
                self.assertGreaterEqual(value["floor"], cost)

    def test_a_cost_above_the_ceiling_keeps_the_floor_and_is_recorded(self):
        value = self.terms(self.unit(900, 1000))
        self.assertEqual((value["quote"], value["price_cap"]["state"]), (value["floor"], "floor_bound"))
        self.assertEqual(value["floor"], 945)

    def test_clearance_keeps_its_lower_price(self):
        value = self.terms(self.unit(100, 1000, age=200))
        self.assertEqual(value["discount_reason"], "long_stock_loss_clearance")
        self.assertEqual(value["quote"], value["floor"])
        self.assertLess(value["quote"], 790)


class SellerFlow(unittest.IsolatedAsyncioTestCase):
    """The seller end to end with a fake updater: nothing is paid."""

    async def sale(self, cap, offer=None, cost=300, listed=1000):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / ".market"
            config = root / "cap.json"
            config.write_text(json.dumps(cap), encoding="utf-8")
            import time
            now = time.time()
            unit = {"unit": "u", "sku": "G1", "name": "fixture", "category": "g", "grade": "B", "cert": True, "cost": cost, "kind": "goods", "list_price": listed, "supplied_at": now, "expires_at": None}
            write(md / "inventory.json", {"round": 3, "balance": 5000000, "units": [unit]})
            write(md / "catalog.json", {"G1": {"kind": "goods", "list_price": listed}})
            updater = MagicMock()
            updater.requires_input, updater.complete, updater.add_artifact, updater.reject = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
            context = SimpleNamespace(current_task=SimpleNamespace(id="t1", context_id="c"), message=None)
            seller = seller_r3.Seller()
            real = price_cap.capped
            with patch.object(seller_r3, "MARKET_DIR", md), patch.object(seller_r3, "__file__", str(root / "seller_r3.py")), patch.object(seller_r3, "TaskUpdater", return_value=updater), \
                    patch.object(seller_r3, "get_message_text", side_effect=["buy G1"]), patch.object(seller_r3, "decision_mode", return_value="rules"), \
                    patch.object(seller_r3, "capped", side_effect=lambda terms, u, **kw: real(terms, u, config, **kw)), \
                    patch.object(seller_r3, "sign", side_effect=lambda r: {**r, "sig_seller": "fixture"}) as sign:
                await seller.execute(context, MagicMock())
                ask = seller.open["t1"]["ask"]
                with patch.object(seller_r3, "get_message_text", side_effect=[str(ask if offer is None else offer)]):
                    await seller.execute(context, MagicMock())
                receipt = sign.call_args.args[0] if sign.called else None
                return ask, receipt, read(task_file(md, "signed-offers", "t1"), None), seller

    async def test_the_sale_is_at_the_ceiling_and_recorded(self):
        ask, receipt, offer, _ = await self.sale({"enabled": True, "ratio": .79})
        self.assertEqual((ask, receipt["price"], offer["price"], offer["list_price"], offer["price_cap"]["state"]), (790, 790, 790, 1000, "capped"))

    async def test_switched_off_the_quote_is_todays(self):
        ask, receipt, offer, _ = await self.sale({"enabled": False, "ratio": .79})
        self.assertEqual((ask, receipt["price"], offer["price_cap"]), (1000, 1000, None))

    async def test_an_offer_above_the_ceiling_never_buys_at_that_price(self):
        ask, receipt, offer, _ = await self.sale({"enabled": True, "ratio": .79}, offer=950)
        self.assertEqual(ask, 790)
        self.assertIsNone(receipt)
        self.assertIsNone(offer)

    async def test_no_sale_below_the_floor(self):
        ask, receipt, offer, _ = await self.sale({"enabled": True, "ratio": .79}, cost=900)
        self.assertEqual((ask, receipt["price"], offer["price_cap"]["state"]), (ask, ask, "floor_bound"))
        self.assertGreaterEqual(ask, 945)  # cost 900 plus 5%, and a moment of storage


class Order(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        write(self.root / "inventory.json", {"round": 3, "units": [
            {"unit": "c-old", "sku": "G1", "name": "n", "category": "x>y", "grade": "C", "kind": "goods", "expires_at": None, "supplied_at": 1},
            {"unit": "a-new", "sku": "G1", "name": "n", "category": "x>y", "grade": "A", "kind": "goods", "expires_at": None, "supplied_at": 2},
            {"unit": "b-new", "sku": "G1", "name": "n", "category": "x>y", "grade": "B", "kind": "goods", "expires_at": None, "supplied_at": 3},
            {"unit": "c-only", "sku": "G2", "name": "m", "category": "x>z", "grade": "C", "kind": "goods", "expires_at": None, "supplied_at": 4},
            {"unit": "food-c", "sku": "G3", "name": "f", "category": "f>k", "grade": "C", "kind": "food", "expires_at": 100, "supplied_at": 5},
            {"unit": "food-a", "sku": "G3", "name": "f", "category": "f>k", "grade": "A", "kind": "food", "expires_at": 200, "supplied_at": 6}]})
        self.setting = self.root / "order.json"

    def find(self, text, taken=(), setting=None):
        self.setting.write_text(setting if isinstance(setting, str) else json.dumps(setting), encoding="utf-8")
        real = sale_order.grade_c_last
        with patch.object(seller_r3, "MARKET_DIR", self.root), patch.object(seller_r3, "grade_c_last", side_effect=lambda: real(self.setting)):
            return seller_r3.find_unit(text, taken)["unit"]

    def test_on_a_and_b_go_before_an_older_c(self):
        self.assertEqual(self.find("buy G1", setting={"grade_c_last": True}), "a-new")
        self.assertEqual(self.find("buy G1", {"a-new"}, {"grade_c_last": True}), "b-new")

    def test_on_a_c_alone_still_sells(self):
        self.assertEqual(self.find("buy G1", {"a-new", "b-new"}, {"grade_c_last": True}), "c-old")
        self.assertEqual(self.find("buy G2", setting={"grade_c_last": True}), "c-only")

    def test_food_still_goes_by_expiry(self):
        self.assertEqual(self.find("buy G3", setting={"grade_c_last": True}), "food-c")

    def test_off_or_broken_is_oldest_first(self):
        for setting in ({"grade_c_last": False}, {"grade_c_last": 1}, {}, "broken", "[]"):
            self.assertEqual(self.find("buy G1", setting=setting), "c-old", setting)


if __name__ == "__main__":
    unittest.main()
