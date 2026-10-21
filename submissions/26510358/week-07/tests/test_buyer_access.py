"""TEST ONLY identities: access rejection must precede model work and receipts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2a.helpers import new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Role, SendMessageRequest, TaskState
from buyer_access import BuyerAccess
import seller
from test_seller_cancel import ITEM, RecordingQueue, RecordingTrace, StubPolicy
from test_seller_cancel_http import actual_sdk_server, rpc, send_params, result_task


class BuyerAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test-only-access.json"
        self.path.write_text(json.dumps({"version": "TEST_ONLY", "blocked_buyers": [
            {"registry_name": "TEST_ONLY_BLOCKED"}]}))
        self.access = BuyerAccess(self.path)

    def test_declared_identity_is_exact_and_not_marked_authenticated(self):
        for metadata in ({"buyerName": "test_only_blocked"}, {"buyer_name": "TEST_ONLY_BLOCKED"},
                         {"buyer": "TEST_ONLY_BLOCKED"}, {"buyer": {"name": "TEST_ONLY_BLOCKED"}}):
            with self.subTest(metadata=metadata):
                match = self.access.match(message_metadata=metadata)
                self.assertEqual(match["buyer"], "TEST_ONLY_BLOCKED")
                self.assertFalse(match["authenticated"])
        self.assertIsNotNone(self.access.match(request_metadata={"buyerName": "TEST_ONLY_BLOCKED"}))

    def test_no_name_does_not_prove_identity_and_unrelated_fields_do_not_block(self):
        for metadata in (None, {}, {"buyer": []}, {"buyer": 1}, {"buyer": {"name": []}},
                         {"buyerName": "TEST_ONLY_BLOCKED_else"},
                         {"review": "TEST_ONLY_BLOCKED"}, {"owner": "TEST_ONLY_BLOCKED"}):
            with self.subTest(metadata=metadata):
                self.assertIsNone(self.access.match(message_metadata=metadata))

    def test_verified_principal_overrides_spoofed_metadata(self):
        peer = SimpleNamespace(is_authenticated=True, user_name="TEST_ONLY_ALLOWED")
        self.assertIsNone(self.access.match(user=peer, message_metadata={"buyerName": "TEST_ONLY_BLOCKED"}))
        peer.user_name = "TEST_ONLY_BLOCKED"
        self.assertTrue(self.access.match(user=peer)["authenticated"])

    async def test_rejection_has_no_model_price_lookup_or_receipt_even_on_replay(self):
        with patch.object(seller, "Trace", RecordingTrace), \
                patch.object(seller, "load_catalog", return_value=[deepcopy(ITEM)]), \
                patch.object(seller, "StrategyPolicy", StubPolicy):
            shop = seller.Seller({"name": "TEST ONLY access shop", "buyer_access": str(self.path),
                "price_policy": str(Path(__file__).with_name("price-policy-test-only.json"))})
        shop.harness.decide = Mock(side_effect=AssertionError("TEST ONLY model must not run"))
        shop.rank_pricing.refresh = Mock(side_effect=AssertionError("TEST ONLY rank lookup must not run"))
        context = RequestContext(call_context=ServerCallContext(), task_id="TEST_ONLY_ACCESS_TASK",
            context_id="TEST_ONLY_ACCESS_CONTEXT", request=SendMessageRequest(message=Message(
                role=Role.ROLE_USER, message_id="TEST_ONLY_MESSAGE", parts=[new_text_part("lamp")],
                metadata={"buyerName": "TEST_ONLY_BLOCKED"})))
        queue = RecordingQueue()
        await shop.execute(context, queue)
        await shop.execute(context, queue)
        statuses = [e.status.state for e in queue.events if hasattr(e, "status")]
        self.assertIn(TaskState.TASK_STATE_REJECTED, statuses)
        self.assertFalse(any(getattr(e, "artifact", None) for e in queue.events))
        self.assertIsNone(shop.signing_key)
        self.assertEqual(shop.open, {})
        shop.harness.decide.assert_not_called()
        shop.rank_pricing.refresh.assert_not_called()
        self.assertEqual(len([e for e in shop.trace.events if e["event"] == "buyer_access_denied"]), 1)

    def test_actual_http_rejects_declared_buyer_but_anonymous_can_request_quote(self):
        with patch.object(seller, "BuyerAccess", return_value=self.access), actual_sdk_server() as state:
            params = send_params("lamp")
            params["metadata"] = {"buyerName": "TEST_ONLY_BLOCKED"}
            denied = result_task(rpc(state, "SendMessage", params))
            self.assertEqual(denied["status"]["state"], "TASK_STATE_REJECTED")
            self.assertFalse(denied.get("artifacts"))
            self.assertEqual(state.model_contexts, [])
            # This is the explicit limitation: anonymity is not proof of an
            # allowed identity, but anonymous calls remain protocol-compatible.
            opened = result_task(rpc(state, "SendMessage", send_params("lamp")))
            self.assertEqual(opened["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertEqual(len(state.model_contexts), 1)
            params = send_params("100", task_id=opened["id"])
            params["message"]["metadata"] = {"buyer": {"name": "TEST_ONLY_BLOCKED"}}
            denied = result_task(rpc(state, "SendMessage", params))
            self.assertEqual(denied["status"]["state"], "TASK_STATE_REJECTED")
            self.assertFalse(denied.get("artifacts"))
            self.assertEqual(len(state.model_contexts), 1)


if __name__ == "__main__":
    unittest.main()
