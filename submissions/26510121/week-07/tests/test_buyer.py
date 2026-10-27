"""Buyer protocol, receipt and lost-response checks, with no live API calls."""
import json
import unittest

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError, asking_price, integer_money, receipt
from negotiation.buying import negotiate
from negotiation.contracts import BuyerDecision


class MemoryLog:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})


def task(state="TASK_STATE_INPUT_REQUIRED", price=100.0, item="lamp", task_id="task-one"):
    result = {"id": task_id, "contextId": "context-one", "status": {
        "state": state, "message": {"parts": [{"text": "Counteroffer: 105"}]}}}
    if state == "TASK_STATE_COMPLETED":
        result["artifacts"] = [{"parts": [{"data": {"item": item, "price": price}}]}]
    return result


class ReceiptTests(unittest.TestCase):
    def test_sdk_float_price_is_normalized(self):
        self.assertEqual(receipt(task("TASK_STATE_COMPLETED"), "lamp", 120, 100)["price"], 100)

    def test_bool_string_fraction_nan_infinity_are_not_money(self):
        for value in (True, "100", 100.5, float("nan"), float("inf"), -1, 0):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                integer_money(value)

    def test_receipt_must_match_item_budget_and_agreed_price(self):
        cases = [("chair", 100, "receipt_item_mismatch"),
                 ("lamp", 121, "receipt_above_budget"), ("lamp", 101, "receipt_price_mismatch")]
        for item, price, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ProtocolError, code):
                receipt(task("TASK_STATE_COMPLETED", price, item), "lamp", 120, 100)

    def test_missing_or_duplicate_receipt_is_not_success(self):
        value = task("TASK_STATE_COMPLETED")
        value["artifacts"] *= 2
        with self.assertRaisesRegex(ProtocolError, "one_receipt_required"):
            receipt(value, "lamp", 120, 100)
        with self.assertRaisesRegex(ProtocolError, "receipt_requires_completed_task"):
            receipt(task(), "lamp", 120, 100)

    def test_baseline_ask_parser_preserves_negative_sign(self):
        self.assertEqual(asking_price(task()), 105)
        value = task()
        value["status"]["message"]["parts"][0]["text"] = "Counteroffer: -105"
        with self.assertRaisesRegex(ProtocolError, "invalid_numeric_price"):
            asking_price(value)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_requests_have_version_header_and_followups_have_task_id(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"name": "Test Shop", "skills": [{"id": "negotiate"}],
                    "supportedInterfaces": [{"protocolBinding": "HTTP+JSON", "protocolVersion": "1.0", "url": "/rest"},
                                            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "/rpc"}]})
            body = json.loads(request.content)
            return httpx.Response(200, json={"result": {"task": task()} if body["method"] == "SendMessage" else task()})
        client = A2AClient(MemoryLog(), httpx.MockTransport(handler))
        try:
            await client.discover("http://shop.test")
            await client.send("I want to buy a lamp")
            await client.send("100", "task-one")
            await client.get_task("task-one")
            await client.cancel("task-one")
        finally:
            await client.close()
        self.assertTrue(all(r.headers.get("A2A-Version") == "1.0" for r in requests))
        self.assertTrue(all(r.url.path == "/rpc" for r in requests[1:]))
        followup = json.loads(requests[2].content)
        self.assertEqual(followup["params"]["message"]["taskId"], "task-one")

    async def test_missing_supported_interface_is_rejected_before_send(self):
        client = A2AClient(MemoryLog(), httpx.MockTransport(lambda r: httpx.Response(200, json={
            "name": "Old Shop", "skills": [{"id": "sell"}], "supportedInterfaces": []})))
        try:
            with self.assertRaisesRegex(ProtocolError, "jsonrpc_v1_interface_missing"):
                await client.discover("http://shop.test")
        finally:
            await client.close()


class FakeHarness:
    def __init__(self, action="offer"):
        self.action = action

    async def buyer(self, context, observation):
        return BuyerDecision(action=self.action, item_id=context.item_id,
                             price=100 if self.action == "offer" else None,
                             reason="Simulated decision.", summary="Simulated memory.")


class FakeClient:
    def __init__(self, lost=False, recovered=None):
        self.sent = []
        self.canceled = []
        self.lookups = []
        self.lost = lost
        self.recovered = recovered or task("TASK_STATE_COMPLETED")

    async def discover(self, url):
        return {"name": "Simulated Shop", "skills": [{"id": "negotiate"}]}

    async def send(self, text, task_id=None):
        self.sent.append((text, task_id))
        if task_id is None:
            return task()
        if self.lost:
            raise httpx.ReadTimeout("Simulated lost offer reply")
        return task("TASK_STATE_COMPLETED")

    async def get_task(self, task_id):
        self.lookups.append(task_id)
        return self.recovered

    async def cancel(self, task_id):
        self.canceled.append(task_id)
        return task("TASK_STATE_CANCELED")


class BuyerLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_purchase_stops_after_one_valid_receipt(self):
        client = FakeClient()
        result = await negotiate(client, FakeHarness(), "http://shop.test", "lamp", 120, MemoryLog())
        self.assertEqual(result["outcome"], "purchased")
        self.assertEqual(client.sent, [("I want to buy a lamp", None), ("100", "task-one")])

    async def test_model_walkaway_cancels_without_sending_a_price(self):
        client = FakeClient()
        result = await negotiate(client, FakeHarness("walk_away"), "http://shop.test", "lamp", 120, MemoryLog())
        self.assertEqual(result["outcome"], "stopped")
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.canceled, ["task-one"])

    async def test_lost_offer_response_uses_gettask_instead_of_repeating_offer(self):
        client = FakeClient(lost=True)
        result = await negotiate(client, FakeHarness(), "http://shop.test", "lamp", 120, MemoryLog())
        self.assertEqual(result["outcome"], "purchased")
        self.assertEqual(len(client.sent), 2)
        self.assertEqual(client.lookups, ["task-one"])

    async def test_unresolved_lost_offer_is_not_reported_as_purchase_or_retried(self):
        client = FakeClient(lost=True, recovered=task())
        with self.assertRaisesRegex(ProtocolError, "offer_outcome_unknown"):
            await negotiate(client, FakeHarness(), "http://shop.test", "lamp", 120, MemoryLog())
        self.assertEqual(len(client.sent), 2)

    async def test_changed_task_id_prevents_further_purchase_actions(self):
        client = FakeClient(lost=True, recovered=task("TASK_STATE_COMPLETED", task_id="other-task"))
        with self.assertRaisesRegex(ProtocolError, "task_id_changed"):
            await negotiate(client, FakeHarness(), "http://shop.test", "lamp", 120, MemoryLog())


if __name__ == "__main__":
    unittest.main(verbosity=2)
