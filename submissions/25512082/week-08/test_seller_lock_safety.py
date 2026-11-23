"""End-to-end lock safety of the seller over real A2A JSON-RPC (in-process, no network).

Each test drives seller.build_app through buyer.BOriginalClient, the same client the
buyer uses in the market. Scenarios from the 2026-11-10 operating review.
"""
from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from buyer import BOriginalClient, COMPLETED, INPUT_REQUIRED
from ledger import TradeLedger
from seller import OPEN_QUOTE_SECONDS, PENDING_RESERVATION_SECONDS, SafeSeller, build_app, canon

PENDING = "a2a_agreed_pending_market_settlement"
PORT = 9311


def unit(uid: str, sku: str = "G1", grade: str = "A") -> dict:
    return {"unit": uid, "sku": sku, "name": f"item {sku}", "category": "a>b>침구", "kind": "goods",
            "list_price": 20_000, "cost": 10_000, "grade": grade, "supplied_at": time.time(), "expires_at": None}


class SellerLockSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.key = Ed25519PrivateKey.generate()
        self.key_path = self.tmp / "shop_key.pem"
        self.key_path.write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.ledger_path = self.tmp / "seller-ledger.json"
        self.set_inventory([unit("u-1")])

    # helpers -------------------------------------------------------------
    def set_inventory(self, units: list[dict]) -> None:
        (self.tmp / "inventory.json").write_text(json.dumps({"round": 3, "storage_per_day": 0.01, "units": units}))

    def seller(self) -> SafeSeller:
        return SafeSeller(self.tmp, self.key_path, self.ledger_path)

    def client(self, executor: SafeSeller) -> BOriginalClient:
        app = build_app(PORT, self.tmp, self.key_path, self.ledger_path, executor=executor)
        return BOriginalClient(f"http://127.0.0.1:{PORT}", client=TestClient(app, base_url=f"http://127.0.0.1:{PORT}"))

    def quote(self, c: BOriginalClient, sku: str = "G1") -> dict:
        return c.send(f"I want to buy item {sku} ({sku})")

    @staticmethod
    def offer(c: BOriginalClient, task: dict, price: int | None = None) -> dict:
        """Follow-up in the same task and context, as market buyers send it."""
        price = BOriginalClient._ask(task) if price is None else price
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": str(price)}],
                   "taskId": task["id"], "contextId": task["contextId"]}
        return c.rpc("SendMessage", {"message": message})

    def buy(self, c: BOriginalClient, sku: str = "G1") -> dict:
        task = self.quote(c, sku)
        if task["status"]["state"] != INPUT_REQUIRED:
            return task
        return self.offer(c, task)

    def events(self) -> list[dict]:
        path = self.tmp / "seller-events.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def write_ledger(self, rows: list[dict]) -> None:
        self.ledger_path.write_text(json.dumps(rows))

    # 1 -------------------------------------------------------------------
    def test_expired_unpaid_agreement_is_sellable_again(self) -> None:
        old = time.time() - PENDING_RESERVATION_SECONDS - 5
        self.write_ledger([{"transaction_id": "t0", "order_id": "t0", "unit_id": "u-1", "status": PENDING,
                            "recorded_at": old, "rejection_reason": ""}])
        task = self.buy(self.client(self.seller()))
        self.assertEqual(task["status"]["state"], COMPLETED)
        self.assertEqual(BOriginalClient._receipt(task)["unit"], "u-1")

    def test_old_rejected_row_with_real_unit_id_does_not_lock(self) -> None:
        # the exact row shape that locked u-cb8b40190b30 (granola) in production
        self.write_ledger([{"transaction_id": "t0", "order_id": "t0", "unit_id": "u-1", "status": "rejected",
                            "rejection_reason": "no_price_agreement"}])
        self.assertEqual(self.buy(self.client(self.seller()))["status"]["state"], COMPLETED)

    # 2 -------------------------------------------------------------------
    def test_settled_unit_is_never_resold_even_after_window(self) -> None:
        old = time.time() - 10 * PENDING_RESERVATION_SECONDS
        self.write_ledger([{"transaction_id": "t0", "order_id": "t0", "unit_id": "u-1", "status": PENDING,
                            "recorded_at": old, "rejection_reason": ""}])
        self.set_inventory([])  # the market removed the settled unit; the kit rewrote inventory.json
        task = self.buy(self.client(self.seller()))
        self.assertNotEqual(task["status"]["state"], COMPLETED)
        self.assertEqual(TradeLedger(self.ledger_path).rows[-1]["rejection_reason"], "no_verified_inventory")

    def test_unit_sold_between_quote_and_agreement_is_refused(self) -> None:
        seller = self.seller()
        c = self.client(seller)
        task = self.quote(c)
        self.set_inventory([])  # sold elsewhere / seized / expired after the quote
        done = self.offer(c, task)
        self.assertNotEqual(done["status"]["state"], COMPLETED)
        self.assertEqual(TradeLedger(self.ledger_path).rows[-1]["rejection_reason"], "unit_not_in_inventory")

    # 3 -------------------------------------------------------------------
    def test_unit_in_checkout_is_not_offered_to_a_second_buyer(self) -> None:
        seller = self.seller()
        first = self.buy(self.client(seller))
        self.assertEqual(first["status"]["state"], COMPLETED)  # agreed, waiting for the kit's checkout
        second = self.quote(self.client(seller))
        self.assertNotEqual(second["status"]["state"], INPUT_REQUIRED)
        self.assertEqual(TradeLedger(self.ledger_path).rows[-1]["rejection_reason"], "no_verified_inventory")

    # 4 -------------------------------------------------------------------
    def test_two_concurrent_buyers_only_one_gets_the_unit(self) -> None:
        seller = self.seller()
        a, b = self.client(seller), self.client(seller)
        qa = self.quote(a)
        qb = self.quote(b)
        self.assertEqual(qa["status"]["state"], INPUT_REQUIRED)
        self.assertNotEqual(qb["status"]["state"], INPUT_REQUIRED)  # unit held by a's open quote

    def test_stale_quote_race_still_signs_only_one_receipt(self) -> None:
        seller = self.seller()
        a, b = self.client(seller), self.client(seller)
        qa = self.quote(a)
        seller.open[qa["id"]]["opened_at"] -= OPEN_QUOTE_SECONDS + 1  # a walked away for > 10 min
        qb = self.quote(b)
        self.assertEqual(qb["status"]["state"], INPUT_REQUIRED)  # unit released to b
        done_b = self.offer(b, qb)
        done_a = self.offer(a, qa)  # a comes back late
        states = sorted([done_a["status"]["state"], done_b["status"]["state"]])
        self.assertEqual(states.count(COMPLETED), 1)
        agreed = [r for r in TradeLedger(self.ledger_path).rows if r["status"] == PENDING]
        self.assertEqual(len(agreed), 1)

    # 5 -------------------------------------------------------------------
    def test_resent_offer_does_not_produce_a_second_agreement(self) -> None:
        seller = self.seller()
        c = self.client(seller)
        task = self.quote(c)
        self.offer(c, task)
        try:
            self.offer(c, task)  # same task, same price, sent again after it completed
        except Exception:
            pass  # the A2A server refuses messages to a terminal task
        agreed = [r for r in TradeLedger(self.ledger_path).rows if r["status"] == PENDING]
        self.assertEqual(len(agreed), 1)
        self.assertEqual(len(seller.issued), 1)

    # 6 -------------------------------------------------------------------
    def test_restart_keeps_fresh_reservation_and_settled_ban(self) -> None:
        self.set_inventory([unit("u-1"), unit("u-2")])
        self.assertEqual(self.buy(self.client(self.seller()))["status"]["state"], COMPLETED)  # u-1 agreed
        self.set_inventory([unit("u-2")])  # u-1 settled and left inventory
        restarted = self.seller()           # new process: empty memory, same ledger file
        receipt = BOriginalClient._receipt(self.buy(self.client(restarted)))
        self.assertEqual(receipt["unit"], "u-2")
        self.set_inventory([unit("u-1"), unit("u-2")])  # even if a stale file still listed u-1...
        third = self.buy(self.client(self.seller()))
        self.assertNotEqual(third["status"]["state"], COMPLETED)  # ...both are freshly reserved

    # 7 -------------------------------------------------------------------
    def test_sku_not_in_official_inventory_is_never_sold(self) -> None:
        task = self.buy(self.client(self.seller()), sku="G999")
        self.assertNotEqual(task["status"]["state"], COMPLETED)

    # 8 -------------------------------------------------------------------
    def test_receipt_signature_still_verifies(self) -> None:
        receipt = BOriginalClient._receipt(self.buy(self.client(self.seller())))
        body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
        self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon(body))
        self.assertEqual(receipt["claim"], "A")


if __name__ == "__main__":
    unittest.main()
