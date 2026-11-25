""""N is too low. Counteroffer price: M." names the seller's ask M; the parser read it as no price and sent the order to the model.
The fix keeps the parser's cautions: another party's or a past figure, a range, a second price or a malformed number is never read."""

import unittest
from unittest.mock import patch

import buyer_fast as fast
from test_market_buyer import TradeFixture, shop
import market_buyer as market

# Seller replies to our counter as saved in logs/buyer (round 3); all 132 saved replies of this kind have this one wording.
REAL = ("100000 is too low. Counteroffer price: 117077.", "21000 is too low. Counteroffer price: 22312.",
        "21200 is too low. Counteroffer price: 22312.", "20500 is too low. Counteroffer price: 22312.",
        "318838 is too low. Counteroffer price: 325000.", "20507 is too low. Counteroffer price: 21587.",
        "18500 is too low. Counteroffer price: 23524.", "19200 is too low. Counteroffer price: 23524.",
        "11684 is too low. Counteroffer price: 12442.")


class Parse(unittest.TestCase):
    def test_real_replies_read_as_the_sellers_counter(self):
        for text in REAL:
            want = int(text.rsplit(" ", 1)[1].rstrip("."))
            with self.subTest(text=text):
                self.assertEqual(fast.parse_ask(text), (want, "single_ask"))
                self.assertEqual(fast.read_ask(text, None, fast.load_reading())[:2], (want, "single_ask"))

    def test_spellings_of_the_same_label(self):
        for text in ("Counter-offer price: 12442.", "counteroffer price 12442", "Counteroffer price is 12442.", "Counteroffer price: 12,442 KRW."):
            with self.subTest(text=text):
                self.assertEqual(fast.parse_ask(text), (12442, "single_ask"))

    def test_the_cautions_still_hold(self):
        for text, code in (("Your counteroffer price: 9000 is too low.", "non_current_price"),
                           ("Previous counteroffer price: 12442.", "non_current_price"),
                           ("Counteroffer price: 12442 or 12000.", "malformed_price"),
                           ("Counteroffer price: 12442-12600.", "malformed_price"),
                           ("Counteroffer price: 12442.50", "malformed_price"),
                           ("Counteroffer price: 12442. Asking price: 13000.", "conflicting_prices"),
                           ("Counteroffer price: 12442. Today only 12000.", "conflicting_prices")):
            with self.subTest(text=text):
                self.assertEqual(fast.parse_ask(text), (None, code))

    def test_the_number_we_offered_is_not_taken_for_the_ask(self):
        self.assertEqual(fast.classify("11684 is too low. Counteroffer price: 12442.", 11684, 11684), (None, "over_budget", 12442))
        decision, code, price = fast.classify("100 is too low. Counteroffer price: 98.", 100, 100)
        self.assertEqual((code, price, decision["price"]), ("rule_offer", 98, 98))


class Trades(TradeFixture):
    ON = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}

    def setUp(self):
        super().setUp()
        self.gate_calls = []

    def gate(self):
        self.gate_calls.append(1)
        return "model_quota_hour"

    def counter_trade(self, transport):
        return market.trade(shop("fixture"), "lamp", 100, transport=transport, fast_rules=True, max_turns=3, model_gate=self.gate,
                            over_budget_counter=self.ON, catalog_list_price=200, price_reading=fast.load_reading())

    def test_a_counter_within_the_cap_is_bought_by_the_rule_with_the_receipt_check(self):
        replies = iter(["100 is too low. Counteroffer price: 98.", None])
        transport = self.transport("asking 110.")
        inner = transport.handler
        def respond(request):
            response = inner(request)
            body = response.json() if response.status_code == 200 else None
            if body and request.method == "POST" and self.posts and ((self.posts[-1].get("params") or {}).get("message") or {}).get("taskId"):
                text = next(replies)
                if text is not None:
                    task = body["result"]
                    task["status"] = {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": text}]}}
                    task["artifacts"] = []
                    return type(response)(200, json=body)
            return response
        transport.handler = respond
        with patch("buyer.run_harness") as harness:
            result, record = self.counter_trade(transport)
            harness.assert_not_called()
        self.assertEqual(self.offers(), [100, 98])
        self.assertEqual([e["reason_code"] for e in record["decision_trail"]], ["rule_counter_offer", "rule_offer"])
        self.assertEqual((record["outcome"], result["receipt"]["price"]), ("purchased", 98))

    def test_a_counter_over_the_cap_is_read_and_still_never_paid(self):
        with patch("buyer.run_harness") as harness:
            _, record = self.counter_trade(self.transport("asking 110.", after_offer="100 is too low. Counteroffer price: 105."))
            harness.assert_not_called()
        self.assertEqual(self.offers(), [100])
        second = record["decision_trail"][1]
        self.assertEqual((second["parser_reason"], second["counter_check"]["ask"], second["counter_check"]["reason_code"]),
                         ("over_budget", 105, "counter_already_used"))
        self.assertEqual(record["failure_reason"], "model_quota_hour")


if __name__ == "__main__":
    unittest.main()
