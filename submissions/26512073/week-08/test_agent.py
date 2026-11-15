"""Offline tests for persistence, pricing, seller lifecycle, and receipt signing."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from seller import Seller, canon, sign_receipt
import seller_strategy
from seller_strategy import LEGACY_POLICY, POLICY, load_item_state, save_item_state


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
            ("seller.round3_inventory", MagicMock(return_value=False)),
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


class InventoryTests(unittest.TestCase):
    def test_opening_uses_five_percent_above_actual_cost(self):
        with patch("seller_strategy.load_round3_rules", return_value={
                "seller_pricing": {"opening_markup_percent": 5}}):
            self.assertEqual(seller_strategy.stock_opening_ask({"cost": 10000}), 10500)
            self.assertEqual(seller_strategy.stock_opening_ask({"cost": 10001}), 10502)
            with self.assertRaises(ValueError):
                seller_strategy.stock_opening_ask({"cost": -1})

    def setUp(self):
        self.units = [
            {"unit": "food-1", "sku": "F0001", "name": "Sample food",
             "category": "Food", "grade": "A", "expires_at": 200},
            {"unit": "goods-1", "sku": "G0001", "name": "Sample chair",
             "category": "Furniture", "grade": "B", "expires_at": None},
            {"unit": "goods-10", "sku": "G00010", "name": "Another chair",
             "category": "Furniture", "grade": "A", "expires_at": None},
        ]

    def candidates(self, text, reserved=(), now=100):
        with patch("seller_strategy.load_inventory", return_value=self.units):
            return seller_strategy.available_units(text, reserved, now)

    def test_exact_sku_does_not_match_longer_sku(self):
        self.assertEqual([u["unit"] for u in self.candidates("Buy G0001")], ["goods-1"])

    def test_expiry_overrides_stale_grade(self):
        self.assertEqual(self.candidates("F0001", now=200), [])

    def test_reserved_unit_is_not_offered_again(self):
        self.assertEqual(self.candidates("G0001", reserved={"goods-1"}), [])

    def test_category_and_name_matching(self):
        self.assertEqual(len(self.candidates("Furniture")), 2)
        self.assertEqual(self.candidates("Sample food")[0]["unit"], "food-1")

    def test_cached_expired_unit_is_excluded(self):
        self.units[0]["grade"] = "expired"
        self.assertEqual(self.candidates("F0001"), [])

    def test_inventory_reader_uses_isolated_market_dir(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch.dict("os.environ", {"MARKET_DIR": folder}):
                self.assertEqual(seller_strategy.load_inventory(), [])
                path = Path(folder) / "inventory.json"
                path.write_text(json.dumps({"units": self.units}), encoding="utf-8")
                before = path.read_bytes()
                self.assertEqual(seller_strategy.load_inventory(), self.units)
                self.assertEqual(path.read_bytes(), before)
                path.write_text('{"units": {}}', encoding="utf-8")
                with self.assertRaises(ValueError):
                    seller_strategy.load_inventory()


class Round3BuyerTests(unittest.TestCase):
    def test_shop_choice_requires_stocked_acceptable_sku(self):
        import market_buyer
        shops = [{"name": "Sample", "url": "http://sample", "reviews": [],
                  "offers": [{"sku": "F1", "qty": 1}]}]
        with patch("market_buyer.call_harness", return_value={
                "shop_name": "Sample", "sku": "G2", "reason": "Simulated"}):
            with self.assertRaises(ValueError):
                market_buyer.choose_shop(shops, [], "Food", 100, {"acceptable": ["F1"]})

    def test_valid_sku_choice_preserves_shop_input(self):
        import market_buyer
        shops = [{"name": "Sample", "url": "http://sample", "reviews": [],
                  "offers": [{"sku": "F1", "qty": 1}]}]
        with patch("market_buyer.call_harness", return_value={
                "shop_name": "Sample", "sku": "F1", "reason": "Simulated"}):
            choice = market_buyer.choose_shop(shops, [], "Food", 100, {"acceptable": ["F1"]})
        self.assertEqual(choice["requested_sku"], "F1")
        self.assertNotIn("requested_sku", shops[0])

    def test_pending_rules_do_not_negotiate_or_write_memory(self):
        import io
        from contextlib import redirect_stdout
        import market_buyer
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            folder = Path(folder)
            shops, order, memory = [folder / name for name in ("shops.json", "order.json", "memory.jsonl")]
            shops.write_text("[]", encoding="utf-8")
            order.write_text(json.dumps({"order": "sample", "item": "Food",
                                        "budget": 100, "acceptable": ["F1"]}), encoding="utf-8")
            output = io.StringIO()
            with patch("market_buyer.load_round3_rules", return_value={"enabled": False}), \
                    patch("market_buyer.buy") as buy, redirect_stdout(output):
                market_buyer.market_buy(shops, "Food", 100, memory, order)
            buy.assert_not_called()
            self.assertFalse(memory.exists())
            self.assertIsNone(json.loads(output.getvalue())["receipt"])

    def test_restock_guard_requests_no_purchases(self):
        with patch("seller_strategy.load_round3_rules", return_value={"enabled": False}):
            self.assertEqual(seller_strategy.plan_restock(Path("unused-sample-path"))["buy"], [])

    def test_food_policy_follows_order_and_goods_need_no_warranty(self):
        import market_buyer
        food = market_buyer.acceptance_policy({"kind": "food", "use_within_hours": 8})
        self.assertEqual(food, {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 8})
        goods = market_buyer.acceptance_policy({"kind": "goods"})
        self.assertTrue(goods["cert"])
        self.assertEqual(goods["warranty_hours"], 0)

    def test_final_output_contains_quality_policy_and_pending_memory(self):
        import io
        from contextlib import redirect_stdout
        import market_buyer
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            folder = Path(folder)
            shops, order, memory = [folder / name for name in ("shops.json", "order.json", "memory.jsonl")]
            shops.write_text(json.dumps([{"name": "Sample", "url": "https://sample.invalid",
                                         "offers": [{"sku": "F1", "qty": 1}]}]))
            order.write_text(json.dumps({"order": "sample-order", "item": "Food", "kind": "food",
                                        "use_within_hours": 8, "budget": 100, "acceptable": ["F1"]}))
            receipt = {"item": "F1", "price": 90, "task": "sample-task", "sig_seller": "simulated"}
            output = io.StringIO()
            with patch("market_buyer.call_harness", return_value={
                    "shop_name": "Sample", "sku": "F1", "reason": "Simulated"}), \
                    patch("market_buyer.buy", return_value={"receipt": receipt, "outcome": "deal"}) as buy, \
                    redirect_stdout(output):
                market_buyer.market_buy(shops, "Food", 100, memory, order)
            final = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(final["receipt"], receipt)
            self.assertEqual(final["accept"]["min_hours_left"], 8)
            self.assertTrue(final["accept"]["cert"])
            self.assertEqual(json.loads(memory.read_text())["result"], "agreed_pending_payment")
            self.assertEqual(buy.call_args.args[1], "F1")

    def test_payment_memory_does_not_confuse_agreement_with_settlement(self):
        import market_buyer
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder) / "payments.jsonl"
            memory = [{"order": "one", "result": "agreed_pending_payment"},
                      {"order": "two", "result": "agreed_pending_payment"}]
            path.write_text(json.dumps({"order": "one", "paid": True, "ledger": 10}) + "\n"
                            + json.dumps({"order": "two", "paid": False, "reason": "No certificate"}) + "\n")
            enriched = market_buyer.settlement_memory(memory, path)
            self.assertEqual(enriched[0]["result"], "settled")
            self.assertEqual(enriched[1]["result"], "payment_refused")
            self.assertEqual(memory[0]["result"], "agreed_pending_payment")


class StockPricingTests(unittest.TestCase):
    def test_food_before_halfway_keeps_five_percent(self):
        unit = {"cost": 10000, "kind": "food", "supplied_at": 0, "expires_at": 200}
        self.assertEqual(seller_strategy.stock_minimum(unit, 0, now=99), 10500)

    def test_food_at_halfway_allows_three_percent_immediately(self):
        unit = {"cost": 10000, "kind": "food", "supplied_at": 0, "expires_at": 200}
        self.assertEqual(seller_strategy.stock_minimum(unit, 0, now=100), 10300)
        self.assertEqual(seller_strategy.stock_minimum(unit, 0, now=199), 10300)
        self.assertEqual(seller_strategy.stock_opening_ask(unit), 10500)

    def test_expired_food_never_gets_an_acceptable_minimum(self):
        unit = {"cost": 10000, "kind": "food", "supplied_at": 0, "expires_at": 200}
        with self.assertRaises(ValueError):
            seller_strategy.stock_minimum(unit, 0, now=200)

    def test_goods_do_not_receive_food_age_discount(self):
        unit = {"cost": 10000, "kind": "goods", "supplied_at": 0, "expires_at": None}
        self.assertEqual(seller_strategy.stock_minimum(unit, 0, now=150), 10500)

    def test_missing_arrival_time_does_not_invent_food_age(self):
        unit = {"cost": 10000, "kind": "food", "expires_at": 200}
        self.assertEqual(seller_strategy.stock_minimum(unit, 0, now=150), 10500)

    def test_model_can_accept_three_percent_without_prior_counters_for_older_food(self):
        unit = {"sku": "F1", "cost": 10000, "kind": "food", "supplied_at": 0, "expires_at": 200}
        with patch("seller_strategy.time.time", return_value=150), \
                patch("seller_strategy.call_harness", return_value={"action": "accept", "price": 10300}):
            self.assertEqual(seller_strategy.decide_stock_offer(unit, 10300, 0)["price"], 10300)

    def test_two_counters_before_lower_minimum(self):
        unit = {"sku": "S1", "cost": 10000}
        for counters, expected in [(0, 10500), (1, 10500), (2, 10300), (5, 10300)]:
            self.assertEqual(seller_strategy.stock_minimum(unit, counters), expected)
        with patch("seller_strategy.call_harness", return_value={
                "action": "accept", "price": 10300, "reason": "Simulated"}):
            with self.assertRaises(ValueError):
                seller_strategy.decide_stock_offer(unit, 10300, 1)
            self.assertEqual(seller_strategy.decide_stock_offer(unit, 10300, 2)["action"], "accept")


class StockSellerTests(unittest.IsolatedAsyncioTestCase):
    context = SellerHistoryTests.context
    rows = SellerHistoryTests.rows

    async def asyncSetUp(self):
        await SellerHistoryTests.asyncSetUp(self)
        import time
        self.unit = {"unit": "sample-unit", "sku": "S1", "name": "Sample",
                     "cost": 10000, "kind": "food", "grade": "A",
                     "expires_at": time.time() + 3600}
        for target, value in [("seller.round3_inventory", True),
                              ("seller.load_round3_rules", {"enabled": True})]:
            patcher = patch(target, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch("seller.available_units", side_effect=lambda text, reserved=():
                        [] if self.unit["unit"] in reserved else [self.unit])
        self.candidates = patcher.start(); self.addCleanup(patcher.stop)

    async def test_sku_counter_sequence_and_specific_unit_receipt(self):
        await self.seller.execute(self.context("Buy S1"), self.queue)
        self.assertEqual(self.rows()[-1]["ask"], 10500)
        signed = []
        decisions = [{"action": "counter", "price": 10500},
                     {"action": "counter", "price": 10500},
                     {"action": "accept", "price": 10300}]
        with patch("seller.decide_stock_offer", side_effect=decisions), \
                patch("seller.sign_receipt", side_effect=lambda r: signed.append(dict(r)) or r):
            for _ in range(3):
                await self.seller.execute(self.context("10300"), self.queue)
        self.assertEqual(signed[0]["unit"], "sample-unit")
        self.assertEqual(signed[0]["item"], "S1")
        self.assertTrue(signed[0]["cert"])
        self.assertEqual(self.rows()[-1]["event"], "agreement")
        self.assertNotIn("simulated-task", self.seller.open)
        self.assertIn("sample-unit", self.seller.held)

    async def test_same_stock_cannot_be_promised_twice(self):
        await self.seller.execute(self.context("Buy S1", "one"), self.queue)
        await self.seller.execute(self.context("Buy S1", "two"), self.queue)
        self.updater.reject.assert_awaited_once()
        self.assertEqual(len(self.seller.open), 1)

    async def test_fifo_uses_arrival_time_instead_of_expiry(self):
        import time
        now = time.time()
        old = dict(self.unit, unit="older", supplied_at=now - 200, expires_at=now + 7200)
        new = dict(self.unit, unit="newer", supplied_at=now - 100, expires_at=now + 3600)
        self.candidates.side_effect = None
        self.candidates.return_value = [new, old]
        await self.seller.execute(self.context("Buy S1"), self.queue)
        self.assertEqual(self.seller.open["simulated-task"]["unit"]["unit"], "older")

    async def test_fifo_applies_to_goods_too(self):
        old = dict(self.unit, unit="older", kind="goods", supplied_at=100, expires_at=None)
        new = dict(self.unit, unit="newer", kind="goods", supplied_at=200, expires_at=None)
        self.candidates.side_effect = None
        self.candidates.return_value = [new, old]
        await self.seller.execute(self.context("Buy S1"), self.queue)
        self.assertEqual(self.seller.open["simulated-task"]["unit"]["unit"], "older")

    async def test_cancellation_releases_unit(self):
        await self.seller.execute(self.context("Buy S1"), self.queue)
        await self.seller.cancel(self.context(""), self.queue)
        self.assertEqual(self.seller.held, {})

    async def test_model_usage_limit_releases_unit_without_agreement(self):
        from harness import HarnessUsageLimit
        await self.seller.execute(self.context("Buy S1"), self.queue)
        with patch("seller.decide_stock_offer", side_effect=HarnessUsageLimit("Model paused")):
            await self.seller.execute(self.context("10500"), self.queue)
        self.assertEqual(self.seller.held, {})
        self.assertEqual(self.seller.open, {})
        self.assertEqual(self.rows()[-1]["event"], "model_unavailable")
        self.updater.complete.assert_not_awaited()


class RestockTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", {"RESTOCK_OUTCOME_TRACKING": "v2"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initial_allowance_survives_repeated_hook_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            folder = Path(folder)
            (folder / "inventory.json").write_text(json.dumps({"round": 3, "balance": 5000000, "units": []}))
            catalog = {"F1": {"name": "Food", "kind": "food", "category": "Food", "list_price": 1000000},
                       "G1": {"name": "Goods", "kind": "goods", "category": "Goods", "list_price": 1000000},
                       "G2": {"name": "More", "kind": "goods", "category": "Goods", "list_price": 1000000}}
            (folder / "catalog.json").write_text(json.dumps(catalog))
            rules = {"enabled": True, "stock_budget": 2000000, "units_per_sku": 1}
            with patch("seller_strategy.load_round3_rules", return_value=rules), \
                    patch("seller_strategy.DATA_DIR", folder), \
                    patch("seller_strategy.call_harness", return_value={
                        "buy": [{"sku": "F1", "qty": 1}, {"sku": "G1", "qty": 1}],
                        "reason": "Simulated diversified selection"}):
                self.assertEqual(len(seller_strategy.plan_restock(folder)["buy"]), 2)
            before = (folder / "restock-reservations.jsonl").read_bytes()
            with patch("seller_strategy.load_round3_rules", return_value=rules), \
                    patch("seller_strategy.DATA_DIR", folder), \
                    patch("seller_strategy.call_harness") as harness:
                self.assertEqual(seller_strategy.plan_restock(folder)["buy"], [])
                harness.assert_not_called()
            self.assertEqual((folder / "restock-reservations.jsonl").read_bytes(), before)

    def test_over_budget_plan_creates_no_reservation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            folder = Path(folder)
            (folder / "inventory.json").write_text(json.dumps({"round": 3, "balance": 5000000, "units": []}))
            (folder / "catalog.json").write_text(json.dumps({
                sku: {"kind": "goods", "category": "Goods", "list_price": 2000000}
                for sku in ["G1", "G2"]}))
            with patch("seller_strategy.load_round3_rules", return_value={"enabled": True, "stock_budget": 2000000}), \
                    patch("seller_strategy.call_harness", return_value={
                        "buy": [{"sku": "G1", "qty": 1}, {"sku": "G2", "qty": 1}]}):
                with self.assertRaises(ValueError):
                    seller_strategy.plan_restock(folder)
            self.assertFalse((folder / "restock-reservations.jsonl").exists())


class BuyerDiscountTests(unittest.TestCase):
    def test_first_affordable_ask_requires_lower_offer(self):
        import buyer
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 90}):
            self.assertEqual(buyer.decide_buy("S1", 100, 95, [], 30)["price"], 90)
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 95}):
            with self.assertRaises(ValueError):
                buyer.decide_buy("S1", 100, 95, [], 30)

    def test_after_discount_accepts_affordable_counter_exactly(self):
        import buyer
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 95}):
            self.assertEqual(buyer.decide_buy("S1", 100, 95, [90], 30, True)["price"], 95)
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 92}):
            with self.assertRaises(ValueError):
                buyer.decide_buy("S1", 100, 95, [90], 30, True)

    def test_budget_guard_still_blocks_over_budget_offer(self):
        import buyer
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 101}):
            with self.assertRaises(ValueError):
                buyer.decide_buy("S1", 100, 120, [], 30)

    def test_free_item_does_not_request_negative_discount(self):
        import buyer
        with patch("buyer.call_harness", return_value={"action": "offer", "price": 0}):
            self.assertEqual(buyer.decide_buy("S1", 100, 0, [], 30)["price"], 0)

    def test_refused_discount_is_followed_by_acceptance(self):
        import buyer
        import io
        from contextlib import redirect_stdout
        def response(data):
            return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)
        def task(state, text="", receipt=None):
            body = {"id": "sample-task", "status": {"state": state,
                    "message": {"parts": [{"text": text}]}}}
            if receipt:
                body["artifacts"] = [{"parts": [{"data": receipt}]}]
            return response({"result": {"task": body}})
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = response({"supportedInterfaces": [{
            "protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "https://sample.invalid/"}]})
        client.post.side_effect = [
            task("TASK_STATE_INPUT_REQUIRED", "asking 95"),
            task("TASK_STATE_INPUT_REQUIRED", "Counteroffer: 95"),
            task("TASK_STATE_COMPLETED", receipt={"task": "sample-task", "item": "S1", "price": 95}),
        ]
        with patch("buyer.httpx.Client", return_value=client), \
                patch("buyer.call_harness", side_effect=[{"action": "offer", "price": 90},
                                                         {"action": "offer", "price": 95}]) as harness, \
                redirect_stdout(io.StringIO()):
            result = buyer.buy("https://sample.invalid", "S1", 100)
        self.assertEqual(result["offers"], [90, 95])
        self.assertEqual(result["receipt"]["price"], 95)
        self.assertIn('"affordable_discount_already_attempted": true', harness.call_args.args[0])


class StockBudgetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.state = self.root / ".market"
        self.logs = self.root / "logs"
        self.state.mkdir(); self.logs.mkdir()
        (self.root / "round3_rules.json").write_text('{"stock_budget": 2000}')

    def reserve(self, ids=("one", "two"), bound=750):
        rows = [{"reservation_id": rid, "sku": rid, "reserved_cost": bound} for rid in ids]
        (self.state / "restock-reservations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
        return [{"reservation_id": rid, "sku": rid, "qty": 1} for rid in ids]

    def run_supply(self, buys, responses):
        import market_runner
        kit = SimpleNamespace(
            last_json=lambda lines: json.loads(lines[-1]),
            run_buyer=lambda cmd, directory: [json.dumps({"buy": buys})],
            fill=lambda cmd, **kwargs: cmd, time=SimpleNamespace(sleep=MagicMock()),
            log=MagicMock(), refresh_inventory=MagicMock(), CATALOG={})
        market = SimpleNamespace(call=MagicMock(side_effect=responses))
        market_runner.tracked_restock(kit, {"restock": {"cmd": "simulated"}},
                                     self.root, market, self.state, {}, [])
        return market

    def test_actual_cost_replaces_bound_and_refusal_releases_it(self):
        import httpx, market_runner
        buys = self.reserve()
        self.run_supply(buys, [httpx.Response(200, json={"total": 500, "balance": 5000}),
                               httpx.Response(400, json={"error": "Not supplied"})])
        result = market_runner.stock_budget_status(self.state, 2000)
        self.assertEqual(result["charged"], 500)
        self.assertEqual(result["remaining"], 1500)
        self.assertEqual(result["pending_skus"], set())

    def test_closed_window_releases_unattempted_requests_and_records_cooldown(self):
        import httpx, market_runner, time
        buys = self.reserve()
        market = self.run_supply(buys, [httpx.Response(429, json={
            "error": "Supply is closed", "wait": 600, "opens_at": time.time() + 600})])
        self.assertEqual(market.call.call_count, 1)
        result = market_runner.stock_budget_status(self.state, 2000)
        self.assertEqual(result["charged"], 0)
        self.assertGreater(result["next_open"], time.time())

    def test_unknown_transport_outcome_keeps_reservation(self):
        import httpx, market_runner
        buys = self.reserve()
        self.run_supply(buys, [httpx.ReadTimeout("Simulated ambiguous response")])
        result = market_runner.stock_budget_status(self.state, 2000)
        self.assertEqual(result["charged"], 750)
        self.assertEqual(result["pending_skus"], {"one"})

    def test_replayed_request_does_not_purchase_twice(self):
        import httpx
        buys = self.reserve(ids=("one",))
        market = self.run_supply(buys * 2, [httpx.Response(200, json={"total": 500, "balance": 5000})])
        self.assertEqual(market.call.call_count, 1)

    def test_legacy_reconciliation_is_idempotent_and_preserves_raw_logs(self):
        import market_runner
        rows = [{"sku": "one", "reserved_cost": 750}, {"sku": "two", "reserved_cost": 300},
                {"sku": "unknown", "reserved_cost": 200}]
        (self.state / "restock-reservations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
        (self.state / "catalog.json").write_text(json.dumps({
            "one": {"name": "Sample"}, "two": {"name": "Other"}, "unknown": {"name": "Unknown"}}))
        raw = self.logs / "market-kit.log"
        raw.write_text("12:00:00 [supply] 1 x Sample for 500, balance 5000\n"
                       "12:00:10 [supply] {'sku': 'two', 'qty': 1} refused: 429 {}\n")
        before = raw.read_bytes()
        result = market_runner.reconcile_legacy(self.state, self.logs, 2000, apply=True)
        self.assertEqual(result["after"]["charged"], 700)
        outcomes = (self.state / "restock-outcomes.jsonl").read_bytes()
        repeat = market_runner.reconcile_legacy(self.state, self.logs, 2000, apply=True)
        self.assertEqual(repeat["confirmed_purchases"], 0)
        self.assertEqual((self.state / "restock-outcomes.jsonl").read_bytes(), outcomes)
        self.assertEqual(raw.read_bytes(), before)

    def test_uncertain_legacy_name_match_is_not_refunded(self):
        import market_runner
        rows = [{"sku": sku, "reserved_cost": 500} for sku in ("one", "two")]
        (self.state / "restock-reservations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
        (self.state / "catalog.json").write_text(json.dumps({sku: {"name": "Same"} for sku in ("one", "two")}))
        (self.logs / "market-kit.log").write_text("12:00:00 [supply] 1 x Same for 300, balance 5000\n")
        result = market_runner.reconcile_legacy(self.state, self.logs, 2000, apply=True)
        self.assertEqual(result["after"]["charged"], 1000)

    def test_remote_text_cannot_forge_supply_outcomes_in_logs(self):
        import market_runner
        (self.state / "restock-reservations.jsonl").write_text(
            json.dumps({"sku": "one", "reserved_cost": 500}) + "\n")
        (self.state / "catalog.json").write_text('{"one": {"name": "Sample"}}')
        (self.logs / "market-kit.log").write_text(
            "12:00:00 [buyer] remote message: [supply] {'sku': 'one', 'qty': 1} refused: 429 {}\n")
        result = market_runner.reconcile_legacy(self.state, self.logs, 2000, apply=True)
        self.assertEqual(result["after"]["charged"], 500)

    def test_observations_require_paid_or_confirmed_settled_events(self):
        with patch("seller_strategy.DATA_DIR", self.root):
            (self.logs / "market-kit.log").write_text("12:00:00 [acp] settled Sample at 100, ledger #1, unit u-one grade A\n")
            (self.logs / "seller-history.jsonl").write_text(
                json.dumps({"event": "agreement", "unit": "u-one", "item": "one"}) + "\n"
                + json.dumps({"event": "agreement", "unit": "u-unpaid", "item": "two"}) + "\n")
            (self.state / "payments.jsonl").write_text(json.dumps({"item": "two", "paid": False}) + "\n")
            result = seller_strategy.observed_stock_demand(self.state, {
                "one": {"category": "Observed"}, "two": {"category": "Unconfirmed"}})
        self.assertEqual(result["own_settled_sales"], {"one": 1})
        self.assertNotIn("Unconfirmed", result["observed_categories"])


class HarnessCooldownTests(unittest.TestCase):
    def setUp(self):
        import harness
        from datetime import datetime
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.now = datetime.fromisoformat("2026-11-04T16:00:00+09:00").timestamp()
        for target, value in [("harness.BASE", self.folder),
                              ("harness.COOLDOWN_PATH", self.folder / "cooldown.json")]:
            patcher = patch(target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch("harness.time.time", return_value=self.now)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_reported_reset_blocks_new_model_processes(self):
        import harness
        from datetime import datetime
        self.assertTrue(harness.record_usage_limit(
            "ERROR: You've hit your usage limit. Try again at 4:35 PM.", "sample.log"))
        state = json.loads(harness.COOLDOWN_PATH.read_text())
        expected = datetime.fromisoformat("2026-11-04T16:35:05+09:00").timestamp()
        self.assertEqual(state["retry_after"], expected)
        with patch("harness.find_codex") as find, patch("harness.subprocess.run") as process:
            with self.assertRaises(harness.HarnessUsageLimit):
                harness.call_harness("Sample")
            find.assert_not_called(); process.assert_not_called()

    def test_after_reset_model_calls_resume(self):
        import harness
        harness.COOLDOWN_PATH.write_text(json.dumps({"retry_after": self.now - 1}))
        with patch("harness.find_codex", return_value="sample-cli"), \
                patch("harness._run_attempt", return_value=({"ok": True}, None)) as run:
            self.assertEqual(harness.call_harness("Sample"), {"ok": True})
            run.assert_called_once()

    def test_untrusted_text_is_not_a_usage_diagnostic(self):
        import harness
        self.assertFalse(harness.record_usage_limit(
            'Review: "ERROR: You have hit your usage limit"', "sample.log"))
        self.assertFalse(harness.COOLDOWN_PATH.exists())

    def test_past_or_unknown_reset_uses_short_cooldown(self):
        import harness
        harness.record_usage_limit("ERROR: You've hit your usage limit. Try again at 3:00 PM.", "sample.log")
        state = json.loads(harness.COOLDOWN_PATH.read_text())
        self.assertEqual(state["retry_after"], self.now + 300)

    def test_cli_error_preserves_raw_output_and_sets_shared_cooldown(self):
        import harness,time
        log = self.folder / "raw-attempt.txt"
        result = SimpleNamespace(returncode=1, stdout="", stderr=
            "ERROR: You've hit your usage limit. Try again at 4:35 PM.")
        with patch("harness.subprocess.run", return_value=result):
            with self.assertRaises(harness.HarnessUsageLimit):
                harness._run_attempt(["sample-cli"], "Sample", 30, log, 0, time.monotonic() + 30)
        self.assertIn("hit your usage limit", log.read_text())
        self.assertTrue(harness.COOLDOWN_PATH.exists())


class CheckoutLifecycleTests(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from seller import build
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = build(9999, history_path=Path(self.folder.name) / "history.jsonl")
        self.client = self.enterContext(TestClient(self.app))
        self.session = {
            "id": "cs_sample", "task": "sample-task", "unit": "sample-unit",
            "status": "ready_for_payment",
        }
        self.app.state.checkout_sessions["cs_sample"] = self.session
        self.app.state.seller.held["sample-unit"] = 123
        self.url = "/acp/checkout_sessions/cs_sample"

    def test_get_and_missing_sessions(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.session)
        self.assertEqual(self.client.get("/acp/checkout_sessions/missing").status_code, 404)
        self.assertEqual(self.client.post("/acp/checkout_sessions/missing/cancel").status_code, 404)

    def test_cancel_releases_hold_and_retry_preserves_later_hold(self):
        response = self.client.post(self.url + "/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "canceled")
        self.assertNotIn("sample-unit", self.app.state.seller.held)
        self.app.state.seller.held["sample-unit"] = 456
        self.assertEqual(self.client.post(self.url + "/cancel").status_code, 200)
        self.assertEqual(self.app.state.seller.held["sample-unit"], 456)

    def test_cancel_preserves_other_negotiation_or_checkout_hold(self):
        self.app.state.seller.open["other-task"] = {"unit": {"unit": "sample-unit"}}
        self.assertEqual(self.client.post(self.url + "/cancel").status_code, 200)
        self.assertIn("sample-unit", self.app.state.seller.held)
        self.app.state.seller.open.clear()
        self.session["status"] = "ready_for_payment"
        self.app.state.checkout_sessions["cs_other"] = {
            **self.session, "id": "cs_other", "task": "other-task",
        }
        self.assertEqual(self.client.post(self.url + "/cancel").status_code, 200)
        self.assertIn("sample-unit", self.app.state.seller.held)

    def test_completed_session_cannot_be_canceled(self):
        self.session["status"] = "completed"
        self.assertEqual(self.client.post(self.url + "/cancel").status_code, 409)
        self.assertEqual(self.session["status"], "completed")
        self.assertIn("sample-unit", self.app.state.seller.held)


if __name__ == "__main__":
    unittest.main(verbosity=2)
