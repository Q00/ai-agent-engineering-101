"""Offline lifecycle tests with simulated decisions, isolated from market history."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from seller import Seller


class SellerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "simulated-history.jsonl"
        self.seller = Seller(self.path)
        self.updater = MagicMock()
        for method in ("requires_input", "add_artifact", "complete", "reject", "cancel"):
            setattr(self.updater, method, AsyncMock())
        self.queue = AsyncMock()
        for target, replacement in (
            ("seller.TaskUpdater", MagicMock(return_value=self.updater)),
            ("seller.get_message_text", lambda message: message),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def context(self, text, task="simulated-task"):
        return SimpleNamespace(message=text, current_task=SimpleNamespace(
            id=task, context_id="simulated-context"))

    def rows(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    async def opening(self, task="simulated-task"):
        with patch("seller.decide_opening", return_value={
                "action": "ask", "price": 130, "reason": "Simulated opening"}):
            await self.seller.execute(self.context("lamp", task), self.queue)

    async def offer(self, price, action, response_price):
        with patch("seller.decide_offer", return_value={
                "action": action, "price": response_price, "reason": "Simulated decision"}):
            await self.seller.execute(self.context(str(price)), self.queue)

    async def test_counter_then_deal_preserves_opening_and_previous_lines(self):
        await self.opening()
        prefix = self.path.read_bytes()
        await self.offer(80, "counter", 100)
        with patch("seller.sign_receipt", side_effect=lambda receipt: receipt):
            await self.offer(100, "accept", 100)
        rows = self.rows()
        self.assertEqual([row["event"] for row in rows],
                         ["opened", "offer_received", "countered", "offer_received", "deal"])
        self.assertTrue(self.path.read_bytes().startswith(prefix))
        self.assertTrue(all(row["opening_ask"] == 130 for row in rows))
        self.assertEqual(rows[-1]["price"], 100)
        self.assertEqual(rows[-1]["ask"], 100)
        self.assertEqual(rows[-1]["source"], "local_seller")
        self.assertNotIn("simulated-task", self.seller.open)
        self.updater.complete.assert_awaited_once()

    async def test_rejection_and_cancellation_are_distinct(self):
        await self.opening()
        await self.offer(80, "counter", 100)
        await self.offer(90, "reject", None)
        await self.opening("simulated-cancel")
        await self.seller.cancel(self.context("", "simulated-cancel"), self.queue)
        endings = [row for row in self.rows() if row["event"] in {"rejected", "canceled"}]
        self.assertEqual([row["event"] for row in endings], ["rejected", "canceled"])
        self.assertTrue(all("price" not in row for row in endings))
        self.assertEqual(self.seller.open, {})

    async def test_failed_decision_does_not_invent_a_failed_trade(self):
        await self.opening()
        with patch("seller.decide_offer", side_effect=RuntimeError("Simulated timeout")):
            with self.assertRaisesRegex(RuntimeError, "Simulated timeout"):
                await self.seller.execute(self.context("110"), self.queue)
        self.assertEqual([row["event"] for row in self.rows()], ["opened", "offer_received"])
        self.assertIn("simulated-task", self.seller.open)

    async def test_restart_appends_and_invalid_input_is_not_an_offer(self):
        await self.opening()
        prefix = self.path.read_bytes()
        await self.seller.execute(self.context("hello"), self.queue)
        self.assertEqual(self.path.read_bytes(), prefix)
        self.seller = Seller(self.path)
        await self.opening("simulated-after-restart")
        self.assertTrue(self.path.read_bytes().startswith(prefix))
        self.assertEqual(len(self.rows()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
