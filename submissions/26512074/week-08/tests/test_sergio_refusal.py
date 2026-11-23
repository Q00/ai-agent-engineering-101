import unittest
from unittest.mock import patch
from google.protobuf.struct_pb2 import Struct
from buyer_access import blocked_buyer


class RefusalTests(unittest.TestCase):
    def test_explicit_name_does_not_require_order_id(self):
        self.assertEqual(blocked_buyer([],{'buyer':'sergiobuilds'}),'sergiobuilds')

    def test_protobuf_metadata_is_also_blocked(self):
        meta=Struct();meta.update({'buyer':'SERGIOBUILDS'})
        self.assertEqual(blocked_buyer([],meta),'SERGIOBUILDS')

    def test_named_text_blocked(self):
        self.assertEqual(blocked_buyer(['buyer: sergiobuilds']),'sergiobuilds')

    def test_named_sentence_punctuation_is_not_part_of_identity(self):
        self.assertEqual(blocked_buyer(['buyer: sergiobuilds. Quote only.']),'sergiobuilds')
        self.assertEqual(blocked_buyer(['buyer: q00. Quote only.']),'q00')

    def test_anonymous_food_before_quote(self):
        text="I want to buy noodles (G5bdaf222). I need it to last 18 hours: please offer only a unit with at least 18.5 hours of shelf life left, with the market's certificate. What is your price?"
        self.assertIn('sergiobuilds',blocked_buyer([text]))

    def test_anonymous_goods_before_quote(self):
        self.assertIn('sergiobuilds',blocked_buyer(['I want to buy mattress (G03d0199f). What is your price?']))

    def test_generic_language_is_not_enough(self):
        with patch('review_policy.identity',return_value=(None,'anonymous')):
            self.assertIsNone(blocked_buyer(['I want to buy X.','That is more than I want to pay. I can do 5.']))

    def test_quote_only_other_client_not_blocked(self):
        with patch('review_policy.identity',return_value=(None,'anonymous')):
            self.assertIsNone(blocked_buyer(['Quote only, no purchase authorization: mattress (G03d0199f). Only grade A, six-hour warranty.']))
