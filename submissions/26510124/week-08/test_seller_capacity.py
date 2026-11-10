"""Offline congestion checks: three workers, deterministic quotes, real receipt path."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from a2a.helpers import new_text_part
from a2a.types import Message
from starlette.testclient import TestClient

from harness import HarnessError
from seller_agent import DEFAULT, ModelCapacityError, build
from seller_r3 import capacity_choice
from test_adaptive_seller import send
import test_inventory as fixtures
from vendor import market_kit_v4 as kit


class CapacityPriceTests(unittest.TestCase):
    def test_opening_uses_pinned_target_or_bounded_floor_markup(self):
        product = {"list_price": 100000}
        n = {"stage": "opening", "price_floor": 70000}
        self.assertEqual(capacity_choice(product, None, None, n)["price"], 77000)
        band = {"minimum": 80000, "maximum": 84000, "target": 82000}
        self.assertEqual(capacity_choice(product, None, None, {**n, "opening_bounds": band})["price"], 82000)
        self.assertEqual(capacity_choice({"list_price": 75000}, None, None, n)["price"], 75000)
        self.assertEqual(capacity_choice({"list_price": 65000}, None, None, n)["price"], 70000)

    def test_counter_never_raises_ask_or_breaks_floor(self):
        for floor in (1, 70000, 80000):
            for ask in (80000, 90001):
                for offer in (0, 1, 60000, 79999, 90001):
                    n = {"stage": "negotiate", "price_floor": floor}
                    c = capacity_choice({}, offer, ask, n)
                    if offer >= ask:
                        self.assertEqual(c, {"action": "accept", "price": offer})
                    else:
                        self.assertEqual(c["action"], "counter")
                        self.assertTrue(floor <= c["price"] <= ask)

    def test_close_and_final_reply_follow_existing_rules(self):
        for stage in ("close", "final_offer"):
            for offer in (69000, 70000, 85000):
                c = capacity_choice({}, offer, 90000, {"stage": stage, "price_floor": 70000})
                self.assertEqual(c, {"action": "counter", "price": 70000} if offer < 70000 else
                                 {"action": "accept", "price": offer})
        self.assertEqual(capacity_choice({}, 69999, 70000, {"stage": "final_reply", "price_floor": 70000}),
                         {"action": "reject"})


class CapacityReceiptTests(unittest.TestCase):
    setUp = fixtures.InventoryTests.setUp
    write = fixtures.InventoryTests.write
    seller = fixtures.InventoryTests.seller

    def test_busy_open_and_counter_still_reach_verified_signed_receipt(self):
        seller, key = self.seller()
        seller.infer = AsyncMock(side_effect=ModelCapacityError("local slots full"))
        with TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            self.assertEqual(first["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            first_ask = seller.r3[first["id"]]["ask"]
            counter = send(client, "1", first["id"])
            self.assertEqual(counter["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            ask = seller.r3[first["id"]]["ask"]
            self.assertLessEqual(ask, first_ask)
            done = send(client, str(ask), first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        receipt = done["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(receipt["price"], ask)
        # A2A transports generic numeric data as doubles; match the kit's
        # existing integer normalization before signature verification.
        receipt["price"] = int(receipt["price"])
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), receipt))
        self.assertEqual(seller.infer.await_count, 2)  # Acceptance already has a code path.
        events = [json.loads(l) for l in seller.events.read_text().splitlines()]
        quotes = [e for e in events if e["outcome"] == "quote"]
        self.assertEqual(len(quotes), 2)
        self.assertTrue(all(e["decision_source"] == "capacity_fallback" for e in quotes))
        self.assertFalse(any(e["reason_code"] == "model_error" for e in events))

    def test_real_model_failure_is_not_hidden_by_fallback(self):
        seller, _ = self.seller()
        seller.infer = AsyncMock(side_effect=HarnessError("provider failed"))
        with patch("seller_r3.capacity_choice") as fallback, TestClient(build(DEFAULT, seller)) as client:
            failed = send(client, "G00001")
        fallback.assert_not_called()
        self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(seller.inventory.active)

    def test_inventory_expiry_after_busy_detection_still_blocks_publication(self):
        seller, _ = self.seller()
        async def busy(*args):
            self.write([fixtures.unit(kind="food", expires_at=time.time()-1)])
            raise ModelCapacityError("local slots full")
        seller.infer = busy
        with TestClient(build(DEFAULT, seller)) as client:
            failed = send(client, "G00001")
        self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(failed.get("artifacts"))
        self.assertFalse(seller.inventory.active)


class ThreeWorkerTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.InventoryTests.setUp
    write = fixtures.InventoryTests.write
    seller = fixtures.InventoryTests.seller

    async def test_fourth_request_quotes_without_worker_and_disconnect_does_not_free_slot(self):
        seller, _ = self.seller()
        release = threading.Event()
        started = [threading.Event() for _ in range(3)]
        def slow(index):
            started[index].set()
            release.wait(5)
            return index
        jobs = [asyncio.create_task(seller.infer(slow, i)) for i in range(3)]
        try:
            for _ in range(100):
                if all(e.is_set() for e in started):
                    break
                await asyncio.sleep(.01)
            self.assertTrue(all(e.is_set() for e in started))
            jobs[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await jobs[0]
            with self.assertRaises(ModelCapacityError):
                await seller.infer(lambda: self.fail("Fourth model must not start"))
            context = SimpleNamespace(current_task=None, message=Message(parts=[new_text_part("G00001")]))
            task = SimpleNamespace(id="overflow", context_id="c")
            with patch("seller_r3.new_task_from_user_message", return_value=task), \
                    patch("seller_r3.TaskUpdater") as updater, \
                    patch("seller_r3.inventory_decision") as model:
                up = updater.return_value
                up.requires_input = AsyncMock()
                up.failed = AsyncMock()
                await seller.execute(context, AsyncMock())
                up.requires_input.assert_awaited_once()
                up.failed.assert_not_called()
                model.assert_not_called()
            self.assertEqual(seller.r3["overflow"]["decision_source"], "capacity_fallback")
        finally:
            release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            for _ in range(100):
                if seller.model_slots._value == 3:
                    break
                await asyncio.sleep(.01)
        self.assertEqual(seller.model_slots._value, 3)
        self.assertEqual(await seller.infer(lambda: 7), 7)


if __name__ == "__main__":
    unittest.main()
