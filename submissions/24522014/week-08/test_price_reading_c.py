"""Price reading group C (price_reading.group_c_free_labels): "our price is N", "the price is N", "final price: N" when no
label was found, and "asking price N with ..." / "asking N credits" where today's parser calls the number malformed.
Every caution of the current parser stays: past, list and other parties' figures, several or conflicting numbers, ranges,
"or" and decimals. A bare number at the end of a message is not read. Off unless set to true."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer_fast as fast
import market_buyer as market
from test_market_buyer import TradeFixture, shop

ALL = {"group_a": True, "lowest_price": True, "colon_krw": True, "free_labels": True}
NO_C = {**ALL, "free_labels": False}
ONLY_C = {"group_a": False, "lowest_price": False, "colon_krw": False, "free_labels": True}


def read(text, reading=ALL, metadata=None):
    return fast.read_ask(text, metadata, reading)


class Settings(unittest.TestCase):
    def load(self, section):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "s.json"
            path.write_text(json.dumps({"price_reading": section}), encoding="utf-8")
            return fast.load_reading(path)

    def test_only_true_turns_it_on(self):
        base = {"group_a_structured_and_labels": True, "group_b_lowest_price": True, "group_b_colon_krw": True}
        self.assertTrue(self.load({**base, "group_c_free_labels": True})["free_labels"])
        for bad in ("true", 1, None, False, [True]):
            with self.subTest(bad=bad):
                self.assertFalse(self.load({**base, "group_c_free_labels": bad})["free_labels"])
        self.assertFalse(self.load(base)["free_labels"])
        self.assertEqual(fast.OFF["free_labels"], False)

    def test_shipped_value(self):
        section = json.loads(fast.CONFIG.read_text(encoding="utf-8"))["price_reading"]
        self.assertIs(section["group_c_free_labels"], True)


class Reading(unittest.TestCase):
    def test_the_live_phrases(self):
        cases = (
            ("We have lowered our price, and this grade A sofa is in normal condition. Our price: 508220", 508220, "our_price"),
            ("It carries a three-day warranty, which also covers your six-hour requirement. Our price is 560718.", 560718, "our_price"),
            ("The market refunds the full price from our balance, which also covers shorter warranty requests. The price is 558500.", 558500, "the_price"),
            ("Five offers left. Final price: 131737.", 131737, "final_price_label"),
            ("Special concession: asking price 308696 with Safe-Care 72h: the market refunds the full price if the item breaks within 72 h of the sale. "
             "Reply with an offer or accept 308696 to close now.", 308696, "label_follower"),
            ("Curtain (G81ade83e): grade A, market certificate attached, warranty 6 h. Asking 25,360 credits. Send your offer as a number.", 25360, "label_follower"),
        )
        for text, price, how in cases:
            with self.subTest(text=text):
                self.assertEqual(read(text), (price, "single_ask", how))

    def test_off_reads_none_of_them(self):
        for text in ("Our price: 508220", "Our price is 560718.", "The price is 558500.", "Five offers left. Final price: 131737."):
            with self.subTest(text=text):
                self.assertIsNone(read(text, NO_C)[0])
        self.assertEqual(read("asking price 308696 with Safe-Care 72h.", NO_C)[1], "malformed_price")
        self.assertEqual(read("Asking 25,360 credits.", NO_C)[1], "malformed_price")

    def test_a_bare_number_at_the_end_is_not_read(self):
        for text in ("It comes with a three-day warranty, which also covers your shorter request. 60349",
                     "Thanks for your offer. 60349", "60349"):
            with self.subTest(text=text):
                self.assertIsNone(read(text)[0])

    def test_the_current_parser_and_earlier_groups_still_go_first(self):
        self.assertEqual(read("Asking 100. Our price: 90"), (100, "single_ask", None))
        self.assertEqual(read("My price: 100", ALL), (100, "single_ask", "my_price"))
        self.assertEqual(read("Our final price is 9000.", ALL)[0], 9000)

    def test_other_figures_are_not_read(self):
        for text in ("The price was 9000.", "Their price is 9000.", "The list price is 30000.", "List price: 30000. Final price was 25000.",
                     "Not the price: 9000.", "Previous final price: 9000.", "Market final price: 9000.", "Your price: 9000.",
                     "The price is 20% below list.", "The price is 9000 for grade A and 8000 for B.", "Our price is 9,000.5.",
                     "Our price is 9000 or 8500.", "Our price is 9000-9500.", "Final price: 9000 ~ 9500.", "The price is 9000 to 9500.",
                     "Our price is 9000 won off the list.", "asking price 9000 within the week."):
            with self.subTest(text=text):
                self.assertIsNone(read(text)[0])

    def test_two_figures_are_a_conflict(self):
        for text in ("Our price: 100. The price is 90.", "Our price is 100. Final price: 95.", "My price: 100. Our price: 90.",
                     "Asking 100 credits. Asking 90 credits.", "asking price 100 with care; asking price 90 with none.",
                     "Our price: 100. Today only 90."):
            with self.subTest(text=text):
                price, code, _ = read(text)
                self.assertIsNone(price)
                self.assertEqual(code, "conflicting_prices")

    def test_follower_words_are_only_with_and_credits(self):
        for text in ("Asking 25360 coins.", "asking price 308696 plus tax.", "Asking 25360 credits each, 3 for 70000."):
            with self.subTest(text=text):
                self.assertIsNone(read(text)[0])

    def test_group_c_alone(self):
        self.assertEqual(read("Our price: 508220", ONLY_C)[0], 508220)
        self.assertIsNone(read("My price: 100", ONLY_C)[0])


class NeverAboveTheLimit(TradeFixture):
    def run_it(self, quote, budget, *, receipt_price=None, counter=None):
        with patch("buyer.run_harness") as harness:
            result, record = market.trade(shop("fixture"), "lamp", budget, transport=self.transport(quote, receipt_price=receipt_price), fast_rules=True,
                                          model_gate=lambda: False, max_turns=3, price_reading=ALL, over_budget_counter=counter, catalog_list_price=budget * 2)
            harness.assert_not_called()
        return result, record

    def test_a_fair_reading_buys_at_that_price_and_is_logged(self):
        result, record = self.run_it("Five offers left. Final price: 800.", 1000)
        self.assertEqual(self.offers(), [800])
        self.assertEqual(result["receipt"]["price"], 800)
        self.assertEqual(record["decision_trail"][0]["price_read"], "final_price_label")

    def test_a_receipt_above_the_reading_is_never_passed_on(self):
        for quote in ("Our price is 1000.", "The price is 1000.", "Asking 1,000 credits.", "asking price 1000 with care."):
            with self.subTest(quote=quote):
                self.posts.clear()
                result, record = self.run_it(quote, 1000, receipt_price=1001)
                self.assertEqual(self.offers(), [1000])
                self.assertIsNone(result["receipt"])

    def test_a_reading_above_the_cap_sends_no_offer_above_it(self):
        for counter in (None, {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.1}):
            for quote in ("Our price: 1100", "The price is 999999.", "Asking 1,100 credits."):
                with self.subTest(quote=quote, counter=counter):
                    self.posts.clear()
                    result, _ = self.run_it(quote, 1000, counter=counter)
                    self.assertTrue(all(offer <= 1000 for offer in self.offers()))
                    if result["receipt"]:
                        self.assertLessEqual(result["receipt"]["price"], 1000)


if __name__ == "__main__":
    unittest.main()
