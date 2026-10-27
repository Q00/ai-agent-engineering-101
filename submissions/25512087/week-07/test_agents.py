"""Agent-level tests for the A2A server card and buyer task continuation."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from buyer import BuyerRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from harness import Decision
from seller import build_card, canon, find_item, sign_receipt


def input_required(task_id: str, ask: int) -> dict:
    return {
        "id": task_id,
        "status": {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "message": {"parts": [{"text": f"asking {ask}"}]},
        },
    }


def completed(task_id: str, price: int) -> dict:
    return {
        "id": task_id,
        "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": []}},
        "artifacts": [{"name": "receipt", "parts": [{"data": {
            "shop": "Kit Shop", "item": "lamp", "price": price, "task": task_id, "sig_seller": "c2ln"}}]}],
    }


class ScriptedClient:
    def __init__(self, *tasks: dict) -> None:
        self.tasks = list(tasks)
        self.calls: list[tuple[str, str | None]] = []

    def discover(self) -> dict:
        return {"name": "Kit Shop"}

    def send(self, text: str, task_id: str | None = None) -> dict:
        self.calls.append((text, task_id))
        return self.tasks.pop(0)


class ScriptedBuyerPolicy:
    def __init__(self, *decisions: Decision) -> None:
        self.decisions = list(decisions)

    def respond(self, item: str, budget: int, ask: int, rounds: int) -> Decision:
        return self.decisions.pop(0)


class SellerAgentTests(unittest.TestCase):
    def test_card_advertises_a2a_v1_jsonrpc(self) -> None:
        card = build_card(8765)
        interface = card.supported_interfaces[0]
        self.assertEqual(interface.protocol_binding, "JSONRPC")
        self.assertEqual(interface.protocol_version, "1.0")
        self.assertEqual(interface.url, "http://127.0.0.1:8765/")
        self.assertTrue(card.skills)

    def test_finds_supported_item_and_rejects_unknown(self) -> None:
        self.assertEqual(find_item("I need a BOOKSHELF"), "bookshelf")
        self.assertIsNone(find_item("I need a sofa"))

    def test_receipt_is_signed_over_market_fields(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        with TemporaryDirectory() as directory:
            key_path = Path(directory) / "shop_key.pem"
            key_path.write_bytes(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            receipt = sign_receipt(
                {"shop": "ggari0703", "item": "lamp", "price": 100, "task": "t-1"},
                key_path,
            )
        signature = __import__("base64").b64decode(receipt["sig_seller"])
        body = {key: receipt[key] for key in ("shop", "item", "price", "task")}
        private_key.public_key().verify(signature, canon(body))


class BuyerAgentTests(unittest.TestCase):
    def test_continues_same_task_and_completes_under_budget(self) -> None:
        client = ScriptedClient(input_required("task-1", 130), completed("task-1", 110))
        policy = ScriptedBuyerPolicy(Decision("offer", 110, "close"))
        result = BuyerRunner(client, policy, max_rounds=4).run("lamp", 120)
        self.assertEqual(client.calls, [("I want to buy a lamp", None), ("110", "task-1")])
        self.assertEqual(result["outcome"], "deal")
        self.assertEqual(result["price"], 110)

    def test_walks_without_sending_over_budget_offer(self) -> None:
        client = ScriptedClient(input_required("task-2", 338))
        policy = ScriptedBuyerPolicy(Decision("walk", None, "too expensive"))
        result = BuyerRunner(client, policy, max_rounds=4).run("bookshelf", 230)
        self.assertEqual(client.calls, [("I want to buy a bookshelf", None)])
        self.assertEqual(result["outcome"], "walk")


if __name__ == "__main__":
    unittest.main()
