"""TEST ONLY wire/model fixtures: a terminal label cannot erase purchase evidence."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buyer
from evidence import Ledger


STATES = ("TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED")
RECEIPT = {"item": "lamp", "price": 110, "purchaseId": "possibly-sold", "taskId": "task-1"}


class TestPolicy:
    sha256, version = "TEST_ONLY", "TEST_ONLY"

    def choose(self, *args):
        return "balanced"


class SilentTrace:
    def write(self, *args, **kwargs):
        pass


def quoted():
    return {"id": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
        "metadata": {"ask": 120, "itemId": "lamp"}, "parts": [{"text": "Current ask 120"}]}}}


def terminal(state, artifacts):
    return {"id": "task-1", "status": {"state": state}, "artifacts": deepcopy(artifacts)}


def authorize_offer(_instance, context, **kwargs):
    return kwargs["validator"]({"action": "offer", "price": 110, "item_id": "lamp",
                                "offer": None, "stance": None, "reason": "TEST ONLY judgment"})


class TerminalReceiptConflictTests(unittest.TestCase):
    def trade(self, final, *, unsolicited=False):
        client = Mock(base="http://TEST-ONLY.invalid")
        client.send.side_effect = [final] if unsolicited else [quoted(), final]
        ledger = Ledger(120)
        with patch("buyer.A2AClient", return_value=client), patch.object(
                buyer.Harness, "decide", authorize_offer):
            result = buyer.negotiate("http://TEST-ONLY.invalid", "lamp", 120, ledger=ledger,
                                     policy=TestPolicy(), trace=SilentTrace(), timeout=10)
        return result, ledger.snapshot(), client

    def test_receipt_in_any_no_deal_terminal_preserves_ambiguous_funds(self):
        for state in STATES:
            with self.subTest(state=state):
                result, ledger, client = self.trade(terminal(state, [{"parts": [{"data": RECEIPT}]}]))
                self.assertEqual(result["status"], "disputed")
                self.assertEqual(result["issues"], ["terminal_receipt_conflict"])
                self.assertEqual(result["state"], state)
                self.assertEqual(ledger["committed_total"], 0)
                self.assertEqual(ledger["reserved_total"], 110)
                self.assertEqual(ledger["available_budget"], 10)
                self.assertEqual([record["state"] for record in ledger["purchases"].values()], ["disputed"])
                self.assertEqual(client.send.call_count, 2)

    def test_invalid_price_or_partial_named_receipt_is_still_purchase_evidence(self):
        artifacts = ([{"parts": [{"data": {"price": "invalid", "item": "lamp"}}]}],
                     [{"parts": [{"data": {"price": None}}]}],
                     [{"name": "receipt", "parts": [{"data": {"purchaseId": "partial"}}]}],
                     [{"name": "Receipt", "parts": []}])
        for evidence in artifacts:
            with self.subTest(evidence=evidence):
                result, ledger, _ = self.trade(terminal("TASK_STATE_FAILED", evidence))
                self.assertEqual(result["status"], "disputed")
                self.assertEqual(ledger["reserved_total"], 110)
                self.assertEqual(ledger["committed_total"], 0)

    def test_unsolicited_conflicting_receipt_does_not_authorize_a_purchase(self):
        result, ledger, client = self.trade(terminal("TASK_STATE_FAILED", [{"name": "receipt"}]),
                                             unsolicited=True)
        self.assertEqual(result["status"], "disputed")
        self.assertEqual(ledger["committed_total"], 0)
        self.assertEqual(ledger["reserved_total"], 0)
        self.assertEqual(ledger["purchases"], {})
        self.assertEqual(client.send.call_count, 1)

    def test_plain_terminal_failure_and_diagnostic_artifacts_release_unspent_offer(self):
        for state in STATES:
            for artifacts in ([], [{}], [{"name": "diagnostic", "parts": [{"data": {
                    "code": "declined", "message": "TEST ONLY no purchase"}}]}]):
                with self.subTest(state=state, artifacts=artifacts):
                    result, ledger, _ = self.trade(terminal(state, artifacts))
                    self.assertEqual((result["status"], result["state"], result["offer_count"]), ("no_deal", state, 1))
                    self.assertEqual(ledger["reserved_total"], 0)
                    self.assertEqual(ledger["committed_total"], 0)
                    self.assertEqual(ledger["available_budget"], 120)



if __name__ == "__main__":
    unittest.main()
