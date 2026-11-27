"""Offline regression tests: python -B submissions/25512085/week-08/test_seller_food_offline.py.

Only a unique temporary directory is written; no market, CLI, or existing key/state is used.
"""
import base64
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import seller
import store


class FakeQueue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


class FakeUpdater:
    def __init__(self, event_queue, task_id, context_id):
        self.queue = event_queue

    def new_agent_message(self, parts):
        return parts[0]

    async def requires_input(self, message):
        await self.queue.enqueue_event(("input", message))

    async def reject(self, message):
        await self.queue.enqueue_event(("reject", message))

    async def complete(self, message):
        await self.queue.enqueue_event(("complete", message))

    async def add_artifact(self, parts, name):
        await self.queue.enqueue_event((name, parts[0]))


class FoodSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        preferred = Path.home() / "AppData/Local/Temp/opencode"
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="seller-food-offline-", dir=preferred if preferred.exists() else None)))
        self.key = Ed25519PrivateKey.generate()
        (self.tmp / "shop_key.pem").write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        self.now = 1_800_000_000.0
        self.inventory = {"round": 3, "units": []}
        self.catalog = {"f00000001": {"shelf_hours": 600, "category": "food>milk"}}
        replacements = {
            "BASE_DIR": self.tmp,
            "MARKET_DIR": self.tmp / ".market",
            "SELLER_HISTORY_FILE": self.tmp / "seller_history.json",
            "TaskUpdater": FakeUpdater,
            "get_message_text": lambda message: message,
            "new_text_part": lambda text: text,
            "new_data_part": lambda data: data,
            "load_inventory": lambda *args: self.inventory,
            "load_catalog": lambda *args: self.catalog,
            "load_hub": lambda: {},
            "record_inbound_inquiry": lambda *args, **kwargs: None,
            "clearance_factor": lambda: 1.0,
            "get_sku_market_anchor": lambda hub, sku, price: price,
        }
        for name, value in replacements.items():
            self.stack.enter_context(patch.object(seller, name, value))
        self.stack.enter_context(patch.object(store, "BASE_DIR", self.tmp))
        self.stack.enter_context(patch.object(store, "STATE_FILE", self.tmp / "state_hub.json"))
        self.stack.enter_context(patch.object(seller.time, "time", lambda: self.now))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")))
        self.agent = seller.Seller(0)

    def food(self, hours, uid="food-1", grade="A"):
        return dict(unit=uid, sku="f00000001", kind="food", name="milk 500ml 24팩",
                    category="food>milk", cost=100, list_price=200, grade=grade,
                    live_grade=grade, hours_left=hours, shelf_hours=600,
                    expires_at=self.now + hours * 3600 + 15, supplied_at=1)

    def turn(self, message, task_id="task-1"):
        context = SimpleNamespace(current_task=SimpleNamespace(id=task_id, context_id="ctx"), message=message)
        queue = FakeQueue()
        # All fake I/O awaits complete inline. Drive the real execute coroutine
        # directly: Windows asyncio.run creates a socketpair even with no network I/O.
        coroutine = self.agent.execute(context, queue)
        try:
            try:
                coroutine.send(None)
            except StopIteration:
                pass
            else:
                self.fail("Unexpected external asynchronous I/O")
        finally:
            coroutine.close()
        return queue.events

    def assertNoReceipt(self, events):
        self.assertNotIn("receipt", [kind for kind, _ in events])
        self.assertFalse(self.agent.committed_units)

    def receipt(self, events):
        receipts = [data for kind, data in events if kind == "receipt"]
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
        self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]), seller.canon(body))
        return receipt

    def test_requirement_language(self):
        examples = {
            "use_within_hours=360": 360, "min_hours_left=360": 360,
            "use within 72.0 h": 72, "at least 360h shelf life": 360,
            "I need food to last 360 hours": 360, "require at least 360 hours": 360,
            "360시간 이상 필요": 360, "360시간 남아 있어야 함": 360,
            "360시간 동안 사용": 360, "360시간 후 먹을 예정": 360,
            "15일 동안 먹을 거예요": 360,
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(seller.extract_food_use_hours(text), expected)

    def test_product_price_warranty_and_bare_duration_not_requirement(self):
        for text in ("milk 500ml 24팩 price 36000", "need warranty 6h", "360h", "offer 36000"):
            with self.subTest(text=text):
                self.assertIsNone(seller.extract_food_use_hours(text))
        self.assertEqual(seller.extract_food_use_hours("360h", awaiting_duration=True), 360)

    def test_price_parser_ignores_duration_and_product_numbers(self):
        for text in ("use within 72.0 h", "use_within_hours=360", "min_hours_left=360",
                     "360시간 동안 먹을 예정", "15일 동안 먹", "milk 500ml 24팩", "360h",
                     "30 m", "30 mins", "warranty 6h"):
            with self.subTest(text=text):
                self.assertIsNone(seller.extract_buyer_offer(text))
        for text in ("use within 72.0 h; offer 36000", "use_within_hours=360; price 36000",
                     "360시간 필요, 가격 36000원"):
            with self.subTest(text=text):
                self.assertEqual(seller.extract_buyer_offer(text), 36000)

    def test_single_short_unit_rejected_by_selection(self):
        self.assertIsNone(seller.select_best_unit([self.food(137.5)], "use_within_hours=360"))

    def test_sku_selects_500h_not_137h(self):
        self.inventory["units"] = [self.food(137.5), self.food(500, "food-long")]
        chosen, _ = seller.find_stock_unit("f00000001 use_within_hours=360", set(), self.inventory, self.catalog)
        self.assertEqual(chosen["unit"], "food-long")

    def test_cheap_cannot_bypass_requirement(self):
        units = [self.food(137.5, grade="C"), self.food(500, "long")]
        self.assertEqual(seller.select_best_unit(units, "cheap use within 360h")["unit"], "long")
        self.assertIsNone(seller.select_best_unit(units[:1], "cheap use within 360h"))

    def test_fefo_only_among_suitable_units(self):
        units = [self.food(20), self.food(500, "later"), self.food(400, "first-fit")]
        self.assertEqual(seller.select_best_unit(units, "use within 360h")["unit"], "first-fit")

    def test_checkout_headroom(self):
        self.assertIsNone(seller.select_best_unit([self.food(72.24)], "use within 72h"))
        self.assertIsNotNone(seller.select_best_unit([self.food(72.26)], "use within 72h"))

    def test_short_stock_rejects_even_high_opening_offer(self):
        self.inventory["units"] = [self.food(137.5)]
        events = self.turn("f00000001 use_within_hours=360 offer 36000")
        self.assertEqual(events[0][0], "reject")
        self.assertNoReceipt(events)
        self.assertFalse(self.agent.open)

    def test_name_matching_short_stock_none_safe(self):
        self.inventory["units"] = [self.food(137.5)]
        events = self.turn("milk 500ml 24팩 use within 360h")
        self.assertEqual(events[0][0], "reject")
        self.assertNoReceipt(events)

    def test_explicit_72h_opening_receipt(self):
        self.inventory["units"] = [self.food(137.5)]
        receipt = self.receipt(self.turn("f00000001 use within 72h offer 36000"))
        self.assertEqual(receipt["unit"], "food-1")
        self.assertNotIn("warranty_hours", receipt)

    def test_unknown_period_high_offer_asks_and_never_signs(self):
        self.inventory["units"] = [self.food(137.5)]
        events = self.turn("f00000001 offer 36000")
        self.assertNoReceipt(events)
        self.assertIn("137.5h", events[0][1])
        self.assertIn("server does not disclose", events[0][1])
        self.assertIn("No receipt", events[0][1])
        self.assertNoReceipt(self.turn("36000"))
        self.assertEqual(self.agent.open["task-1"]["offers"], [])

    def test_period_only_answer_updates_session_not_price(self):
        self.inventory["units"] = [self.food(137.5)]
        self.turn("f00000001")
        events = self.turn("72h")
        self.assertNoReceipt(events)
        deal = self.agent.open["task-1"]
        self.assertEqual(deal["required_use_hours"], 72)
        self.assertEqual(deal["offers"], [])
        self.assertEqual(deal["rounds"], 0)
        self.assertIn("offer", events[0][1])
        self.receipt(self.turn("36000"))

    def test_requirement_revealed_later_rejects_and_unlocks(self):
        self.inventory["units"] = [self.food(137.5)]
        self.turn("f00000001")
        events = self.turn("360h")
        self.assertNoReceipt(events)
        self.assertEqual(events[0][0], "reject")
        self.assertFalse(self.agent.open)

    def test_requirement_increase_with_price_rejects_and_unlocks(self):
        self.inventory["units"] = [self.food(137.5)]
        self.turn("f00000001 use within 72h")
        events = self.turn("I need 360 hours; offer 36000")
        self.assertNoReceipt(events)
        self.assertEqual(events[0][0], "reject")
        self.assertFalse(self.agent.open)

    def test_live_expiry_rechecked(self):
        self.inventory["units"] = [self.food(72.26)]
        self.turn("f00000001 use within 72h")
        self.now += 0.02 * 3600  # 72s: below the existing 90s negotiation TTL.
        events = self.turn("offer 36000")
        self.assertNoReceipt(events)
        self.assertEqual(events[0][0], "reject")
        self.assertIn("unit released", events[0][1])
        self.assertFalse(self.agent.open)

    def test_explicit_period_and_price_answer_closes_at_price_not_hours(self):
        self.inventory["units"] = [self.food(500)]
        self.turn("f00000001")
        receipt = self.receipt(self.turn("use within 360h; offer 36000"))
        self.assertEqual(receipt["price"], 190)

    def test_period_does_not_decrease(self):
        self.inventory["units"] = [self.food(500)]
        self.turn("f00000001 use within 360h")
        self.turn("use within 72h")
        self.assertEqual(self.agent.open["task-1"]["required_use_hours"], 360)

    def test_opening_guard_rechecks_after_selection(self):
        self.inventory["units"] = [self.food(72.5)]
        original = seller.extract_buyer_offer

        def delayed_offer(*args, **kwargs):
            self.now += 0.3 * 3600
            return original(*args, **kwargs)

        with patch.object(seller, "extract_buyer_offer", delayed_offer):
            events = self.turn("f00000001 use within 72h offer 36000")
        self.assertNoReceipt(events)
        self.assertEqual(events[0][0], "reject")

    def test_negotiated_guard_rechecks_immediately_before_signing(self):
        self.inventory["units"] = [self.food(72.5)]
        self.turn("f00000001 use within 72h")
        original = seller.extract_buyer_offer

        def delayed_offer(*args, **kwargs):
            self.now += 0.3 * 3600
            return original(*args, **kwargs)

        with patch.object(seller, "extract_buyer_offer", delayed_offer):
            events = self.turn("offer 36000")
        self.assertNoReceipt(events)
        self.assertEqual(events[0][0], "reject")
        self.assertFalse(self.agent.open)

    def test_goods_selection_and_receipts_unchanged(self):
        goods = [dict(unit="good-c", sku="g00000001", kind="goods", grade="C", name="lamp",
                      category="goods>lamp", cost=100, list_price=200, supplied_at=1),
                 dict(unit="good-a", sku="g00000001", kind="goods", grade="A", name="lamp",
                      category="goods>lamp", cost=100, list_price=200, supplied_at=2)]
        self.assertEqual(seller.select_best_unit(goods, "warranty 6h")["unit"], "good-a")
        self.assertEqual(seller.select_best_unit(goods, "cheap use_within_hours=360")["unit"], "good-c")
        self.inventory["units"] = goods
        receipt = self.receipt(self.turn("g00000001 warranty 6h offer 36000"))
        self.assertEqual(receipt["unit"], "good-a")
        self.assertEqual(receipt["warranty_hours"], 6)
        self.agent = seller.Seller(0)
        self.turn("g00000001 warranty 6h")
        receipt = self.receipt(self.turn("offer 36000"))
        self.assertEqual(receipt["warranty_hours"], 6)

    def test_differentiated_clearance_food_vs_goods(self):
        hub = {}
        # Food (<12h): Salvage floor 35% of cost to prevent 100% expiration loss
        crit_food = dict(cost=1000, list_price=2000, kind="food", grade="B", live_grade="B", hours_left=10.0, sku="f1")
        crit_p = seller.compute_unit_pricing_and_terms(crit_food, hub)
        self.assertEqual(crit_p["hard_floor"], 350)
        self.assertEqual(crit_p["r1_target"], 840)  # max(350, 2000 * 0.42)

        # Food (<24h): Urgent markdown 55% of cost
        urg_food = dict(cost=1000, list_price=2000, kind="food", grade="B", live_grade="B", hours_left=20.0, sku="f2")
        urg_p = seller.compute_unit_pricing_and_terms(urg_food, hub)
        self.assertEqual(urg_p["hard_floor"], 550)

        # Fresh Food (>=48h Grade A): Full margin preserved
        fresh_food = dict(cost=1000, list_price=2000, kind="food", grade="A", live_grade="A", hours_left=100.0, sku="f3")
        fresh_p = seller.compute_unit_pricing_and_terms(fresh_food, hub)
        self.assertEqual(fresh_p["hard_floor"], 1030)  # cost * 1.03

        # Fresh Goods (<8h): Full margin & Safe-Care reserve preserved
        fresh_goods = dict(cost=1000, list_price=2000, kind="goods", grade="A", live_grade="A", supplied_at=self.now - 2 * 3600, sku="g1")
        fresh_gp = seller.compute_unit_pricing_and_terms(fresh_goods, hub)
        self.assertGreaterEqual(fresh_gp["hard_floor"], 1080)

        # Aged Goods (>=12h): Floor softened to cost * 0.95 to clear idle capital without panic dumping
        aged_goods = dict(cost=1000, list_price=2000, kind="goods", grade="A", live_grade="A", supplied_at=self.now - 14 * 3600, sku="g2")
        aged_gp = seller.compute_unit_pricing_and_terms(aged_goods, hub)
        self.assertEqual(aged_gp["hard_floor"], 950)  # cost * 0.95

    def test_memory_compile(self):
        for name in ("seller.py", Path(__file__).name):
            path = Path(__file__).with_name(name)
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
