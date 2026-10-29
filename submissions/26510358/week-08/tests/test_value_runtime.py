"""One optional value counter, private context and real contract guards."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "week-07"))
import buyer
from evidence import EvidenceError


class Trace:
    def write(self, *_args, **_kwargs):
        pass


class ValueRuntime(unittest.TestCase):
    def run_case(self, second_action):
        wire, contexts = [], []
        quote = {"id": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
            "parts": [{"text": "lamp at 110"}], "metadata": {"ask": 110, "itemId": "lamp", "quoteId": "q1"}}}}

        class Client:
            base = "https://shop.test"
            def __init__(self, *_args, **_kwargs):
                pass
            def send(self, text, task_id=None):
                wire.append(text)
                if text == "110":
                    return {"id": "task-1", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [
                        {"parts": [{"data": {"item": "lamp", "price": 110, "task": "task-1",
                            "quoteId": "q1", "purchaseId": "purchase-1"}}]}]}
                return deepcopy(quote)
            def close(self):
                pass

        class Harness:
            def __init__(self, *_args):
                pass
            def decide(self, context, **kwargs):
                contexts.append(deepcopy(context))
                first = len(contexts) == 1
                action, price = ("offer", 100) if first else (second_action, 110 if second_action == "accept" else 100)
                return kwargs["validator"]({"action": action, "price": price, "item_id": "lamp", "offer": None,
                                             "reason": "TEST ONLY private reference"})

        with patch.object(buyer, "A2AClient", Client), patch.object(buyer, "Harness", Harness):
            if second_action == "offer":
                with self.assertRaises(EvidenceError):
                    buyer.negotiate(Client.base, "lamp", 120, trace=Trace(), value_evidence={"private": "REFERENCE"})
            else:
                result = buyer.negotiate(Client.base, "lamp", 120, trace=Trace(), value_evidence={"private": "REFERENCE"})
                self.assertEqual(result["status"], "completed")
        self.assertTrue(contexts[0]["value_counter_available"])
        self.assertFalse(contexts[1]["value_counter_available"])
        self.assertEqual(contexts[0]["purchase_value"]["private"], "REFERENCE")
        self.assertNotIn("REFERENCE", json.dumps(wire))
        return wire

    def test_one_counter_can_finish_on_same_task_without_revealing_context(self):
        self.assertEqual(self.run_case("accept"), ["I want to buy a lamp", "100", "110"])

    def test_second_discretionary_counter_is_rejected_before_transmission(self):
        self.assertEqual(self.run_case("offer"), ["I want to buy a lamp", "100"])


if __name__ == "__main__":
    unittest.main()
