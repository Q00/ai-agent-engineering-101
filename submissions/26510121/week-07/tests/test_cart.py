"""Offline adversarial cases for concurrent reservations and purchase uncertainty."""
import asyncio
import json
import unittest

from negotiation.a2a_client import ProtocolError
from negotiation.cart_contracts import CartContext, CartDecision, parse_cart_decision
from negotiation.cart_state import CartLedger
from negotiation.contracts import DecisionError
from negotiation.state import BuyerMemory


class MemoryLog:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})


def line(key="a", item="lamp"):
    return {"line_id": key, "item_id": item, "category": item, "description": "One requested product."}


def plan(*rows):
    return CartDecision(action="plan", assignments=[{"line_id": key, "shop_id": shop, "budget": cap}
                                                     for key, shop, cap in rows],
                        reason="Allocate within the total.", summary="Plan awaiting execution.")


class CartContractTests(unittest.TestCase):
    def setUp(self):
        self.context = CartContext(100, [{**line(), "shops": [{"shop_id": "one"}, {"shop_id": "two"}]}])

    def test_model_caps_and_shops_are_preserved(self):
        for cap in (71, 99):
            decision = parse_cart_decision(plan(("a", "two", cap)).model_dump_json(), self.context)
            self.assertEqual((decision.assignments[0].shop_id, decision.assignments[0].budget), ("two", cap))

    def test_invalid_allocation_is_not_clamped_or_rerouted(self):
        for value in (plan(("a", "one", 101)), plan(("a", "one", 0)),
                      plan(("a", "fake", 90)), plan(("a", "one", 50), ("a", "two", 50)),
                      plan(("other", "one", 90))):
            with self.assertRaises(DecisionError):
                parse_cart_decision(value.model_dump_json(), self.context)

    def test_boolean_cap_and_duplicate_json_keys_are_rejected(self):
        raw = plan(("a", "one", 90)).model_dump()
        raw["assignments"][0]["budget"] = True
        with self.assertRaises(DecisionError):
            parse_cart_decision(json.dumps(raw), self.context)
        with self.assertRaisesRegex(DecisionError, "duplicate_json_key"):
            parse_cart_decision('{"action":"stop","action":"plan"}', self.context)

    def test_stop_cannot_carry_purchase_assignments(self):
        raw = plan(("a", "one", 90)).model_dump()
        raw["action"] = "stop"
        with self.assertRaisesRegex(DecisionError, "stop_requires_empty_assignments"):
            parse_cart_decision(json.dumps(raw), self.context)


class CartLedgerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.log = MemoryLog()
        self.ledger = CartLedger([line(), line("b", "chair")], 100, self.log)

    async def active(self, cap=80):
        await self.ledger.reserve(plan(("a", "one", cap)))
        await self.ledger.begin("a")
        await self.ledger.remember("a", BuyerMemory("task-a", "context-a"))

    def test_duplicate_item_is_rejected_even_with_distinct_line_ids(self):
        with self.assertRaises(ValueError):
            CartLedger([line(), line("different", "lamp")], 100, self.log)

    async def test_concurrent_reservations_cannot_each_spend_the_same_total(self):
        results = await asyncio.gather(self.ledger.reserve(plan(("a", "one", 80))),
                                       self.ledger.reserve(plan(("b", "two", 80))), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ProtocolError) for result in results), 1)
        self.assertEqual((self.ledger.held, self.ledger.available), (80, 20))
        self.assertTrue(all(e["ledger"]["available"] >= 0 for e in self.log.events))

    async def test_same_line_cannot_start_twice_under_race(self):
        await self.ledger.reserve(plan(("a", "one", 80)))
        results = await asyncio.gather(self.ledger.begin("a"), self.ledger.begin("a"), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ProtocolError) for result in results), 1)
        self.assertEqual(self.ledger.lines["a"].attempted_shops, ["one"])

    async def test_verified_purchase_releases_only_unused_reservation(self):
        await self.active()
        result = {"outcome": "purchased", "task_id": "task-a", "receipt": {"item": "lamp", "price": 65}}
        await self.ledger.finish("a", result)
        self.assertEqual((self.ledger.spent, self.ledger.held, self.ledger.available), (65, 0, 35))
        with self.assertRaises(ProtocolError):
            await self.ledger.finish("a", result)
        with self.assertRaises(ProtocolError):
            await self.ledger.reserve(plan(("a", "two", 30)))
        self.assertEqual(self.ledger.spent, 65)

    async def test_unknown_purchase_holds_cap_and_blocks_another_attempt(self):
        await self.active()
        await self.ledger.uncertain("a", "offer_outcome_unknown")
        self.assertEqual((self.ledger.spent, self.ledger.held, self.ledger.available), (0, 80, 20))
        with self.assertRaises(ProtocolError):
            await self.ledger.reserve(plan(("a", "two", 20)))
        with self.assertRaises(ProtocolError):
            await self.ledger.reserve(plan(("b", "two", 21)))
        self.assertEqual(self.ledger.result()["outcome"], "incomplete")

    async def test_confirmed_cancellation_releases_cap_for_a_different_shop(self):
        await self.active()
        await self.ledger.finish("a", {"outcome": "stopped", "task_id": "task-a", "state": "TASK_STATE_CANCELED"})
        self.assertEqual(self.ledger.available, 100)
        with self.assertRaises(ProtocolError):
            await self.ledger.reserve(plan(("a", "one", 80)))
        await self.ledger.reserve(plan(("a", "two", 70)))
        self.assertEqual(self.ledger.lines["a"].shop_id, "two")

    async def test_wrong_receipt_and_task_do_not_release_or_spend_money(self):
        await self.active()
        for result in ({"outcome": "purchased", "task_id": "wrong", "receipt": {"item": "lamp", "price": 65}},
                       {"outcome": "purchased", "task_id": "task-a", "receipt": {"item": "chair", "price": 65}},
                       {"outcome": "purchased", "task_id": "task-a", "receipt": {"item": "lamp", "price": 81}},
                       {"outcome": "stopped", "task_id": "task-a", "state": "TASK_STATE_INPUT_REQUIRED"}):
            with self.assertRaises(ProtocolError):
                await self.ledger.finish("a", result)
            self.assertEqual((self.ledger.spent, self.ledger.held), (0, 80))

    async def test_task_id_cannot_be_replaced_by_another_shops_memory(self):
        await self.active()
        with self.assertRaises(ProtocolError):
            await self.ledger.remember("a", BuyerMemory("task-b", "context-b"))
        self.assertEqual(self.ledger.lines["a"].task_id, "task-a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
