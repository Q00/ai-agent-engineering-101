"""Pure public seam; synthetic values, no market IO."""
import unittest
import buyer_firm_followup as firm
import oneshot

class FirmTests(unittest.TestCase):
    def call(self, **overrides):
        args = dict(meta=None, text=TEXT,
                    history=[("shop asked",120),("you offered",90)],
                    limit=100, arm="B", goods=True, policy_ok=True)
        args.update(overrides)
        return firm.turn(**args)

    def test_verified_followup_uses_existing_code_policy(self):
        self.assertEqual(firm.turn(None, TEXT, [("shop asked",120),("you offered",90)], 100, arm="B", goods=True, policy_ok=True), {"shop_price":120,"action":"offer","price":100})

    def test_default_and_control_are_off(self):
        self.assertIsNone(firm.turn(None, TEXT, [("shop asked",120),("you offered",90)], 100))
        for arm in (None, "A", "b", True):
            with self.subTest(arm=arm):
                self.assertIsNone(self.call(arm=arm))

    def test_metadata_and_unverified_policy_leave_fallback(self):
        for override in ({"meta":120}, {"meta":0}, {"policy_ok":False}, {"policy_ok":1}):
            with self.subTest(override=override):
                self.assertIsNone(self.call(**override))

    def test_first_card_and_incomplete_history_leave_fallback(self):
        for history in ([], None, [("shop asked",120)], [("you offered",90)],
                        [("you offered",90),("shop asked",120)],
                        [("shop asked",120),("you offered",)],
                        [("shop asked",120),("you offered",90),("shop asked",120)]):
            with self.subTest(history=history):
                self.assertIsNone(self.call(history=history))

    def test_invalid_history_numbers_leave_fallback(self):
        for price in (0,-1,True,90.0,"90",None,121):
            with self.subTest(price=price):
                self.assertIsNone(self.call(history=[("shop asked",120),("you offered",price)]))

    def test_invalid_limits_leave_fallback(self):
        for limit in (0,-1,True,100.0,"100",None,80):
            with self.subTest(limit=limit):
                self.assertIsNone(self.call(limit=limit))

    def test_only_exact_standing_ask_is_eligible(self):
        for text in (TEXT.replace("120","121"), TEXT.replace("120","119"),
                     TEXT.replace("120","0"), TEXT.replace("120","0120"),
                     TEXT.replace("120","120.5"), TEXT.replace("120","1200000000000")):
            with self.subTest(text=text):
                self.assertIsNone(self.call(text=text))
        self.assertIsNone(self.call(history=[("shop asked",120),("you offered",121)],limit=130))

    def test_dates_discounts_other_goods_and_extra_numbers_fail_closed(self):
        for suffix in (" Expires 2099-01-01.", " Discount 10.", " Other SKU G12345678.",
                       " No warranty.", " 120 130.", " This is the lowest price.", "\n"):
            with self.subTest(suffix=suffix):
                self.assertIsNone(self.call(text=TEXT+suffix))
        self.assertIsNone(self.call(text=None))

    def test_cap_repeat_walks_without_zero_offer(self):
        self.assertEqual(self.call(history=[("shop asked",120),("you offered",100)]),
                         {"shop_price":120,"action":"walk_away","price":0})

    def test_within_cap_takes_ask(self):
        self.assertEqual(self.call(limit=130), {"shop_price":120,"action":"offer","price":120})

    def test_equal_ask_rejected_by_shop_walks(self):
        self.assertEqual(self.call(limit=120, history=[("shop asked",120),("you offered",120)]),
                         {"shop_price":120,"action":"walk_away","price":0})

    def test_valid_history_is_not_mutated(self):
        history = [["shop asked",120],["you offered",90]]
        self.assertEqual(self.call(history=history), {"shop_price":120,"action":"offer","price":100})
        self.assertEqual(history, [["shop asked",120],["you offered",90]])

    def test_existing_goods_no_warranty_rejection_is_preserved(self):
        text = "Counteroffer: 120. No warranty."
        history = [("shop asked",120),("you offered",90)]
        self.assertIsNone(oneshot.turn(None, text, history, 100, goods=True))
        self.assertIsNone(self.call(text=text))

TEXT = 'Counteroffer: 120. This is my lowest price.'
if __name__ == "__main__":
    unittest.main()
