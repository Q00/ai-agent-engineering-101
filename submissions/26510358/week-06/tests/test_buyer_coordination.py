"""TEST ONLY stub transport/model; exercise real quote and buyer validation guards."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buyer
from evidence import EvidenceError, Ledger


class RecordingTrace:
    def __init__(self):
        self.events = []

    def write(self, event, **fields):
        self.events.append({"event": event, **fields})


class StubPolicy:
    sha256, version = "test-only-policy", "test-only"

    def choose(self, role, context):
        return "balanced"


def stub_quote():
    """Explicit synthetic wire fixture, normalized by the production quote adapter."""
    return {"id": "coordination-test-task", "status": {
        "state": "TASK_STATE_INPUT_REQUIRED", "message": {
            "parts": [{"text": "TEST ONLY stub: this lamp costs 120 credits."}],
            "metadata": {"ask": 120, "itemId": "lamp", "quoteId": "stub-quote",
                         "quantity": 1, "currency": "credits"}}}}


def walk_away():
    return {"action": "walk_away", "price": None, "item_id": "lamp", "offer": None,
            "reason": "TEST ONLY stub decision, no inference or transaction"}


class BuyerCoordinationTests(unittest.TestCase):
    def run_stub(self, coordination_context, decide, *, expected_error=None):
        calls = {"wire": [], "buyer_contexts": [], "observer_calls": 0}
        trace, ledger = RecordingTrace(), Ledger(120)

        class StubHarness:
            def __init__(self, role, _trace):
                self.role = role

            def decide(self, context, **kwargs):
                if self.role != "buyer":
                    calls["observer_calls"] += 1
                    raise AssertionError("Structured stub metadata needs no observation model")
                calls["buyer_contexts"].append(context)
                # Execute the real validator, as the production Harness does.
                return kwargs["validator"](decide(context))

        class StubClient:
            base = "http://coordination-stub.test"

            def __init__(self, *_args, **_kwargs):
                self.closed = False
                calls["client"] = self

            def send(self, text, task_id=None):
                calls["wire"].append({"text": text, "task_id": task_id})
                if task_id is not None:
                    raise AssertionError("These guard tests must never send a priced offer")
                return stub_quote()

            def close(self):
                self.closed = True

        result = None
        with patch.object(buyer, "Harness", StubHarness), \
                patch.object(buyer, "A2AClient", StubClient):
            if expected_error:
                with self.assertRaisesRegex(expected_error, "exceeds the configured budget"):
                    buyer.negotiate(StubClient.base, "lamp", 120, ledger=ledger,
                                    policy=StubPolicy(), trace=trace,
                                    coordination_context=coordination_context)
            else:
                result = buyer.negotiate(StubClient.base, "lamp", 120, ledger=ledger,
                                         policy=StubPolicy(), trace=trace,
                                         coordination_context=coordination_context)
        self.assertEqual(calls["observer_calls"], 0)
        self.assertEqual(len(calls["wire"]), 1)  # Opening request; zero priced offer sends.
        self.assertIsNone(calls["wire"][0]["task_id"])
        self.assertTrue(calls["client"].closed)
        self.assertEqual(ledger.snapshot()["purchases"], {})
        return result, calls

    def test_coordinator_budget_cannot_authorize_an_overbudget_offer(self):
        coordination = {"budget": 999, "available_budget": 999,
                        "slot_states": [{"item": "lamp", "budget": 999}]}

        def overbudget_decision(context):
            self.assertEqual(context["budget"], 120)
            self.assertEqual(context["ask"], 120)
            self.assertEqual(context["item"], "lamp")
            self.assertEqual(context["coordination"]["budget"], 999)
            return {"action": "offer", "price": 121, "item_id": "lamp", "offer": None,
                    "reason": "TEST ONLY deliberately invalid simulated model output"}

        _, calls = self.run_stub(coordination, overbudget_decision, expected_error=EvidenceError)
        self.assertEqual(len(calls["buyer_contexts"]), 1)

    def test_harness_mutation_does_not_change_original_nested_coordination(self):
        coordination = {"budget": 999, "slot_states": [
            {"item": "chair", "observations": {"asks": [200, 190]}}]}
        original = deepcopy(coordination)

        def mutating_decision(context):
            self.assertIsNot(context["coordination"], coordination)
            context["coordination"]["budget"] = 0
            context["coordination"]["slot_states"][0]["observations"]["asks"].append(1)
            context["coordination"]["new_key"] = "TEST ONLY mutation"
            return walk_away()

        result, _ = self.run_stub(coordination, mutating_decision)
        self.assertEqual(result["status"], "no_deal")
        self.assertEqual(coordination, original)

    def test_non_object_context_is_rejected_before_client_or_model_creation(self):
        for invalid in ([], "budget=999", 999, False):
            with self.subTest(context=invalid), \
                    patch.object(buyer, "A2AClient") as client, \
                    patch.object(buyer, "Harness") as harness:
                with self.assertRaisesRegex(EvidenceError, "factual object"):
                    buyer.negotiate("http://coordination-stub.test", "lamp", 120,
                                    policy=StubPolicy(), trace=RecordingTrace(),
                                    coordination_context=invalid)
                client.assert_not_called()
                harness.assert_not_called()

    def test_none_keeps_basic_buyer_context_without_coordination_field(self):
        def basic_decision(context):
            self.assertNotIn("coordination", context)
            self.assertEqual(context["budget"], 120)
            self.assertEqual(context["ask"], 120)
            self.assertEqual(context["item"], "lamp")
            return walk_away()

        result, calls = self.run_stub(None, basic_decision)
        self.assertEqual(result["status"], "no_deal")
        self.assertEqual(len(calls["buyer_contexts"]), 1)


if __name__ == "__main__":
    unittest.main()
