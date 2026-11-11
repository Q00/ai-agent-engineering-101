"""Offline checks for R3 contract boundaries; no model, keys, or market calls."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer
import market_buyer
import seller_r3


class R3Contract(unittest.TestCase):
    def test_condition_is_disclosed_before_final_price(self):
        notice, details = seller_r3.condition_notice({"unit": "u1", "sku": "G00001", "grade": "B",
                                                    "expires_at": 2000000000, "supplied_at": 0, "cert": {"x": 1}})
        self.assertIn("Expires at", notice)
        self.assertEqual(details["grade_observed"], "B")
        self.assertTrue(details["certificate_available"])
        self.assertIsNone(details["discount_reason"])
    def test_questions_and_conditional_messages_are_not_purchase_offers(self):
        self.assertIsNone(seller_r3.purchase_offer("My budget is 100; is a warranty included?", "G00001", 90))
        self.assertIsNone(seller_r3.purchase_offer("I will pay 90 if you add a warranty", "G00001", 90))
        self.assertEqual(seller_r3.purchase_offer("90", "G00001", 90), 90)
        self.assertEqual(seller_r3.purchase_offer("I accept the quoted price", "G00001", 90), 90)
    def test_buyer_accepts_sku_but_still_rejects_over_budget_offer(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(buyer, "ROOT", Path(tmp)):
            with buyer.BuyerTransport("https://example.test", "G00001", 100) as client:
                client.task = {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}
                with self.assertRaises(buyer.BuyerError):
                    client.offer(101)

    def test_inventory_selection_excludes_expired_and_reserved_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit=str(i), sku="G00001", name="food", category="food",
                          grade=grade, expires_at=expiry, supplied_at=0)
                     for i, grade, expiry in ((1, "expired", 1), (2, "A", 20), (3, "B", 10))]
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root):
                self.assertEqual(seller_r3.find_unit("G00001")["unit"], "3")
                self.assertEqual(seller_r3.find_unit("G00001", {"3"})["unit"], "2")
                self.assertIsNone(seller_r3.find_unit("G00002"))

    # The stated shelf-life need, the user's 11-11 choice ("맞는 재고 골라 팔기 + 기록 (권장)").
    def test_a_stated_shelf_life_need_picks_a_unit_that_meets_it(self):
        now = 1794390000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit=str(i), sku="G00001", name="food", category="food", grade=grade, expires_at=now + hours * 3600, supplied_at=0)
                     for i, grade, hours in ((3, "C", 10), (2, "A", 400), (4, "B", 100))]
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root), patch.object(seller_r3.time, "time", return_value=now):
                self.assertEqual(seller_r3.find_unit("G00001")["unit"], "3")  # no stated need: the first to expire, as before
                self.assertEqual(seller_r3.find_unit("G00001 please. This order needs at least 362 hours of shelf life left.")["unit"], "2")
                self.assertEqual(seller_r3.find_unit("G00001, needs at least 50 hours of shelf life left")["unit"], "4")
                self.assertEqual(seller_r3.find_unit("G00001, needs at least 50 hours of shelf life left", {"4"})["unit"], "2")
                # No unit meets it: picked as before, never refused.
                self.assertEqual(seller_r3.find_unit("G00001. This order needs at least 900 hours of shelf life left.")["unit"], "3")

    def test_a_unit_without_expiry_meets_any_stated_need(self):
        now = 1794390000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit="g", sku="G00009", name="lamp", category="home", grade="A", expires_at=None, supplied_at=0, kind="goods")]
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root), patch.object(seller_r3.time, "time", return_value=now):
                self.assertEqual(seller_r3.find_unit("G00009, needs at least 500 hours of shelf life left")["unit"], "g")

    def test_stated_need_hours_reads_only_shelf_life_hours(self):
        need = seller_r3.stated_need_hours
        self.assertEqual(need("This order accepts grade A or B and needs at least 362 hours of shelf life left."), 362)
        self.assertEqual(need("I need 180 h of shelf life"), 180)
        self.assertEqual(need("at least 74.5 hours remaining please"), 74.5)
        self.assertEqual(need('{"min_hours_left": 542}'), 542)
        self.assertEqual(need("유통기한 360시간 이상 남은 것"), 360)
        self.assertEqual(need("72 hours of shelf life, or better 126 hours left"), 126)
        for none in ("", None, "My budget is 30000", "deliver within 3 hours", "G00001 x 2", "0 hours of shelf life"):
            self.assertIsNone(need(none), none)

    def test_balanced_food_order_retains_stock_filter_and_time_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shops = [{"name": "shop", "url": "https://example.test", "reviews": [],
                      "offers": [{"sku": "bad", "qty": 9}, {"sku": "G00001", "qty": 1}]}]
            (root / "shops.json").write_text(json.dumps(shops), encoding="utf-8")
            (root / "catalog.json").write_text(json.dumps({"G00001": {"list_price": 100}}), encoding="utf-8")
            order = {"order": "o1", "item": "food", "budget": 100, "kind": "food",
                     "acceptable": ["G00001"], "use_within_hours": 3}
            (root / "order.json").write_text(json.dumps(order), encoding="utf-8")
            args = ["market_buyer", "--shops", str(root / "shops.json"), "--order", str(root / "order.json"),
                    "--memory", str(root / "memory.jsonl"), "--item", "food", "--budget", "100"]
            output = io.StringIO()
            # The grade C food trial has its own tests (test_food_c_trial); pin its control arm so this check does not follow the live share.
            with patch("sys.argv", args), patch.object(market_buyer, "ROOT", root), redirect_stdout(output), patch("food_c_trial.arm", return_value="control"), \
                    patch.object(market_buyer, "trade", return_value=({"shop": "https://example.test", "receipt": None},
                                                                     {"outcome": "no_deal"})) as trade:
                market_buyer.main()
            self.assertEqual(trade.call_args.args[1], "G00001")
            result = json.loads(output.getvalue().splitlines()[-1])
            self.assertIsNone(result["receipt"])
            self.assertEqual(result["accept"], {"cert": True, "grades": ["A", "B"], "min_hours_left": 5})
            from r3_policy import buyer_accept
            self.assertEqual(buyer_accept(order), {"cert": True, "grades": ["A", "B"], "min_hours_left": 5})


if __name__ == "__main__":
    unittest.main()
