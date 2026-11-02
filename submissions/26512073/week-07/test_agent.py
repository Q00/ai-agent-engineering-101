"""Offline tests for persistence, pricing, seller lifecycle, and receipt signing."""
import json
from pathlib import Path
import tempfile
import unittest
from seller_strategy import LEGACY_POLICY, POLICY, load_item_state, save_item_state
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import seller_strategy
import seller_strategy
import seller_strategy
from seller import Seller
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import base64
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from seller import canon, sign_receipt


class PricingStateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "simulated-state.json"

    def test_initial_prices(self):
        for item, reserve, ask in [("lamp", 100, 115), ("chair", 180, 207),
                                   ("bookshelf", 260, 299)]:
            state = load_item_state(item, reserve, self.path)
            self.assertEqual(state["ask"], ask)
            self.assertIsNone(state["last_reviewed_task"])
        self.assertFalse(self.path.exists())

    def test_price_and_checkpoint_survive_reload(self):
        state = load_item_state("lamp", 100, self.path)
        state.update(ask=113, last_reviewed_task="simulated-sixth-result")
        save_item_state("lamp", 100, state, self.path)
        self.assertEqual(load_item_state("lamp", 100, self.path), state)

    def test_saving_one_item_preserves_other_items(self):
        lamp = load_item_state("lamp", 100, self.path)
        save_item_state("lamp", 100, lamp, self.path)
        chair = load_item_state("chair", 180, self.path)
        save_item_state("chair", 180, chair, self.path)
        self.assertEqual(load_item_state("lamp", 100, self.path), lamp)
        self.assertEqual(load_item_state("chair", 180, self.path), chair)
        self.assertEqual(list(self.path.parent.glob("pricing-*.tmp")), [])

    def test_invalid_state_cannot_overwrite_valid_state(self):
        state = load_item_state("lamp", 100, self.path)
        save_item_state("lamp", 100, state, self.path)
        original = self.path.read_bytes()
        for invalid in (dict(state, ask=99), dict(state, ask=118), dict(state, ask=115.5),
                        dict(state, last_reviewed_task="")):
            with self.assertRaises(ValueError):
                save_item_state("lamp", 100, invalid, self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_file_is_preserved_instead_of_silently_reset(self):
        self.path.write_text("{unfinished", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_item_state("lamp", 100, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{unfinished")

    def test_legacy_migration_caps_prices_and_preserves_old_checkpoint(self):
        old = {"ask": 125, "policy": LEGACY_POLICY,
               "last_reviewed_task": "old-task", "last_review": {"action": "hold"}}
        self.path.write_text(json.dumps({"lamp": old}), encoding="utf-8")
        original = self.path.read_bytes()
        migrated = load_item_state("lamp", 100, self.path)
        self.assertEqual(migrated["ask"], 115)
        self.assertEqual(migrated["policy"], POLICY)
        self.assertIsNone(migrated["last_reviewed_task"])
        self.assertEqual(migrated["previous_policy_state"], old)
        self.assertEqual(self.path.read_bytes(), original)
        save_item_state("lamp", 100, migrated, self.path)
        self.assertEqual(load_item_state("lamp", 100, self.path), migrated)

    def test_legacy_price_below_cap_is_preserved(self):
        old = {"ask": 108, "policy": LEGACY_POLICY, "last_reviewed_task": None}
        self.path.write_text(json.dumps({"lamp": old}), encoding="utf-8")
        self.assertEqual(load_item_state("lamp", 100, self.path)["ask"], 108)


class AdaptiveOpeningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.state = self.folder / "simulated-state.json"
        self.history = self.folder / "simulated-history.jsonl"
        for target, value in (
            ("seller_strategy.STATE_PATH", self.state),
            ("seller_strategy.HISTORY_PATH", self.history),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("seller_strategy.load_shop_reviews", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_results(self, strong=4, opening_ask=115):
        rows = [{"task": f"simulated-{i}", "item": "lamp", "event": "deal",
                 "opening_ask": opening_ask, "price": opening_ask if i < strong else 100,
                 "policy": seller_strategy.POLICY} for i in range(6)]
        self.history.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                                encoding="utf-8")

    def reply(self, action):
        return patch("seller_strategy.call_harness", return_value={
            "action": action, "reason": "Simulated explanation; not market evidence"})

    def test_initial_prices_persist_without_model_calls(self):
        with patch("seller_strategy.call_harness") as harness:
            for item, reserve, price in [("lamp", 100, 115), ("chair", 180, 207),
                                          ("bookshelf", 260, 299)]:
                self.assertEqual(seller_strategy.decide_opening(item, reserve)["price"], price)
                self.assertEqual(seller_strategy.load_item_state(item, reserve)["ask"], price)
            harness.assert_not_called()

    def test_legacy_migration_does_not_reuse_old_results(self):
        self.write_results()
        rows = [json.loads(line) for line in self.history.read_text().splitlines()]
        for row in rows:
            row["policy"] = seller_strategy.LEGACY_POLICY
        self.history.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        old = {"ask": 125, "policy": seller_strategy.LEGACY_POLICY,
               "last_reviewed_task": "simulated-5"}
        self.state.write_text(json.dumps({"lamp": old}))
        with patch("seller_strategy.call_harness") as harness:
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 115)
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 115)
            harness.assert_not_called()
        state = seller_strategy.load_item_state("lamp", 100)
        self.assertEqual(state["previous_policy_state"], old)
        self.assertIsNone(state["last_reviewed_task"])

    def test_raise_persists_and_same_results_are_not_reused(self):
        state = seller_strategy.load_item_state("lamp", 100)
        state["ask"] = 110
        seller_strategy.save_item_state("lamp", 100, state)
        self.write_results(opening_ask=110)
        with self.reply("raise") as harness:
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 113)
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 113)
            harness.assert_called_once()
        state = seller_strategy.load_item_state("lamp", 100)
        self.assertEqual(state["last_reviewed_task"], "simulated-5")
        self.assertEqual(state["last_review"]["previous_ask"], 110)
        self.assertEqual(len(state["last_review"]["tasks"]), 6)

    def test_hold_also_consumes_window(self):
        self.write_results(strong=0)
        with self.reply("hold") as harness:
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 115)
            seller_strategy.decide_opening("lamp", 100)
            harness.assert_called_once()
        self.assertEqual(seller_strategy.load_item_state("lamp", 100)["last_reviewed_task"],
                         "simulated-5")

    def test_lower_persists_rounded_price(self):
        self.write_results(strong=0)
        with self.reply("lower"):
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 112)
        self.assertEqual(seller_strategy.load_item_state("lamp", 100)["ask"], 112)

    def test_failed_review_leaves_saved_state_unchanged(self):
        seller_strategy.decide_opening("lamp", 100)
        original = self.state.read_bytes()
        self.write_results()
        with patch("seller_strategy.call_harness", side_effect=RuntimeError("Simulated timeout")):
            with self.assertRaisesRegex(RuntimeError, "Simulated timeout"):
                seller_strategy.decide_opening("lamp", 100)
        self.assertEqual(self.state.read_bytes(), original)

    def test_concurrent_openings_review_once(self):
        self.write_results()
        with self.reply("raise") as harness:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(seller_strategy.decide_opening, "lamp", 100)
                           for _ in range(2)]
                self.assertEqual([future.result()["price"] for future in futures], [115, 115])
            harness.assert_called_once()

    def test_raise_at_cap_holds_and_consumes_window(self):
        self.write_results()
        with self.reply("raise") as harness:
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 115)
            self.assertEqual(seller_strategy.decide_opening("lamp", 100)["price"], 115)
            harness.assert_called_once()
        state = seller_strategy.load_item_state("lamp", 100)
        self.assertEqual(state["last_review"]["action"], "hold")
        self.assertEqual(state["last_reviewed_task"], "simulated-5")

    def test_lower_at_floor_holds(self):
        result = seller_strategy
        self.write_results(strong=0)
        with self.reply("lower"):
            decision = result.decide_price_adjustment("lamp", 100, 100,
                                                      seller_strategy.load_review_window("lamp"), [])
        self.assertEqual(decision["price"], 100)
        self.assertEqual(decision["action"], "hold")

    def test_recorded_policy_matches_window_reader(self):
        seller = Seller(self.history)
        deal = {"item": "lamp", "opening_ask": 115, "ask": 115}
        for i in range(6):
            seller.record(f"simulated-{i}", "deal", deal, price=115)
        self.assertEqual(len(seller_strategy.load_review_window("lamp")), 6)


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


class ReceiptTests(unittest.TestCase):
    def test_valid_and_changed_receipt(self):
        key_path = Path(__file__).resolve().parent / "shop_key.pem"
        original_key = key_path.read_bytes()
        public = serialization.load_pem_private_key(original_key, password=None).public_key()
        body = {"shop": "Afford Shop", "item": "lamp", "price": 100, "task": "offline-test"}
        signed = sign_receipt(dict(body))
        signature = base64.b64decode(signed["sig_seller"])
        public.verify(signature, canon(body))
        with self.assertRaises(InvalidSignature):
            public.verify(signature, canon(dict(body, price=120)))
        with self.assertRaises(InvalidSignature):
            public.verify(signature, canon(dict(body, task="another-task")))
        self.assertEqual(original_key, key_path.read_bytes())
        self.assertEqual(set(signed), {"shop", "item", "price", "task", "sig_seller"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
