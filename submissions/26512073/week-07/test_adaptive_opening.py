"""Offline integration checks with simulated trades and harness replies."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pricing_state
import seller_decision
import seller_memory
from seller import Seller


class AdaptiveOpeningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.state = self.folder / "simulated-state.json"
        self.history = self.folder / "simulated-history.jsonl"
        for target, value in (
            ("pricing_state.STATE_PATH", self.state),
            ("seller_memory.HISTORY_PATH", self.history),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("seller_decision.load_shop_reviews", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_results(self, strong=4):
        rows = [{"task": f"simulated-{i}", "item": "lamp", "event": "deal",
                 "opening_ask": 115, "price": 115 if i < strong else 100,
                 "policy": pricing_state.POLICY} for i in range(6)]
        self.history.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                                encoding="utf-8")

    def reply(self, action):
        return patch("seller_decision.call_harness", return_value={
            "action": action, "reason": "Simulated explanation; not market evidence"})

    def test_initial_prices_persist_without_model_calls(self):
        with patch("seller_decision.call_harness") as harness:
            for item, reserve, price in [("lamp", 100, 115), ("chair", 180, 207),
                                          ("bookshelf", 260, 299)]:
                self.assertEqual(seller_decision.decide_opening(item, reserve)["price"], price)
                self.assertEqual(pricing_state.load_item_state(item, reserve)["ask"], price)
            harness.assert_not_called()

    def test_raise_persists_and_same_results_are_not_reused(self):
        self.write_results()
        with self.reply("raise") as harness:
            self.assertEqual(seller_decision.decide_opening("lamp", 100)["price"], 118)
            self.assertEqual(seller_decision.decide_opening("lamp", 100)["price"], 118)
            harness.assert_called_once()
        state = pricing_state.load_item_state("lamp", 100)
        self.assertEqual(state["last_reviewed_task"], "simulated-5")
        self.assertEqual(state["last_review"]["previous_ask"], 115)
        self.assertEqual(len(state["last_review"]["tasks"]), 6)

    def test_hold_also_consumes_window(self):
        self.write_results(strong=0)
        with self.reply("hold") as harness:
            self.assertEqual(seller_decision.decide_opening("lamp", 100)["price"], 115)
            seller_decision.decide_opening("lamp", 100)
            harness.assert_called_once()
        self.assertEqual(pricing_state.load_item_state("lamp", 100)["last_reviewed_task"],
                         "simulated-5")

    def test_lower_persists_rounded_price(self):
        self.write_results(strong=0)
        with self.reply("lower"):
            self.assertEqual(seller_decision.decide_opening("lamp", 100)["price"], 112)
        self.assertEqual(pricing_state.load_item_state("lamp", 100)["ask"], 112)

    def test_failed_review_leaves_saved_state_unchanged(self):
        seller_decision.decide_opening("lamp", 100)
        original = self.state.read_bytes()
        self.write_results()
        with patch("seller_decision.call_harness", side_effect=RuntimeError("Simulated timeout")):
            with self.assertRaisesRegex(RuntimeError, "Simulated timeout"):
                seller_decision.decide_opening("lamp", 100)
        self.assertEqual(self.state.read_bytes(), original)

    def test_concurrent_openings_review_once(self):
        self.write_results()
        with self.reply("raise") as harness:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(seller_decision.decide_opening, "lamp", 100)
                           for _ in range(2)]
                self.assertEqual([future.result()["price"] for future in futures], [118, 118])
            harness.assert_called_once()

    def test_recorded_policy_matches_window_reader(self):
        seller = Seller(self.history)
        deal = {"item": "lamp", "opening_ask": 115, "ask": 115}
        for i in range(6):
            seller.record(f"simulated-{i}", "deal", deal, price=115)
        self.assertEqual(len(seller_memory.load_review_window("lamp")), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
