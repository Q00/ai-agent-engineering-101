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

    # Days and shares of shelf life, and the logged clues, the user's 11-12 choice ("단서 기록 + 일·% 표현 읽기 (권장)").
    def test_stated_need_reads_days_as_hours(self):
        need = seller_r3.stated_need_hours
        self.assertEqual(need("needs at least 15 days of shelf life left"), 360)
        self.assertEqual(need("7.5 days remaining, please"), 180)
        self.assertEqual(need('{"min_days_left": 22.5}'), 540)
        self.assertEqual(need("유통기한 15일 이상"), 360)
        self.assertEqual(need("100 hours of shelf life, or 5 days left"), 120)
        for none in ("deliver within 3 days", "a 30 day return window", "0 days of shelf life", "G0123d456"):
            self.assertIsNone(need(none), none)

    def test_stated_need_percent_reads_only_shares_of_shelf_life(self):
        pct = seller_r3.stated_need_percent
        self.assertEqual(pct("needs at least 50% of its shelf life left"), 50)
        self.assertEqual(pct("75 % shelf life remaining"), 75)
        self.assertEqual(pct("with a shelf life of at least 25%"), 25)
        self.assertEqual(pct("freshness >= 75%"), 75)
        self.assertEqual(pct("유통기한 50% 이상 남은 것"), 50)
        self.assertEqual(pct("25% of shelf life is fine, 50% of shelf life preferred"), 50)
        for none in ("", None, "10% off please", "budget 50%", "150% of shelf life", "shelf life please"):
            self.assertIsNone(pct(none), none)

    def test_a_stated_share_of_shelf_life_picks_a_unit_that_meets_it(self):
        now = 1794390000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit=str(i), sku="G00001", name="food", category="food", grade=grade, expires_at=now + hours * 3600,
                          supplied_at=0, kind="food")
                     for i, grade, hours in ((3, "C", 190), (2, "A", 700), (4, "B", 450))]
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            (root / "catalog.json").write_text(json.dumps({"G00001": {"kind": "food", "shelf_hours": 720}}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root), patch.object(seller_r3.time, "time", return_value=now):
                self.assertEqual(seller_r3.find_unit("G00001")["unit"], "3")
                self.assertEqual(seller_r3.find_unit("G00001, needs 50% of shelf life left")["unit"], "4")   # 360 h
                self.assertEqual(seller_r3.find_unit("G00001, needs 75% of shelf life left")["unit"], "2")   # 540 h
                self.assertEqual(seller_r3.find_unit("G00001, needs 25% of shelf life left")["unit"], "3")   # 180 h
                self.assertEqual(seller_r3.find_unit("G00001, needs 15 days of shelf life")["unit"], "4")    # 360 h
                # Hours and a share both stated: the larger need decides.
                self.assertEqual(seller_r3.find_unit("G00001, 200 hours of shelf life and 75% of shelf life")["unit"], "2")
                # No unit meets it: picked as before, never refused.
                self.assertEqual(seller_r3.find_unit("G00001, needs 100% of shelf life", {"2"})["unit"], "3")
                # Without the SKU's shelf_hours a share sets no need for that unit.
                (root / "catalog.json").write_text(json.dumps({"G00002": {"kind": "food", "shelf_hours": 720}}), encoding="utf-8")
                self.assertEqual(seller_r3.find_unit("G00001, needs 75% of shelf life left")["unit"], "3")

    def test_shelf_hours_are_parsed_again_only_when_the_catalog_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "catalog.json"
            path.write_text(json.dumps({"G00001": {"shelf_hours": 168}, "G00002": {"shelf_hours": None}, "G00003": "x"}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root):
                self.assertEqual(seller_r3.shelf_hours_by_sku(), {"G00001": 168})
                with patch.object(seller_r3.json, "loads", side_effect=AssertionError("parsed again")):
                    self.assertEqual(seller_r3.shelf_hours_by_sku(), {"G00001": 168})
                path.write_text(json.dumps({"G00001": {"shelf_hours": 720}, "G00004": {"shelf_hours": 42}}), encoding="utf-8")
                self.assertEqual(seller_r3.shelf_hours_by_sku(), {"G00001": 720, "G00004": 42})
                path.write_text("{not json", encoding="utf-8")
                self.assertEqual(seller_r3.shelf_hours_by_sku(), {})
            with patch.object(seller_r3, "MARKET_DIR", root / "missing"):
                self.assertEqual(seller_r3.shelf_hours_by_sku(), {})

    def test_shelf_clues_keep_terms_and_unit_numbers_never_text(self):
        clues = seller_r3.shelf_clues("Hi from Some Buyer: G4a998429 x1, budget 30000. Must be fresh, at least 50% of shelf life, "
                                      "or 15 days left; grade A or B.")
        self.assertEqual(clues["terms"], ["shelf life", "fresh", "left", "at least", "grade A", "grade B"])
        self.assertEqual(clues["numbers"], [[50.0, "%"], [15.0, "d"]])
        self.assertNotIn("Buyer", json.dumps(clues))
        self.assertEqual(seller_r3.shelf_clues("유통기한 360시간 이상, 2주"), {"terms": ["유통기한", "이상"], "numbers": [[360.0, "h"], [2.0, "w"]]})
        self.assertEqual(seller_r3.shelf_clues(None), {"terms": [], "numbers": []})
        self.assertEqual(seller_r3.shelf_clues("This order accepts grade A or B. Grades B/C are not ok")["terms"], ["grade A", "grade B", "grade C"])
        self.assertEqual(seller_r3.shelf_clues("grades are good, grade a lot, upgrade Apple")["terms"], [])
        self.assertEqual(len(seller_r3.shelf_clues(" ".join(f"{i}h" for i in range(1, 40)))["numbers"]), 12)

    # Every hours number in a food request, the user's 11-12 choice ("식품 요청의 시간 숫자는 모두 읽기 (권장)").
    def test_food_need_hours_reads_hours_numbers_outside_delivery_and_reply_windows(self):
        need = seller_r3.food_need_hours
        self.assertEqual(need("G4a998429 x1, use within 126.0 h"), 126)
        self.assertEqual(need('{"sku": "G476cc094", "use_within_hours": 540}'), 540)
        self.assertEqual(need("use_within_hours=180.0, budget 30000"), 180)
        self.assertEqual(need("I'll eat it over 360.5 hours"), 360.5)
        self.assertEqual(need("at least 74 h please"), 74)
        self.assertEqual(need("72.5h"), 72.5)
        self.assertEqual(need("126시간 안에 먹을 예정"), 126)
        self.assertEqual(need("deliver by tomorrow and use within 126 h"), 126)
        self.assertEqual(need("Deliver within 3 hours. Use within 540 h."), 540)
        self.assertEqual(need("540 h, delivery to Seoul"), 540)
        for none in ("deliver within 3 hours", "respond within the next 2 hours", "please reply in 2 h", "offer valid for 24 hours",
                     "3시간 이내 배송", "2 hour delivery", "posted 5 hours ago", "budget 30000, G4a998429", "0 h", None, ""):
            self.assertIsNone(need(none), none)

    def test_a_food_request_hours_number_picks_a_food_unit_that_meets_it(self):
        now = 1794390000.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            units = [dict(unit=str(i), sku="G00001", name="food", category="food", grade=grade, expires_at=now + hours * 3600,
                          supplied_at=0, kind="food")
                     for i, grade, hours in ((3, "C", 54), (2, "A", 150), (4, "B", 100))]
            units.append(dict(unit="g", sku="G00009", name="lamp", category="home", grade="B", expires_at=now + 10 * 3600, supplied_at=0,
                              kind="goods"))
            units.append(dict(unit="h", sku="G00009", name="lamp", category="home", grade="A", expires_at=now + 900 * 3600, supplied_at=1,
                              kind="goods"))
            (root / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
            with patch.object(seller_r3, "MARKET_DIR", root), patch.object(seller_r3.time, "time", return_value=now):
                self.assertEqual(seller_r3.find_unit("G00001")["unit"], "3")
                self.assertEqual(seller_r3.find_unit("G00001, use within 126 h")["unit"], "2")
                self.assertEqual(seller_r3.find_unit("G00001 at least 84 h")["unit"], "4")
                self.assertEqual(seller_r3.find_unit("G00001, deliver within 3 hours")["unit"], "3")
                # No unit meets it: picked as before, never refused.
                self.assertEqual(seller_r3.find_unit("G00001, use within 540 h")["unit"], "3")
                # Goods units are not held to a food hours number.
                self.assertEqual(seller_r3.find_unit("G00009, use within 500 h")["unit"], "g")
        unit = dict(unit="x", sku="G00001", kind="food")
        self.assertEqual(seller_r3.unit_need_hours(unit, 100, None, {}, 126), 126)
        self.assertEqual(seller_r3.unit_need_hours(unit, 200, None, {}, 126), 200)
        self.assertEqual(seller_r3.unit_need_hours(dict(unit, kind="goods"), None, None, {}, 126), None)
        self.assertEqual(seller_r3.shelf_clues("use within 126 h, need it")["terms"], ["use within", "within", "need"])

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
