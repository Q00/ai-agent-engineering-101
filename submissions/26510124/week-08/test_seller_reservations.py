"""Reservation cleanup and failure evidence: no live market/model calls."""
import asyncio
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from a2a.helpers import new_text_part
from a2a.types import Message
from starlette.testclient import TestClient

from inventory import Inventory, StockError
from seller_agent import DEFAULT, build
from test_adaptive_seller import send
import test_inventory as fixtures
from test_inventory import unit


class SellerReservationTests(unittest.TestCase):
    setUp = fixtures.InventoryTests.setUp
    write = fixtures.InventoryTests.write
    seller = fixtures.InventoryTests.seller

    def events(self, seller):
        return [json.loads(line) for line in seller.events.read_text().splitlines()]

    def test_hex_sku_never_falls_back_to_wrong_category_product(self):
        self.write([unit(sku="G1515d951")])
        stock = Inventory(self.root)
        with self.assertRaises(StockError) as error:
            stock.reserve("wrong", "의자 (Gabcdef12)")
        self.assertEqual(error.exception.sku, "GABCDEF12")
        self.assertEqual(error.exception.reason_code, "stock_unavailable")
        self.assertEqual(stock.reserve("right", "의자 (G1515d951)")["sku"], "G1515d951")

    def test_reserved_stock_distinguishes_negotiating_and_issued_receipts(self):
        stock = Inventory(self.root)
        stock.reserve("first", "G00001")
        with self.assertRaises(StockError) as pending:
            stock.reserve("second", "G00001")
        self.assertEqual(pending.exception.reason_code, "stock_reserved")
        self.assertEqual(pending.exception.reservation_kind, "negotiation")
        stock.issue("first")
        with self.assertRaises(StockError) as issued:
            Inventory(self.root).reserve("second", "G00001")
        self.assertEqual(issued.exception.reservation_kind, "receipt")

    def test_invalid_terminal_followup_releases_reservation(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", return_value={"action": "counter", "price": 90000}), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            failed = send(client, "x" * 4001, first["id"])
            self.assertEqual(failed["status"]["state"], "TASK_STATE_REJECTED")
            self.assertFalse(seller.inventory.active)
            self.assertFalse(seller.r3)
            next_buyer = send(client, "G00001")
            self.assertEqual(next_buyer["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        failure = next(e for e in self.events(seller) if e.get("reason_code") == "invalid_request")
        self.assertTrue(failure["terminal"])
        self.assertEqual(failure["round"], 3)
        self.assertEqual(failure["sku"], "G00001")

    def test_busy_and_no_stock_events_do_not_invent_price_failure(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", return_value={"action": "counter", "price": 90000}), \
                TestClient(build(DEFAULT, seller)) as client:
            send(client, "G00001")
            send(client, "G00001")
            send(client, "Gabcdef12 의자")
        failures = [e for e in self.events(seller) if e["outcome"] == "stock_unavailable"]
        self.assertEqual([e["reason_code"] for e in failures], ["stock_reserved", "stock_unavailable"])
        self.assertEqual([e["sku"] for e in failures], ["G00001", "GABCDEF12"])
        self.assertEqual(failures[0]["reservation_kind"], "negotiation")
        self.assertTrue(all(e["round"] == 3 and len(e["detail"]) <= 240 for e in failures))

    def test_stale_inventory_failure_is_not_an_out_of_stock_sale(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", return_value={"action": "counter", "price": 90000}), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            old = time.time() - 60
            os.utime(self.root / "inventory.json", (old, old))
            failed = send(client, "90000", first["id"])
        self.assertEqual(failed["status"]["state"], "TASK_STATE_REJECTED")
        self.assertFalse(failed.get("artifacts"))
        self.assertFalse(seller.inventory.active)
        self.assertFalse(seller.inventory.holds)
        self.assertFalse(seller.r3)
        event = self.events(seller)[-1]
        self.assertEqual(event["reason_code"], "inventory_unavailable")
        self.assertEqual((event["round"], event["sku"]), (3, "G00001"))

    def test_corrupt_state_and_unreadable_reservations_are_inventory_errors(self):
        for contents in ("{broken", '{"round": 3}', '{"round": 3, "units": [null]}'):
            with self.subTest(contents=contents):
                (self.root / "inventory.json").write_text(contents)
                with self.assertRaises(StockError) as error:
                    Inventory(self.root).reserve("attempt", "G00001")
                self.assertEqual(error.exception.reason_code, "inventory_unavailable")
        for contents in ("{broken", '[]'):
            (self.root / "receipt-reservations.json").write_text(contents)
            with self.assertRaises(StockError) as error:
                Inventory(self.root)
            self.assertEqual(error.exception.reason_code, "inventory_unavailable")
            self.assertEqual((self.root / "receipt-reservations.json").read_text(), contents)
        (self.root / "receipt-reservations.json").unlink()
        self.write([unit()])
        stock = Inventory(self.root)
        stock.reserve("first", "G00001")
        with patch("inventory.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(StockError) as error:
                stock.issue("first")
        self.assertEqual(error.exception.reason_code, "inventory_unavailable")
        self.assertFalse(stock.holds)

    def test_expiry_cleans_idle_negotiation_and_lock_but_never_receipt_hold(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", return_value={"action": "counter", "price": 90000}), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            seller.r3[first["id"]]["touched"] = time.time() - 301
            seller.locks[first["id"]] = asyncio.Lock()
            next_buyer = send(client, "G00001")
            self.assertEqual(next_buyer["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertNotIn(first["id"], seller.locks)
            self.assertNotIn(first["id"], seller.r3)
            seller.inventory.issue(next_buyer["id"])
            seller.r3.pop(next_buyer["id"])
            seller.expire_negotiations()
        self.assertEqual(seller.inventory.holds, {"u1": next_buyer["id"]})
        expiry = next(e for e in self.events(seller) if e["outcome"] == "expired")
        self.assertEqual(expiry["reason_code"], "negotiation_expired")

    def test_unexpected_preparation_and_model_errors_release_active_hold(self):
        for location in ("opening", "model"):
            with self.subTest(location=location):
                seller, _ = self.seller()
                fail = (patch.object(seller.openings, "for_product", side_effect=RuntimeError("broken"))
                        if location == "opening" else
                        patch("seller_r3.inventory_decision", side_effect=RuntimeError("broken")))
                with fail, TestClient(build(DEFAULT, seller)) as client:
                    failed = send(client, "G00001")
                self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
                self.assertFalse(seller.inventory.active)
                self.assertFalse(seller.r3)
                self.assertFalse(seller.pending)
                self.assertFalse(seller.locks)
                self.assertEqual(self.events(seller)[-1]["reason_code"],
                                 "model_error" if location == "model" else "internal_error")

    def test_final_price_rejection_is_explicit_and_releases_unit(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", return_value={"action": "counter", "price": 90000}), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            seller.r3[first["id"]]["final_quote"] = True
            ended = send(client, "70000", first["id"])
        self.assertEqual(ended["status"]["state"], "TASK_STATE_REJECTED")
        self.assertFalse(seller.inventory.active)
        self.assertEqual(self.events(seller)[-1]["reason_code"], "price_rejected")

    def test_pending_malformed_request_does_not_free_active_reservation(self):
        seller, _ = self.seller()
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            async def slow(*_):
                entered.set()
                await release.wait()
                return {"action": "counter", "price": 90000}
            seller.infer = slow
            task = SimpleNamespace(id="concurrent", context_id="c")
            context = SimpleNamespace(current_task=None, message=Message(parts=[new_text_part("G00001")]))
            with patch("seller_r3.new_task_from_user_message", return_value=task), \
                    patch("seller_r3.TaskUpdater") as updater:
                up = updater.return_value
                up.requires_input = AsyncMock()
                up.reject = AsyncMock()
                job = asyncio.create_task(seller.execute(context, AsyncMock()))
                await entered.wait()
                duplicate = SimpleNamespace(current_task=task, message=Message(parts=[new_text_part("x" * 4001)]))
                await seller.execute(duplicate, AsyncMock())
                self.assertEqual(seller.inventory.active, {task.id: "u1"})
                up.reject.assert_not_awaited()
                release.set()
                await job
        asyncio.run(scenario())
        self.assertTrue(any(e["reason_code"] == "request_in_progress" and e["terminal"] is False
                            for e in self.events(seller)))

    def test_response_failure_after_receipt_persistence_never_resells_unit(self):
        seller, _ = self.seller()
        async def scenario():
            task = SimpleNamespace(id="receipt", context_id="c")
            with patch("seller_r3.new_task_from_user_message", return_value=task), \
                    patch("seller_r3.TaskUpdater") as updater:
                up = updater.return_value
                up.requires_input = AsyncMock()
                up.add_artifact = AsyncMock(side_effect=RuntimeError("closed connection"))
                up.failed = AsyncMock()
                seller.infer = AsyncMock(return_value={"action": "counter", "price": 90000})
                await seller.execute(SimpleNamespace(current_task=None, message=Message(parts=[new_text_part("G00001")])), AsyncMock())
                await seller.execute(SimpleNamespace(current_task=task, message=Message(parts=[new_text_part("90000")])), AsyncMock())
                self.assertFalse(seller.inventory.active)
                self.assertFalse(seller.r3)
                self.assertFalse(seller.pending)
                self.assertFalse(seller.locks)
        asyncio.run(scenario())
        self.assertEqual(seller.inventory.holds, {"u1": "receipt"})
        with self.assertRaises(StockError):
            Inventory(self.root).reserve("later", "G00001")
        self.assertEqual([e["outcome"] for e in self.events(seller)][-2:], ["receipt_issued", "delivery_error"])
        self.assertFalse(self.events(seller)[-1]["terminal"])

    def test_disconnect_before_receipt_releases_active_hold(self):
        seller, _ = self.seller()
        async def scenario():
            entered = asyncio.Event()
            async def slow(*_):
                entered.set()
                await asyncio.Event().wait()
            seller.infer = slow
            task = SimpleNamespace(id="disconnected", context_id="c")
            with patch("seller_r3.new_task_from_user_message", return_value=task), patch("seller_r3.TaskUpdater"):
                context = SimpleNamespace(current_task=None, message=Message(parts=[new_text_part("G00001")]))
                job = asyncio.create_task(seller.execute(context, AsyncMock()))
                await entered.wait()
                job.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await job
        asyncio.run(scenario())
        self.assertFalse(seller.inventory.active)
        self.assertFalse(seller.inventory.holds)
        self.assertFalse(seller.r3)
        self.assertFalse(seller.pending)
        self.assertFalse(seller.locks)
        self.assertEqual(self.events(seller)[-1]["reason_code"], "buyer_cancelled")
