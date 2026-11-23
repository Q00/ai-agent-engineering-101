"""Behavioral contract for the Week 06 buyer, seller, and required challenges."""

from __future__ import annotations

import unittest

from catalog import CatalogIndex, Product, catalog_prompt
from directory import AgentSummary, BudgetLedger, DirectoryIndex, NegotiationSummary
from harness import Decision, HarnessResponseError, parse_decision
from policy import BuyerPolicy, SellerPolicy
from protocol import build_send_message, rpc_request


class FakeHarness:
    """Return scripted decisions while retaining prompts for assertions."""

    def __init__(self, *decisions: Decision) -> None:
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    def decide(self, prompt: str, fallback: Decision) -> Decision:
        self.prompts.append(prompt)
        return self.decisions.pop(0) if self.decisions else fallback


class HarnessContractTests(unittest.TestCase):
    def test_parses_fenced_json(self) -> None:
        decision = parse_decision(
            '```json\n{"action":"offer","price":110,"reason":"counter"}\n```'
        )
        self.assertEqual(decision, Decision("offer", 110, "counter"))

    def test_rejects_non_json_output(self) -> None:
        with self.assertRaises(HarnessResponseError):
            parse_decision("I would probably offer 110")


class SellerPolicyTests(unittest.TestCase):
    def test_opening_ask_cannot_go_below_reserve(self) -> None:
        policy = SellerPolicy(FakeHarness(Decision("counter", 70, "cheap")))
        decision = policy.opening("lamp", reserve=100)
        self.assertEqual(decision.action, "counter")
        self.assertEqual(decision.price, 100)

    def test_low_offer_cannot_be_accepted(self) -> None:
        policy = SellerPolicy(FakeHarness(Decision("accept", 90, "unsafe")))
        decision = policy.respond("lamp", reserve=100, ask=130, offer=90, rounds=1)
        self.assertEqual(decision.action, "counter")
        self.assertGreaterEqual(decision.price or 0, 100)

    def test_valid_offer_can_be_accepted_by_harness(self) -> None:
        policy = SellerPolicy(FakeHarness(Decision("accept", None, "valid")))
        decision = policy.respond("lamp", reserve=100, ask=110, offer=110, rounds=2)
        self.assertEqual(decision, Decision("accept", 110, "valid"))


class BuyerPolicyTests(unittest.TestCase):
    def test_offer_is_clamped_to_budget(self) -> None:
        policy = BuyerPolicy(FakeHarness(Decision("offer", 500, "unsafe")))
        decision = policy.respond("lamp", budget=120, ask=130, rounds=1)
        self.assertEqual(decision, Decision("offer", 120, "unsafe"))

    def test_buyer_cannot_accept_above_budget(self) -> None:
        policy = BuyerPolicy(FakeHarness(Decision("accept", 150, "unsafe")))
        decision = policy.respond("lamp", budget=120, ask=150, rounds=1)
        self.assertEqual(decision.action, "walk")


class ProtocolTests(unittest.TestCase):
    def test_follow_up_message_carries_task_id(self) -> None:
        message = build_send_message("110", task_id="task-123", message_id="msg-1")
        self.assertEqual(message["taskId"], "task-123")
        self.assertEqual(message["parts"], [{"text": "110"}])

    def test_rpc_request_uses_a2a_v1_method(self) -> None:
        payload, headers = rpc_request("hello", request_id="rpc-1")
        self.assertEqual(payload["method"], "SendMessage")
        self.assertEqual(headers["A2A-Version"], "1.0")


class CatalogChallengeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.products = [
            Product("p1", "Oak bookshelf", "wooden storage for about 40 books", 260),
            Product("p2", "Steel lamp", "bright adjustable desk light", 100),
            Product("p3", "Office chair", "mesh chair with lumbar support", 180),
        ]

    def test_search_narrows_description_to_relevant_product(self) -> None:
        matches = CatalogIndex(self.products).search(
            "something wooden to keep about 40 books, not taller than my desk",
            limit=2,
        )
        self.assertEqual(matches[0].product.product_id, "p1")

    def test_prompt_contains_only_narrowed_candidates(self) -> None:
        matches = CatalogIndex(self.products).search("wooden books", limit=1)
        prompt = catalog_prompt("wooden books", matches)
        self.assertIn("Oak bookshelf", prompt)
        self.assertNotIn("Steel lamp", prompt)


class DirectoryChallengeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = [
            AgentSummary(
                name=f"Shop {number}",
                url=f"http://shop-{number}.test",
                description=("lighting lamps" if number % 2 == 0 else "books shelves"),
                skills=("negotiate", "lamp" if number % 2 == 0 else "bookshelf"),
            )
            for number in range(20)
        ]

    def test_directory_filters_twenty_cards_before_harness(self) -> None:
        candidates = DirectoryIndex(self.cards).search("desk lamp", limit=4)
        self.assertEqual(len(candidates), 4)
        self.assertTrue(all("lamp" in card.skills for card in candidates))

    def test_budget_ledger_prevents_duplicate_and_overspend(self) -> None:
        ledger = BudgetLedger(total_budget=300)
        ledger.record("lamp", "Shop 2", 100)
        with self.assertRaises(ValueError):
            ledger.record("lamp", "Shop 4", 90)
        with self.assertRaises(ValueError):
            ledger.record("bookshelf", "Shop 1", 260)

    def test_negotiation_summary_is_bounded(self) -> None:
        summary = NegotiationSummary("lamp", "Shop 2")
        for number in range(50):
            summary.add("seller", f"counteroffer {number}: " + "x" * 100)
        rendered = summary.render(max_events=4)
        self.assertLess(len(rendered), 700)
        self.assertIn("counteroffer 49", rendered)
        self.assertNotIn("counteroffer 1:", rendered)


if __name__ == "__main__":
    unittest.main()
