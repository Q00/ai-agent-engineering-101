"""Exact current-product C/B follow-up is a question, not a monetary offer."""
import json
from pathlib import Path
import unittest
from unittest import mock
import fast_classify as fc

def question(name="청소기 1200", sku="G1234abcd"):
    return (f"Do you have a grade C or B unit of {name} ({sku}) with a warranty of at least 6 hours "
            "on the receipt? I would rather take that one, priced for its grade. "
            "If not, tell me and we go on with this unit.")

class GradeFollowupTest(unittest.TestCase):
    def setUp(self):
        self.deal = {"name": "청소기 1200", "id": "G1234abcd", "unit": "fixture",
                     "ask": 25000, "reserve": 15000, "rounds": 2}

    def test_exact_question_bypasses_jev_and_keeps_offer_zero_and_ask(self):
        before = dict(self.deal)
        jev = mock.Mock(side_effect=AssertionError("must not call a model"))
        result = fc.seller_turn(question(), self.deal, 16000, jev)
        self.assertIsNotNone(result)
        self.assertEqual((result["buyer_offer"], result["action"], result["price"], result["message"]),
                         (0, "counter", 25000, ""))
        jev.assert_not_called()
        self.assertEqual(self.deal, before)

    def test_nine_real_messages_only_use_product_digits_not_prices(self):
        cases = json.loads(Path(__file__).with_name("grade_followup_real.json").read_text())
        self.assertEqual(len(cases), 9)
        for case in cases:
            with self.subTest(sku=case["id"]):
                deal = dict(self.deal, name=case["name"], id=case["id"])
                jev = mock.Mock(side_effect=AssertionError("unexpected model call"))
                answer = fc.seller_turn(case["text"], deal, 16000, jev)
                self.assertEqual((answer["buyer_offer"], answer["price"]), (0, 25000))
                jev.assert_not_called()

    def test_suffix_offer_other_product_and_nearby_questions_keep_old_reader(self):
        for text in (question() + " I offer 20000.", question() + " Without warranty.",
                     question(name="다른 상품"), question(sku="G9999abcd"),
                     question().replace("6 hours", "12 hours"), question() + "\n",
                     question().replace("grade C or B", "grade A"),
                     "I can offer 20000. " + question()):
            with self.subTest(text=text):
                jev = mock.Mock(return_value=(None, .99))
                self.assertIsNone(fc.seller_turn(text, self.deal, 16000, jev))
                jev.assert_called_once_with(text)

    def test_non_inventory_or_incomplete_deal_is_not_shortcut(self):
        for changes in ({"unit": None}, {"id": None}, {"name": None}):
            jev = mock.Mock(return_value=(None, .99))
            self.assertIsNone(fc.seller_turn(question(), dict(self.deal, **changes), 16000, jev))
            jev.assert_called_once()
