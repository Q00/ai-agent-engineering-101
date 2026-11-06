"""Offline checks of model-driven task transitions and cancellation."""
import asyncio
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from a2a.helpers import new_text_part
from a2a.types import Message
from starlette.testclient import TestClient

from seller_agent import DEFAULT, Seller, build


def send(client, text, task_id=None):
    message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    body = client.post("/", headers={"A2A-Version": "1.0"}, json={
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage", "params": {"message": message},
    }).json()
    return body


def task(body):
    return body["result"].get("task", body["result"])


class SellerDecisionTests(unittest.TestCase):
    def test_model_can_counter_a_valid_offer_then_accept_later(self):
        decisions = [
            {"action": "counter", "price": 121, "reason": "More room to bargain"},
            {"action": "accept", "price": 110, "reason": "Agreement"},
        ]
        with patch("seller_agent.opening_price", return_value=145), patch("seller_agent.seller_decision", side_effect=decisions) as decide:
            with TestClient(build(DEFAULT)) as client:
                first = task(send(client, "I want to buy a lamp"))
                tid = first["id"]
                second = task(send(client, "100", tid))
                self.assertEqual(second["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                self.assertEqual(second["status"]["message"]["parts"][0]["text"], "Counteroffer: 121.")
                third = task(send(client, "110", tid))
                self.assertEqual(third["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertEqual(third["artifacts"][0]["parts"][0]["data"]["price"], 110)
                self.assertEqual(
                    third["artifacts"][0]["parts"][0]["data"],
                    {"seller": DEFAULT["name"], "item": "lamp", "price": 110},
                )
                completed = third["status"]["message"]["parts"][0]["text"]
                self.assertIn("we will issue a coupon", completed)
                self.assertTrue(completed.endswith("Deal: lamp at 110."))
                self.assertIn("error", send(client, "120", tid))
        self.assertEqual(decide.call_count, 2)
        self.assertEqual(len(decide.call_args.kwargs["history"]), 5)

    def test_model_rejection_ends_negotiation(self):
        with patch("seller_agent.opening_price", return_value=350), patch("seller_agent.seller_decision", return_value={"action": "reject", "reason": "Cannot agree"}):
            with TestClient(build(DEFAULT)) as client:
                first = task(send(client, "I want to buy a bookshelf"))
                last = task(send(client, "230", first["id"]))
                self.assertEqual(last["status"]["state"], "TASK_STATE_REJECTED")
                self.assertFalse(last.get("artifacts"))


class SellerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_inference_cannot_reopen_task(self):
        seller = Seller(DEFAULT)
        entered, release = threading.Event(), threading.Event()
        def slow_quote(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test worker was not released")
            return 145
        task_object = SimpleNamespace(id="cancel-test", context_id="context-test")
        context = SimpleNamespace(current_task=task_object, message=Message(parts=[new_text_part("I want a lamp")]))
        execute_up = Mock(requires_input=AsyncMock(), failed=AsyncMock())
        cancel_up = Mock(cancel=AsyncMock())
        with patch("seller_agent.opening_price", side_effect=slow_quote), patch("seller_agent.TaskUpdater", side_effect=[execute_up, cancel_up]):
            running = asyncio.create_task(seller.execute(context, object()))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                await seller.cancel(context, object())
            finally:
                release.set()
            await asyncio.wait_for(running, 3)
        cancel_up.cancel.assert_awaited_once()
        execute_up.requires_input.assert_not_awaited()
        self.assertNotIn(task_object.id, seller.open)
        self.assertNotIn(task_object.id, seller.pending)


if __name__ == "__main__":
    unittest.main()
