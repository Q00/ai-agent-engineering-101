"""Agent-level tests for the A2A server card and buyer task continuation."""

from __future__ import annotations

import unittest

from buyer import BuyerRunner
from harness import Decision
from seller import build_card, find_item


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
        "artifacts": [{"name": "receipt", "parts": [{"data": {"price": price}}]}],
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
