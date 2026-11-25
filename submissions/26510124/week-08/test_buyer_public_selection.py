"""Public prices reach selection/negotiation without new fetch or model calls."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import time
import unittest
from unittest.mock import patch

from buyer_policy import load_policy, buying_terms, negotiation_policy
from buyer_public_prices import merge_price_evidence
from buyer_context import compact_dialogue
from market_buyer import choose_shop, ROOT, last_asking_price
from test_buyer_selection import shop
from test_buyer_value_selection import history


class PublicSelectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {**load_policy(), "selection_cache_seconds": 0}
        self.order = {"order": "o-public", "round": 3, "kind": "goods", "acceptable": ["G00001"]}
        self.catalog = {"G00001": {"kind": "goods", "list_price": 100000}}
        self.own = {("value", "G00001"): history(75000)}
        public = history(60000)
        public["groups"][0].update(source="public_ledger", buyer_count=2, evidence_id="a" * 20)
        self.public = {("value", "G00001"): public}

    def test_cheaper_same_quality_public_sample_survives_own_history_without_pooling(self):
        before = deepcopy((self.own, self.public))
        groups = merge_price_evidence(self.own, self.public)[("value", "G00001")]["groups"]
        self.assertEqual([g["median_price"] for g in groups], [75000, 60000])
        self.assertEqual([g["sample_count"] for g in groups], [3, 3])
        self.assertEqual(groups[1]["source"], "public_ledger")
        self.assertEqual((self.own, self.public), before)
        self.assertEqual(merge_price_evidence(self.own, {}), self.own)
        self.assertEqual(merge_price_evidence({}, self.public), self.public)

    def test_one_existing_selection_call_receives_and_returns_validated_public_hint(self):
        candidates = [shop("value"), shop("new")]
        def model(_, context, validate, **kw):
            selected = next(c for c in context["candidates"] if c["name"] == "value")
            groups = selected["offers"][0]["price_history"]
            self.assertEqual([g["median_price"] for g in groups], [75000, 60000])
            self.assertEqual(kw["max_attempts"], 1)
            return validate(json.dumps({"name": "value", "sku": "G00001", "reason": "compare actual terms"}))
        with patch("market_buyer.read_public_prices", return_value=self.public) as read, \
                patch("market_buyer.build_value_evidence", return_value=self.own), \
                patch("market_buyer.decide", side_effect=model) as called, redirect_stdout(StringIO()):
            selected, _ = choose_shop(candidates, [], item="chair", budget=100000,
                deadline=time.monotonic()+30, policy=self.config, order=self.order,
                catalog=self.catalog, market_dir="/unused")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(called.call_count, 1)
        self.assertEqual(selected["price_reference"][1]["source"], "public_ledger")

    def test_merged_negotiation_input_is_bounded_and_does_not_raise_cap(self):
        terms = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        terms["price_reference"] = merge_price_evidence(self.own, self.public)[("value", "G00001")]["groups"]
        msg = "Grade A. Warranty 6 hours. Asking price: 75000"
        context = {"item": "G00001", "budget": 100000, "purchase_policy": negotiation_policy(terms),
                   "current_ask": 75000, **compact_dialogue([], msg, last_asking_price)}
        self.assertLess(len((ROOT / "prompts/buyer.txt").read_text() + json.dumps(context)), 2800)
        self.assertEqual((terms["target_price"], terms["price_limit"]), (65000, 85000))
        self.assertEqual(len(terms["price_reference"]), 2)


if __name__ == "__main__":
    unittest.main()
