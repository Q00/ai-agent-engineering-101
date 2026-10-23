"""Exercise buyer guardrails with synthetic A2A responses and no model calls."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from negotiation import negotiate, quote_details


CARD = {"name": "Test shop", "description": "Negotiates prices.",
        "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": "http://shop/rpc"}]}


def task(text="", *, state="TASK_STATE_INPUT_REQUIRED", metadata=None, receipt=None):
    value = {"id": "task-one", "status": {"state": state,
             "message": {"parts": [{"text": text}], "metadata": metadata or {}}}}
    if receipt is not None:
        value["artifacts"] = [{"parts": [{"data": receipt}]}]
    return value


def completed(price=100):
    receipt = {"shop": "Test shop", "item": "lamp", "price": price,
               "task": "task-one", "sig_seller": "untouched-signature"}
    return task(f"Deal at {price}", state="TASK_STATE_COMPLETED", receipt=receipt)


class NegotiationGuardTests(unittest.TestCase):
    def run_trade(self, responses, moves=(), *, budget=120, prefetched=False):
        def response(value):
            return Mock(json=Mock(return_value=value), raise_for_status=Mock())

        with patch("negotiation.httpx.get", return_value=response(CARD)) as get, \
                patch("negotiation.httpx.post", side_effect=[response({"result": {"task": t}})
                                                           for t in responses]) as post, \
                patch("negotiation.ask_json", side_effect=[(m, str(m)) for m in moves]) as harness, \
                redirect_stdout(io.StringIO()):
            result = negotiate("http://shop", "lamp", budget,
                               prefetched_card=CARD if prefetched else None)
        calls = [call.kwargs["json"] for call in post.call_args_list]
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["headers"], {"A2A-Version": "1.0"})
        for call in calls[1:]:
            if call["method"] == "SendMessage":
                self.assertEqual(call["params"]["message"]["taskId"], "task-one")
        return result, calls, harness, get

    @staticmethod
    def offers(calls):
        return [int(c["params"]["message"]["parts"][0]["text"])
                for c in calls[1:] if c["method"] == "SendMessage"]

    def test_high_opening_price_still_uses_negotiation(self):
        result, calls, harness, _ = self.run_trade(
            [task("lamp: asking 156"), completed()], [{"action": "offer", "price": 100}])
        self.assertEqual(result["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(self.offers(calls), [100])
        harness.assert_called_once()

    def test_offer_never_exceeds_budget_or_current_ask(self):
        for quote, expected in [(180, 120), (110, 110)]:
            with self.subTest(quote=quote):
                result, calls, _, _ = self.run_trade(
                    [task(f"Counteroffer: {quote}"), completed(expected)],
                    [{"action": "offer", "price": 999}])
                self.assertEqual(self.offers(calls), [expected])
                self.assertEqual(result["last_offer"], expected)

    def test_over_budget_fixed_price_cancels_after_initial_request(self):
        result, calls, harness, _ = self.run_trade([
            task("bookshelf: 260", metadata={"ask": 260,
                 "pricing": {"policy": "same_lowest_for_everyone"}}),
            task(state="TASK_STATE_CANCELED")])
        self.assertEqual([c["method"] for c in calls], ["SendMessage", "CancelTask"])
        self.assertIsNone(result["receipt"])
        self.assertIsNone(result["last_offer"])
        harness.assert_not_called()

    def test_affordable_fixed_quote_is_accepted_exactly(self):
        receipt_task = completed()
        result, calls, harness, _ = self.run_trade([
            task("Final price 100. Replies before 999", metadata={"ask": 100}), receipt_task])
        self.assertEqual(self.offers(calls), [100])
        self.assertIs(result["receipt"], receipt_task["artifacts"][0]["parts"][0]["data"])
        harness.assert_not_called()

    def test_fixed_minimum_text_above_budget_skips_model(self):
        _, calls, harness, _ = self.run_trade([
            task("Fixed minimum: 130"), task(state="TASK_STATE_CANCELED")])
        self.assertEqual(self.offers(calls), [])
        harness.assert_not_called()

    def test_same_offer_and_counter_twice_cancel(self):
        result, calls, harness, _ = self.run_trade([
            task("Asking 130"), task("Counteroffer: 125"), task("Counteroffer: 125"),
            task(state="TASK_STATE_CANCELED")],
            [{"action": "offer", "price": 100}] * 2)
        self.assertEqual(self.offers(calls), [100, 100])
        self.assertEqual(calls[-1]["method"], "CancelTask")
        self.assertEqual(harness.call_count, 2)
        self.assertEqual(result["state"], "TASK_STATE_CANCELED")

    def test_completed_receipt_wins_over_repeated_offer(self):
        result, calls, _, _ = self.run_trade([
            task("Asking 130"), task("Counteroffer: 125"), completed()],
            [{"action": "offer", "price": 100}] * 2)
        self.assertEqual(result["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(self.offers(calls), [100, 100])
        self.assertNotIn("CancelTask", [c["method"] for c in calls])

    def test_prefetched_card_avoids_another_get(self):
        _, _, _, get = self.run_trade([task("Final price 100"), completed()], prefetched=True)
        get.assert_not_called()

    def test_nonfixed_market_floor_quote_skips_model(self):
        result, calls, harness, _ = self.run_trade([task("lamp: asking 100"), completed()])
        self.assertEqual(self.offers(calls), [100])
        self.assertEqual(result['state'], 'TASK_STATE_COMPLETED')
        harness.assert_not_called()

    def test_budget_below_reference_floor_cancels_without_model(self):
        result, calls, harness, _ = self.run_trade([
            task("lamp: asking 130"), task(state="TASK_STATE_CANCELED")], budget=90)
        self.assertEqual([c['method'] for c in calls], ['SendMessage', 'CancelTask'])
        self.assertIsNone(result['receipt'])
        harness.assert_not_called()

    def test_affordable_below_floor_quote_is_not_discarded(self):
        _, calls, harness, _ = self.run_trade([task("lamp: asking 85"), completed(85)], budget=90)
        self.assertEqual(self.offers(calls), [85])
        harness.assert_not_called()

    def test_quote_metadata_precedes_last_text_number(self):
        self.assertEqual(quote_details(task(metadata={"ask": 100.0}), "Quote 100, reference 999", CARD),
                         (100, False))
        self.assertEqual(quote_details(task(), "Offer 60 is too low. Counteroffer: 110.", CARD),
                         (110, False))
        self.assertEqual(quote_details(task(), "Counteroffer: 110.5", CARD), (None, False))
        self.assertEqual(quote_details(task(metadata={'ask': 110, 'pricing': {'policy': []}}),
                                       'Counteroffer: 110', CARD), (110, False))


if __name__ == "__main__":
    unittest.main()
