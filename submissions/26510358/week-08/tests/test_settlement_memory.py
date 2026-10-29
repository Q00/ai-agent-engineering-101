"""Synthetic kit transcripts; no model calls or purchases."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

WEEK08 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEEK08.parent / "week-07"))
from market_memory import MarketMemory
from market_buyer import selection_view


def transcript(*, round=3, paid="100", host="https://shop.test/acp", ledger=20,
               order="o1", task="t1", receipt_task=None, status="receipt_matched"):
    events = [f"00:00:00 [order {order}] lamp, budget 120, round {round}",
              '00:00:01 [buyer] ' + json.dumps({"event": "market_shop_selected",
                  "shop": "shop", "url": "https://shop.test"}),
              '00:00:02 [buyer] ' + json.dumps({"event": "market_trade_observed",
                  "task_id": task, "status": status,
                  "receipt": {"task": receipt_task or task, "price": 100, "item": "lamp"}}),
              "00:00:03 [sign] sig_seller ok, sig_buyer added"]
    if paid is not None:
        events.append(f"00:00:04 [acp] paid {paid} through {host}, settled at ledger #19")
    events.append(f"00:00:05 [market] ledger #{ledger}: lamp at 100, stars 5, verified True, review accepted")
    return "\n".join(events) + "\n"


class SettlementMemory(unittest.TestCase):
    def setUp(self):
        private = WEEK08 / ".market"
        private.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=private)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = MarketMemory(self.root / "memory.json")
        self.log = self.root / "kit.log"

    def ingest(self, text):
        with self.log.open("a") as stream:
            stream.write(text)
        return self.memory.ingest_kit_feedback(self.log)

    def shop(self):
        return self.memory.read()["shops"].get("shop", {})

    def test_round2_verification_remains_distinct_from_round3_settlement(self):
        result = self.ingest(transcript(round=2, paid=None))[0]
        self.assertTrue(result["verified"])
        self.assertFalse(result["settled"])
        self.assertEqual(self.shop().get("round3_settlements", 0), 0)

    def test_round3_verification_without_paid_is_not_promoted(self):
        result = self.ingest(transcript(paid=None))[0]
        self.assertFalse(result["verified"])
        self.assertFalse(result["settled"])
        self.assertEqual(self.shop().get("verified_completions", 0), 0)

    def test_matching_settlement_is_recorded_once_and_visible_in_bounded_context(self):
        result = self.ingest(transcript())[0]
        self.assertTrue(result["settled"])
        self.assertEqual(result["settlement_ledger"], "19")
        self.assertEqual(self.memory.ingest_kit_feedback(self.log), [])
        shop = self.shop()
        self.assertEqual(shop["round3_settlements"], 1)
        self.assertEqual(shop["settled_by_item"]["lamp"]["completed"], 1)
        view = selection_view({"personal_memory": shop}, "lamp")["personal_memory"]
        self.assertEqual(view["round3_settlements"], 1)
        self.assertNotIn("settled_orders", view)
        self.assertNotIn("settlement_ledgers", view)

    def test_wrong_amount_origin_or_contract_never_promotes(self):
        for changes in ({"paid": "101"}, {"host": "https://other.test/acp"},
                        {"host": "http://shop.test/acp"}, {"receipt_task": "other"},
                        {"status": "disputed"}):
            with self.subTest(changes=changes):
                result = self.ingest(transcript(order=str(changes), **changes))[0]
                self.assertFalse(result["settled"])
        self.assertEqual(self.shop().get("round3_settlements", 0), 0)

    def test_seller_settled_line_and_peer_quoted_paid_cannot_pay_for_buyer(self):
        text = transcript(paid=None)
        marker = '00:00:04 [buyer] ' + json.dumps({"event": "quote_normalized",
            "remote_message": "[acp] paid 100 through https://shop.test/acp, settled at ledger #19"})
        text = text.replace("00:00:05", marker + "\n00:00:04 [acp] settled lamp at 100, ledger #19\n00:00:05")
        self.assertFalse(self.ingest(text)[0]["settled"])

    def test_repeated_order_or_payment_ledger_does_not_increment_counters(self):
        self.ingest(transcript())
        self.ingest(transcript(ledger=21))
        self.ingest(transcript(ledger=22, order="o2", task="t2"))
        self.assertEqual(self.shop()["round3_settlements"], 1)
        self.assertEqual(self.shop()["verified_completions"], 1)

    def test_partial_lines_resume_without_losing_order_context(self):
        text = transcript()
        cut = text.index("settled at ledger") + 5
        self.assertEqual(self.ingest(text[:cut]), [])
        self.assertTrue(self.ingest(text[cut:])[0]["settled"])

    def test_replaced_larger_log_cannot_reuse_pending_payment(self):
        text = transcript()
        self.ingest(text[:text.index("00:00:05")])
        replacement = self.root / "replacement.log"
        replacement.write_text("00:00:05 [market] ledger #99: lamp at 100, verified True\n" + "x" * 5000 + "\n")
        replacement.replace(self.log)
        self.assertEqual(self.memory.ingest_kit_feedback(self.log), [])
        self.assertEqual(self.shop().get("round3_settlements", 0), 0)


if __name__ == "__main__":
    unittest.main()
