"""TEST ONLY simulated wire/model boundaries; the real receipt and ledger guards run."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buyer
from evidence import Ledger
from protocol import ProtocolError


class RecordingTrace:
    def __init__(self):
        self.events = []

    def write(self, event, **fields):
        self.events.append({"event": event, **fields})


class CountingLedger(Ledger):
    def __init__(self):
        super().__init__(120)
        self.commit_calls = 0

    def commit(self, *args, **kwargs):
        self.commit_calls += 1
        return super().commit(*args, **kwargs)


def quote():
    return {"id": "same-task", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
        "parts": [{"text": "TEST ONLY fixture. Asking 120."}],
        "metadata": {"ask": 120, "itemId": "lamp", "quoteId": "same-quote"}}}}


def terminal(state="TASK_STATE_COMPLETED", *, receipt=None):
    task = {"id": "same-task", "status": {"state": state}}
    if state == "TASK_STATE_COMPLETED":
        task["artifacts"] = [{"name": "receipt", "parts": [{"data": receipt or {
            "item": "lamp", "price": 115, "taskId": "same-task", "quoteId": "same-quote",
            "purchaseId": "remote-purchase-once"}}]}]
    return task


class BuyerRoundBoundaryTests(unittest.TestCase):
    def run_fixture(self, final_reply, *, expire_on_last=False, expected_error=None):
        trace, ledger, calls = RecordingTrace(), CountingLedger(), {"buyer": 0, "observer": 0, "wire": []}
        clock = [0]

        class SimulatedHarness:
            def __init__(self, role, _trace):
                self.role = role

            def decide(self, context, **kwargs):
                if self.role != "buyer":
                    calls["observer"] += 1
                    raise AssertionError("A structured quote must not add an observation-model call")
                calls["buyer"] += 1
                if calls["buyer"] > 8:
                    raise AssertionError("A ninth buyer decision must never run")
                decision = {"action": "offer", "price": 75 + calls["buyer"] * 5,
                            "item_id": "lamp", "offer": None,
                            "reason": "TEST ONLY simulated decision, not model inference"}
                return kwargs["validator"](decision)

        class SimulatedClient:
            base = "http://round-boundary.test"

            def __init__(self, *_args, **_kwargs):
                self.closed = False
                self.offers = 0
                calls["client"] = self

            def send(self, text, task_id=None):
                calls["wire"].append({"text": text, "task_id": task_id})
                if task_id is None:
                    return quote()
                self.offers += 1
                if self.offers < 8:
                    return quote()
                if expire_on_last:
                    clock[0] = 241
                if isinstance(final_reply, Exception):
                    raise final_reply
                return deepcopy(final_reply)

            def close(self):
                self.closed = True

        class SimulatedPolicy:
            sha256, version = "test-policy", "test-only"

            def choose(self, *_args):
                return "balanced"

        result = None
        with patch.object(buyer, "Harness", SimulatedHarness), \
                patch.object(buyer, "A2AClient", SimulatedClient), \
                patch.object(buyer.time, "monotonic", side_effect=lambda: clock[0]):
            if expected_error:
                with self.assertRaises(expected_error):
                    buyer.negotiate(SimulatedClient.base, "lamp", 120, ledger=ledger,
                                    policy=SimulatedPolicy(), trace=trace)
            else:
                result = buyer.negotiate(SimulatedClient.base, "lamp", 120, ledger=ledger,
                                         policy=SimulatedPolicy(), trace=trace)
        self.assertEqual(calls["buyer"], 8)
        self.assertEqual(calls["observer"], 0)
        self.assertEqual(len(calls["wire"]), 9)  # opening request + eight offers
        self.assertTrue(calls["client"].closed)
        self.assertEqual(json.loads(calls["wire"][-1]["text"]), 115)
        return result, ledger, trace

    def test_eighth_offer_completion_commits_once_and_stops(self):
        result, ledger, trace = self.run_fixture(terminal())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(ledger.commit_calls, 1)
        snapshot = ledger.snapshot()
        self.assertEqual((snapshot["committed_total"], snapshot["reserved_total"]), (115, 0))
        self.assertEqual(sum(p["state"] == "committed" for p in snapshot["purchases"].values()), 1)
        self.assertEqual(sum(e["event"] == "purchase_receipt_matched" for e in trace.events), 1)

    def test_received_final_completion_is_checked_even_if_deadline_just_expired(self):
        result, ledger, _ = self.run_fixture(terminal(), expire_on_last=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(ledger.commit_calls, 1)

    def test_eighth_counter_has_no_ninth_decision_and_releases_unused_reservation(self):
        result, ledger, _ = self.run_fixture(quote())
        self.assertEqual(result, {"status": "no_deal", "reason": "round limit"})
        snapshot = ledger.snapshot()
        self.assertEqual((snapshot["committed_total"], snapshot["reserved_total"], snapshot["available_budget"]),
                         (0, 0, 120))
        self.assertEqual(ledger.commit_calls, 0)

    def test_eighth_explicit_failure_releases_without_committing(self):
        result, ledger, _ = self.run_fixture(terminal("TASK_STATE_FAILED"))
        self.assertEqual((result["status"], result["state"], result["offer_count"]), ("no_deal", "TASK_STATE_FAILED", 8))
        self.assertEqual(ledger.snapshot()["available_budget"], 120)
        self.assertEqual(ledger.commit_calls, 0)

    def test_eighth_mismatched_receipt_keeps_disputed_funds(self):
        result, ledger, _ = self.run_fixture(terminal(receipt={"item": "lamp", "price": 116}))
        self.assertEqual(result["status"], "disputed")
        self.assertIn("price_mismatch", result["issues"])
        snapshot = ledger.snapshot()
        self.assertEqual((snapshot["committed_total"], snapshot["reserved_total"]), (0, 115))
        self.assertEqual(sum(p["state"] == "disputed" for p in snapshot["purchases"].values()), 1)
        self.assertEqual(ledger.commit_calls, 0)

    def test_eighth_lost_response_holds_funds_and_never_retries(self):
        _, ledger, _ = self.run_fixture(TimeoutError("TEST ONLY lost final response"),
                                      expected_error=TimeoutError)
        self.assertEqual(ledger.snapshot()["reserved_total"], 115)
        self.assertEqual(ledger.commit_calls, 0)

    def test_eighth_ambiguous_working_response_is_disputed_not_released(self):
        _, ledger, _ = self.run_fixture(terminal("TASK_STATE_WORKING"), expected_error=ProtocolError)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["reserved_total"], 115)
        self.assertEqual(sum(p["state"] == "disputed" for p in snapshot["purchases"].values()), 1)
        self.assertEqual(ledger.commit_calls, 0)


if __name__ == "__main__":
    unittest.main()
